"""Repair a native Sketch PDF without changing the positions of visible glyphs."""

import json
import math
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, BooleanObject, ByteStringObject, ContentStream,
    DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
    NumberObject, TextStringObject,
)


def dictionary(**values):
    return DictionaryObject({NameObject('/' + key): value for key, value in values.items()})


def unicode_map(writer):
    lines = [
        '/CIDInit /ProcSet findresource begin', '12 dict begin', 'begincmap',
        '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def',
        '/CMapName /ResumeUnicode def', '/CMapType 2 def',
        '1 begincodespacerange', '<00> <FF>', 'endcodespacerange',
    ]
    for start in range(0, 256, 64):
        lines.append('64 beginbfchar')
        for code in range(start, start + 64):
            text = bytes([code]).decode('mac_roman').encode('utf-16-be').hex()
            lines.append(f'<{code:02x}> <{text}>')
        lines.append('endbfchar')
    lines += ['endcmap', 'CMapName currentdict /CMap defineresource pop', 'end', 'end']
    stream = DecodedStreamObject()
    stream.set_data('\n'.join(lines).encode())
    return writer._add_object(stream)


def decoded_text(operands, operator):
    values = operands[0] if operator == b'TJ' else operands
    return ''.join(value.original_bytes.decode('mac_roman') for value in values if isinstance(value, (ByteStringObject, TextStringObject)))


def normalized(text):
    return ' '.join(text.split())


def repair(source, output, metadata):
    reader = PdfReader(source)
    if len(reader.pages) != 1:
        raise ValueError('Expected one resume page.')
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    page = writer.pages[0]
    page_reference = page.indirect_reference
    cmap = unicode_map(writer)
    structure_root = dictionary(Type=NameObject('/StructTreeRoot'))
    root_reference = writer._add_object(structure_root)
    document = dictionary(Type=NameObject('/StructElem'), S=NameObject('/Document'), P=root_reference, K=ArrayObject())
    document_reference = writer._add_object(document)
    structure_root[NameObject('/K')] = document_reference
    parent_tree_entries = ArrayObject()
    visited = set()
    paragraph_count = 0

    def visit(resources):
        nonlocal paragraph_count
        resources = resources.get_object()
        for reference in resources.get('/Font', {}).values():
            font = reference.get_object()
            if font.get('/Encoding') != '/MacRomanEncoding':
                raise ValueError('The export uses a new font encoding. Repair needs review before overwriting the PDF.')
            font[NameObject('/ToUnicode')] = cmap

        for reference in resources.get('/XObject', {}).values():
            if reference.idnum in visited:
                continue
            visited.add(reference.idnum)
            form = reference.get_object()
            if '/Resources' not in form:
                continue
            inner = form['/Resources']
            fonts = inner.get('/Font', {})
            if fonts:
                # A real space glyph keeps adjacent fields separate. Its advance
                # is discarded by the next text block's absolute text matrix.
                space_font = next(name for name, font in fonts.items() if int(font.get_object()['/FirstChar']) <= 32 <= int(font.get_object()['/LastChar']))
                stream = ContentStream(form, writer)
                parent_index = len(parent_tree_entries) // 2
                parents = ArrayObject()
                parent_tree_entries.extend([NumberObject(parent_index), parents])
                form[NameObject('/StructParents')] = NumberObject(parent_index)
                operations = []
                paragraphs = {}
                position = None
                marked = False
                current_text = []
                current_properties = None

                for operands, operator in stream.operations:
                    if operator == b'cm':
                        position = (round(float(operands[4]), 3), round(792 - float(operands[5]), 3))
                    if operator == b'BT':
                        if position is None:
                            raise ValueError('Text position is unavailable.')
                        if position not in paragraphs:
                            matches = [item for item in metadata if abs(item['x'] - position[0]) < 0.1 and abs(item['y'] - position[1]) < 0.1]
                            if len(matches) != 1:
                                raise ValueError(f'Could not associate PDF text at {position} with one Sketch layer.')
                            text = matches[0]['text']
                            kind = '/H1' if text == 'NILS\nHAYAT' else '/H2' if text in ['EXPERIENCE', 'EDUCATION', 'SKILLS'] else '/P'
                            paragraph = dictionary(Type=NameObject('/StructElem'), S=NameObject(kind), P=document_reference, Pg=page_reference, K=ArrayObject(), ActualText=TextStringObject(text))
                            paragraph_reference = writer._add_object(paragraph)
                            document['/K'].append(paragraph_reference)
                            paragraphs[position] = (paragraph, paragraph_reference)
                            paragraph_count += 1
                        paragraph, paragraph_reference = paragraphs[position]
                        mcid = len(parents)
                        parents.append(paragraph_reference)
                        paragraph['/K'].append(dictionary(Type=NameObject('/MCR'), Pg=page_reference, Stm=reference, MCID=NumberObject(mcid)))
                        current_properties = dictionary(MCID=NumberObject(mcid))
                        operations.append(([NameObject('/Span'), current_properties], b'BDC'))
                        marked = True
                        current_text = []
                    if operator in [b'Tj', b'TJ']:
                        current_text.append(decoded_text(operands, operator))
                    if operator == b'TJ':
                        adjusted = ArrayObject()
                        for item in operands[0]:
                            if isinstance(item, (int, float)):
                                # Some extractors mistake a single large kerning
                                # adjustment for a word gap. Equivalent smaller
                                # adjustments preserve every glyph's position.
                                pieces = max(1, math.ceil(abs(float(item)) / 40))
                                adjusted.extend([FloatObject(float(item) / pieces)] * pieces)
                            else:
                                adjusted.append(item)
                        operands = [adjusted]
                    if operator == b'ET':
                        current_properties[NameObject('/ActualText')] = TextStringObject(''.join(current_text).rstrip() + ' ')
                        operations.extend([
                            ([NameObject(space_font), NumberObject(1)], b'Tf'),
                            ([ByteStringObject(b' ')], b'Tj'),
                        ])
                    operations.append((operands, operator))
                    if operator == b'ET' and marked:
                        operations.append(([], b'EMC'))
                        marked = False
                stream.operations = operations
                form.set_data(stream.get_data())
            visit(inner)

    visit(page['/Resources'])
    if paragraph_count != len(metadata):
        raise ValueError('The tagged paragraph count differs from the Sketch text layer count.')
    structure_root[NameObject('/ParentTree')] = writer._add_object(dictionary(Nums=parent_tree_entries))
    structure_root[NameObject('/ParentTreeNextKey')] = NumberObject(len(parent_tree_entries) // 2)
    writer._root_object[NameObject('/StructTreeRoot')] = root_reference
    writer._root_object[NameObject('/MarkInfo')] = dictionary(Marked=BooleanObject(True))
    writer._root_object[NameObject('/Lang')] = TextStringObject('en-US')
    page[NameObject('/Tabs')] = NameObject('/S')
    writer.add_metadata({'/Title': 'Nils Hayat Resume', '/Author': 'Nils Hayat', '/Subject': 'Software engineering experience'})

    output = Path(output)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.pdf', delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        writer.write(temporary_path)
        extracted = PdfReader(temporary_path).pages[0].extract_text()
        for item in metadata:
            if normalized(item['text']) not in normalized(extracted):
                raise ValueError(f'PDF text does not match Sketch: {item["text"][:60]}')
        with pdfplumber.open(temporary_path) as check:
            spatial_text = check.pages[0].extract_text()
        words = lambda text: Counter(re.findall(r'\w+', text.casefold()))
        expected = words(' '.join(item['text'] for item in metadata))
        if expected - words(spatial_text):
            raise ValueError('A second text reader could not recover all visible words.')
        os.replace(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f'Exported {output.name}: {paragraph_count} text layers tagged; both text readers passed.')


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('Usage: repair_pdf.py native-export.pdf output.pdf text-layers.json')
    repair(sys.argv[1], sys.argv[2], json.loads(Path(sys.argv[3]).read_text()))
