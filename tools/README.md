# Resume PDF export

Edit the resume in Sketch. Choose **Plugins > Resume PDF Export > Export Resume PDF** to replace the PDF beside the Sketch document.

The command uses Sketch's native PDF renderer, preserves the design and fonts, and repairs the exported character mappings and word boundaries. It adds paragraph tags, logical reading order, and matching ActualText. Both text readers must recover the visible text before the existing PDF is replaced.

The Sketch document is the only editable resume source. The export contains the visible resume content; no additional keywords or separate resume text are inserted.

The plugin uses the bundled Codex Python runtime on this Mac. To reinstall, open `Resume PDF Export.sketchplugin` in Sketch.
