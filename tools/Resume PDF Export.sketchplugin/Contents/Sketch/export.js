var sketch = require('sketch')
var UI = require('sketch/ui')

function onRun(context) {
  var document = sketch.getSelectedDocument()
  if (!document || !document.sketchObject.fileURL()) {
    UI.alert('Save the resume first', 'Save the Sketch document before exporting its PDF.')
    return
  }

  var frames = document.selectedPage.layers.filter(function (layer) {
    return layer.name === 'Resume Nils Hayat' &&
      Math.abs(layer.frame.width - 612) < 1 &&
      Math.abs(layer.frame.height - 792) < 1
  })
  if (frames.length !== 1) {
    UI.alert('Resume frame not found', 'Open the page containing the Letter-sized Resume Nils Hayat frame.')
    return
  }

  var fileManager = NSFileManager.defaultManager()
  var sourcePath = String(document.sketchObject.fileURL().path())
  var outputPath = sourcePath.replace(/\.sketch$/i, '.pdf')
  if (outputPath === sourcePath) {
    UI.alert('Sketch document required', 'Save this document with the .sketch extension before exporting.')
    return
  }

  var pythonPath = String(NSString.stringWithString(
    '~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
  ).stringByExpandingTildeInPath())
  if (!fileManager.isExecutableFileAtPath(pythonPath)) {
    UI.alert('PDF repair runtime unavailable', 'The bundled Codex Python runtime is needed to repair the export.')
    return
  }

  var temporaryPath = String(NSTemporaryDirectory()) + 'resume-export-' + String(NSUUID.UUID().UUIDString())
  fileManager.createDirectoryAtPath_withIntermediateDirectories_attributes_error(temporaryPath, true, null, null)
  try {
    var frame = frames[0]
    sketch.export(frame, {
      formats: 'pdf',
      output: temporaryPath,
      overwriting: true,
      'use-id-for-name': true
    })

    var metadata = frame.layers.filter(function (layer) {
      return layer.type === 'Text'
    }).map(function (layer) {
      return {
        text: layer.text,
        x: layer.frame.x,
        y: layer.frame.y
      }
    })
    var metadataPath = temporaryPath + '/text.json'
    NSString.stringWithString(JSON.stringify(metadata)).writeToFile_atomically_encoding_error(
      metadataPath, true, NSUTF8StringEncoding, null
    )

    var scriptPath = String(context.plugin.url()
      .URLByAppendingPathComponent('Contents/Resources/repair_pdf.py').path())
    var task = NSTask.alloc().init()
    var pipe = NSPipe.pipe()
    task.setLaunchPath(pythonPath)
    task.setArguments([scriptPath, temporaryPath + '/' + frame.id + '.pdf', outputPath, metadataPath])
    task.setStandardOutput(pipe)
    task.setStandardError(pipe)
    task.launch()
    task.waitUntilExit()
    var result = String(NSString.alloc().initWithData_encoding(
      pipe.fileHandleForReading().readDataToEndOfFile(), NSUTF8StringEncoding
    ))
    if (task.terminationStatus() !== 0) {
      throw new Error(result || 'The PDF repair did not complete.')
    }
    UI.message('Exported ' + outputPath.split('/').pop() + ' with readable text and paragraph tags')
  } catch (error) {
    UI.alert('Resume export failed', String(error.message || error))
  } finally {
    fileManager.removeItemAtPath_error(temporaryPath, null)
  }
}

exports.onRun = onRun
