// Apple native Hebrew speech-to-text via SFSpeechRecognizer.
// Usage: open -W Transcribe.app --args <audio.wav> <locale> <output-file>

import Foundation
import Speech
import AppKit

guard CommandLine.arguments.count >= 4 else { exit(1) }

let audioPath = CommandLine.arguments[1]
let lang = CommandLine.arguments[2]
let outPath = CommandLine.arguments[3]
let audioURL = URL(fileURLWithPath: audioPath)

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        if SFSpeechRecognizer.authorizationStatus() == .authorized {
            recognize()
        } else {
            NSApp.activate(ignoringOtherApps: true)
            SFSpeechRecognizer.requestAuthorization { status in
                if status == .authorized { self.recognize() }
                else { self.finish("") }
            }
        }
    }

    func recognize() {
        let locale = Locale(identifier: lang)
        guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.isAvailable else {
            return finish("")
        }
        let request = SFSpeechURLRecognitionRequest(url: audioURL)
        request.shouldReportPartialResults = false
        if recognizer.supportsOnDeviceRecognition {
            request.requiresOnDeviceRecognition = true
        }
        recognizer.recognitionTask(with: request) { result, error in
            if error != nil { return self.finish("") }
            if let result = result, result.isFinal {
                self.finish(result.bestTranscription.formattedString)
            }
        }
    }

    func finish(_ text: String) {
        try? text.write(toFile: outPath, atomically: true, encoding: .utf8)
        NSApplication.shared.terminate(nil)
    }
}

let delegate = AppDelegate()
app.delegate = delegate
app.run()
