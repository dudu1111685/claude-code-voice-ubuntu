// Voice server for Claude Code (macOS).
// Native languages → proxy to Anthropic's server.
// Unsupported languages (Hebrew, etc.) → Apple SFSpeechRecognizer on-device.

import Foundation
import Network
import Speech
import AppKit

let PORT: UInt16 = 19876
let ANTHROPIC_WS = "wss://api.anthropic.com/api/ws/speech_to_text/voice_stream"
let NATIVE_LANGS: Set<String> = ["en","es","fr","ja","de","pt","it","ko","hi","id","ru","pl","tr","nl","uk","el","cs","da","sv","no"]

// MARK: - Language

let localeMap: [String: String] = [
    "he": "he-IL", "hebrew": "he-IL", "עברית": "he-IL",
    "en": "en-US", "english": "en-US",
    "es": "es-ES", "spanish": "es-ES", "español": "es-ES",
    "fr": "fr-FR", "french": "fr-FR", "français": "fr-FR",
    "de": "de-DE", "german": "de-DE", "deutsch": "de-DE",
    "ja": "ja-JP", "japanese": "ja-JP", "日本語": "ja-JP",
    "ko": "ko-KR", "korean": "ko-KR", "한국어": "ko-KR",
    "pt": "pt-BR", "portuguese": "pt-BR", "português": "pt-BR",
    "it": "it-IT", "italian": "it-IT", "italiano": "it-IT",
    "ru": "ru-RU", "russian": "ru-RU", "русский": "ru-RU",
    "zh": "zh-CN", "chinese": "zh-CN",
    "ar": "ar-SA", "arabic": "ar-SA",
    "hi": "hi-IN", "hindi": "hi-IN",
    "id": "id-ID", "indonesian": "id-ID",
    "tr": "tr-TR", "turkish": "tr-TR",
    "nl": "nl-NL", "dutch": "nl-NL",
    "pl": "pl-PL", "polish": "pl-PL",
    "uk": "uk-UA", "ukrainian": "uk-UA",
    "el": "el-GR", "greek": "el-GR",
    "cs": "cs-CZ", "czech": "cs-CZ",
    "da": "da-DK", "danish": "da-DK",
    "sv": "sv-SE", "swedish": "sv-SE",
    "no": "nb-NO", "norwegian": "nb-NO",
]

func readLanguage() -> String {
    let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".claude/settings.json")
    guard let data = try? Data(contentsOf: path),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let lang = json["language"] as? String else { return "en" }
    return lang.lowercased().trimmingCharacters(in: .whitespaces)
}

func langCode(_ raw: String) -> String {
    if NATIVE_LANGS.contains(raw) { return raw }
    if let mapped = localeMap[raw] { return String(mapped.prefix(2)) }
    return raw
}

func appleLocale(_ raw: String) -> String {
    return localeMap[raw] ?? localeMap[langCode(raw)] ?? "en-US"
}

func isNativeLanguage(_ raw: String) -> Bool {
    return NATIVE_LANGS.contains(langCode(raw))
}

// MARK: - OAuth token

func readOAuthToken() -> String? {
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: "/usr/bin/security")
    proc.arguments = ["find-generic-password", "-s", "Claude Code-credentials", "-w"]
    let pipe = Pipe()
    proc.standardOutput = pipe
    proc.standardError = FileHandle.nullDevice
    try? proc.run()
    proc.waitUntilExit()
    guard proc.terminationStatus == 0 else { return nil }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    guard let str = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
          let json = try? JSONSerialization.jsonObject(with: Data(str.utf8)) as? [String: Any],
          let oauth = json["claudeAiOauth"] as? [String: Any],
          let token = oauth["accessToken"] as? String else { return nil }
    return token
}

// MARK: - WAV + Apple STT

func createWav(_ pcm: Data) -> Data {
    var w = Data(count: 44)
    func u32(_ o: Int, _ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { w.replaceSubrange(o..<o+4, with: $0) } }
    func u16(_ o: Int, _ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { w.replaceSubrange(o..<o+2, with: $0) } }
    w.replaceSubrange(0..<4, with: "RIFF".data(using: .ascii)!)
    u32(4, UInt32(36 + pcm.count))
    w.replaceSubrange(8..<12, with: "WAVE".data(using: .ascii)!)
    w.replaceSubrange(12..<16, with: "fmt ".data(using: .ascii)!)
    u32(16, 16); u16(20, 1); u16(22, 1)
    u32(24, 16000); u32(28, 32000); u16(32, 2); u16(34, 16)
    w.replaceSubrange(36..<40, with: "data".data(using: .ascii)!)
    u32(40, UInt32(pcm.count))
    w.append(pcm)
    return w
}

func transcribeApple(_ pcm: Data, locale: String, completion: @escaping (String) -> Void) {
    guard !pcm.isEmpty else { return completion("") }
    let dur = String(format: "%.1f", Double(pcm.count) / 32000.0)
    print("[voice] \(dur)s → Apple STT (\(locale))")

    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("hv-\(ProcessInfo.processInfo.globallyUniqueString).wav")
    try? createWav(pcm).write(to: tmp)

    guard let rec = SFSpeechRecognizer(locale: Locale(identifier: locale)), rec.isAvailable else {
        try? FileManager.default.removeItem(at: tmp)
        return completion("")
    }
    let req = SFSpeechURLRecognitionRequest(url: tmp)
    req.shouldReportPartialResults = false
    if rec.supportsOnDeviceRecognition {
        req.requiresOnDeviceRecognition = true
        print("[voice] On-device")
    }
    rec.recognitionTask(with: req) { result, _ in
        try? FileManager.default.removeItem(at: tmp)
        let text = result?.isFinal == true ? result!.bestTranscription.formattedString : ""
        if !text.isEmpty { print("[voice] \"\(text)\"") }
        completion(text)
    }
}

// MARK: - ivrit.ai STT (Hebrew — cloud via RunPod or local via faster-whisper)

enum IvritEngine {
    case local(device: String, computeType: String, model: String)
    case runpod(apiKey: String, endpointId: String, model: String)
}

func readIvritConfig() -> IvritEngine? {
    let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".claude/settings.json")
    guard let data = try? Data(contentsOf: path),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let cfg = json["ivritAi"] as? [String: Any] else { return nil }

    let engine = (cfg["engine"] as? String ?? "runpod").lowercased()

    if engine == "local" {
        let device = (cfg["device"] as? String) ?? "cpu"
        let computeType = (cfg["computeType"] as? String) ?? (device.contains("cuda") || device == "mps" ? "float16" : "float32")
        let model = (cfg["model"] as? String) ?? "ivrit-ai/faster-whisper-v2-d4"
        return .local(device: device, computeType: computeType, model: model)
    }

    // runpod
    guard let apiKey = cfg["apiKey"] as? String, !apiKey.isEmpty,
          let endpointId = cfg["endpointId"] as? String, !endpointId.isEmpty else { return nil }
    let model = (cfg["model"] as? String) ?? "ivrit-ai/whisper-large-v3-turbo-ct2"
    return .runpod(apiKey: apiKey, endpointId: endpointId, model: model)
}

func isHebrew(_ raw: String) -> Bool {
    return langCode(raw) == "he"
}

func ivritEngineLabel(_ engine: IvritEngine) -> String {
    switch engine {
    case .local(let device, _, _): return "local/\(device)"
    case .runpod: return "RunPod"
    }
}

func transcribeIvrit(_ pcm: Data, completion: @escaping (String) -> Void) {
    guard !pcm.isEmpty else { return completion("") }
    let dur = String(format: "%.1f", Double(pcm.count) / 32000.0)

    guard let engine = readIvritConfig() else {
        print("[voice] ivrit.ai not configured — falling back to Apple STT")
        transcribeApple(pcm, locale: "he-IL", completion: completion)
        return
    }

    print("[voice] \(dur)s → ivrit.ai STT (\(ivritEngineLabel(engine)))")

    switch engine {
    case .local(let device, let computeType, let model):
        transcribeIvritLocal(pcm, device: device, computeType: computeType, model: model, completion: completion)
    case .runpod(let apiKey, let endpointId, let model):
        transcribeIvritRunpod(pcm, apiKey: apiKey, endpointId: endpointId, model: model, completion: completion)
    }
}

func transcribeIvritLocal(_ pcm: Data, device: String, computeType: String, model: String, completion: @escaping (String) -> Void) {
    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("ivrit-\(ProcessInfo.processInfo.globallyUniqueString).wav")
    try? createWav(pcm).write(to: tmp)

    // Find the helper script next to this binary
    let binaryPath = CommandLine.arguments[0]
    let binaryDir = (binaryPath as NSString).deletingLastPathComponent
    // The helper is at ../../scripts/transcribe_ivrit_local.py relative to the binary inside .app
    let scriptCandidates = [
        (binaryDir as NSString).appendingPathComponent("../../../scripts/transcribe_ivrit_local.py"),
        (binaryDir as NSString).appendingPathComponent("../../transcribe_ivrit_local.py"),
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/share/claude-code-voice/scripts/transcribe_ivrit_local.py").path,
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/share/claude-code-voice/transcribe_ivrit_local.py").path,
    ]

    var scriptPath: String?
    for candidate in scriptCandidates {
        let resolved = (candidate as NSString).standardizingPath
        if FileManager.default.fileExists(atPath: resolved) {
            scriptPath = resolved
            break
        }
    }

    guard let script = scriptPath else {
        print("[voice] transcribe_ivrit_local.py not found — falling back to Apple STT")
        try? FileManager.default.removeItem(at: tmp)
        transcribeApple(pcm, locale: "he-IL", completion: completion)
        return
    }

    DispatchQueue.global(qos: .userInitiated).async {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        proc.arguments = [script, tmp.path, device, computeType, model]
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice

        do {
            try proc.run()
            proc.waitUntilExit()
        } catch {
            print("[voice] ivrit.ai local error: \(error) — falling back to Apple STT")
            try? FileManager.default.removeItem(at: tmp)
            DispatchQueue.main.async { transcribeApple(pcm, locale: "he-IL", completion: completion) }
            return
        }

        try? FileManager.default.removeItem(at: tmp)

        let outData = pipe.fileHandleForReading.readDataToEndOfFile()
        let text = (String(data: outData, encoding: .utf8) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)

        if proc.terminationStatus != 0 || text.hasPrefix("ERROR:") {
            print("[voice] ivrit.ai local failed — falling back to Apple STT")
            DispatchQueue.main.async { transcribeApple(pcm, locale: "he-IL", completion: completion) }
            return
        }

        if !text.isEmpty { print("[voice] \"\(text)\"") }
        DispatchQueue.main.async { completion(text) }
    }
}

func transcribeIvritRunpod(_ pcm: Data, apiKey: String, endpointId: String, model: String, completion: @escaping (String) -> Void) {
    let wav = createWav(pcm)
    let audioB64 = wav.base64EncodedString()

    let url = URL(string: "https://api.runpod.ai/v2/\(endpointId)/runsync")!
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.timeoutInterval = 60

    let body: [String: Any] = [
        "input": [
            "type": "blob",
            "data": audioB64,
            "language": "he",
            "model": model
        ]
    ]
    request.httpBody = try? JSONSerialization.data(withJSONObject: body)

    URLSession.shared.dataTask(with: request) { data, response, error in
        if let error = error {
            print("[voice] ivrit.ai RunPod error: \(error.localizedDescription) — falling back to Apple STT")
            transcribeApple(pcm, locale: "he-IL", completion: completion)
            return
        }
        guard let data = data,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            print("[voice] ivrit.ai RunPod: invalid response — falling back to Apple STT")
            transcribeApple(pcm, locale: "he-IL", completion: completion)
            return
        }

        // RunPod response: {"output": {"text": "..."}} or {"output": {"segments": [...]}}
        var text = ""
        if let output = json["output"] as? [String: Any] {
            if let t = output["text"] as? String {
                text = t.trimmingCharacters(in: .whitespacesAndNewlines)
            } else if let segments = output["segments"] as? [[String: Any]] {
                text = segments.compactMap { $0["text"] as? String }.joined(separator: " ")
                    .trimmingCharacters(in: .whitespacesAndNewlines)
            }
        }

        if !text.isEmpty { print("[voice] \"\(text)\"") }
        completion(text)
    }.resume()
}

// MARK: - WebSocket helpers

func sendJSON(_ conn: NWConnection, _ dict: [String: String]) {
    guard let data = try? JSONSerialization.data(withJSONObject: dict) else { return }
    let meta = NWProtocolWebSocket.Metadata(opcode: .text)
    let ctx = NWConnection.ContentContext(identifier: "ws", metadata: [meta])
    conn.send(content: data, contentContext: ctx, isComplete: true, completion: .idempotent)
}

// MARK: - Proxy session (native languages → Anthropic)

class ProxySession {
    let conn: NWConnection
    var upstream: URLSessionWebSocketTask?

    init(_ conn: NWConnection, lang: String, token: String) {
        self.conn = conn
        let params = "encoding=linear16&sample_rate=16000&channels=1&endpointing_ms=300&utterance_end_ms=1000&language=\(lang)"
        let url = URL(string: "\(ANTHROPIC_WS)?\(params)")!
        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("cli", forHTTPHeaderField: "x-app")
        upstream = URLSession.shared.webSocketTask(with: request)
        upstream?.resume()
        receiveFromUpstream()
        receiveFromClient()
        print("[voice] Proxying to Anthropic (\(lang))")
    }

    // Client → Anthropic
    func receiveFromClient() {
        conn.receiveMessage { [weak self] data, ctx, _, error in
            guard let self = self, let data = data, error == nil else { return }
            let meta = ctx?.protocolMetadata(definition: NWProtocolWebSocket.definition) as? NWProtocolWebSocket.Metadata
            switch meta?.opcode {
            case .text:
                self.upstream?.send(.string(String(data: data, encoding: .utf8) ?? "")) { _ in }
            case .binary:
                self.upstream?.send(.data(data)) { _ in }
            default: break
            }
            self.receiveFromClient()
        }
    }

    // Anthropic → Client
    func receiveFromUpstream() {
        upstream?.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let msg):
                switch msg {
                case .string(let text):
                    let meta = NWProtocolWebSocket.Metadata(opcode: .text)
                    let ctx = NWConnection.ContentContext(identifier: "ws", metadata: [meta])
                    self.conn.send(content: text.data(using: .utf8), contentContext: ctx, isComplete: true, completion: .idempotent)
                case .data(let data):
                    let meta = NWProtocolWebSocket.Metadata(opcode: .binary)
                    let ctx = NWConnection.ContentContext(identifier: "ws", metadata: [meta])
                    self.conn.send(content: data, contentContext: ctx, isComplete: true, completion: .idempotent)
                @unknown default: break
                }
                self.receiveFromUpstream()
            case .failure:
                break
            }
        }
    }
}

// MARK: - Local session (unsupported languages → Apple STT)

class LocalSession {
    var chunks: [Data] = []
    var closed = false
    let locale: String
    let useIvrit: Bool

    init(locale: String, useIvrit: Bool = false) {
        self.locale = locale
        self.useIvrit = useIvrit
    }

    func receive(_ conn: NWConnection) {
        conn.receiveMessage { [self] data, ctx, _, error in
            guard let data = data, error == nil else { return }
            let meta = ctx?.protocolMetadata(definition: NWProtocolWebSocket.definition) as? NWProtocolWebSocket.Metadata
            switch meta?.opcode {
            case .binary: if !closed { chunks.append(data) }
            case .text: handleText(conn, data)
            default: break
            }
            receive(conn)
        }
    }

    func handleText(_ conn: NWConnection, _ data: Data) {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else { return }
        if type == "KeepAlive" { return }
        if type == "CloseStream" && !closed {
            closed = true
            sendJSON(conn, ["type": "TranscriptText", "data": ""])
            let pcm = chunks.reduce(Data()) { $0 + $1 }
            chunks = []
            let transcriber: (Data, @escaping (String) -> Void) -> Void = useIvrit
                ? { data, cb in transcribeIvrit(data, completion: cb) }
                : { data, cb in transcribeApple(data, locale: self.locale, completion: cb) }
            transcriber(pcm) { text in
                if !text.isEmpty { sendJSON(conn, ["type": "TranscriptText", "data": text]) }
                sendJSON(conn, ["type": "TranscriptEndpoint"])
            }
        }
    }
}

// MARK: - Server

var activeSessions: [ObjectIdentifier: AnyObject] = [:]

func startServer() {
    let params = NWParameters.tcp
    let ws = NWProtocolWebSocket.Options()
    params.defaultProtocolStack.applicationProtocols.insert(ws, at: 0)

    guard let listener = try? NWListener(using: params, on: NWEndpoint.Port(rawValue: PORT)!) else {
        print("[voice] Failed to start on port \(PORT)")
        return
    }
    listener.newConnectionHandler = { conn in
        conn.start(queue: .main)
        let raw = readLanguage()
        let code = langCode(raw)

        if isNativeLanguage(raw), let token = readOAuthToken() {
            print("[voice] Connected (\(code) → Anthropic)")
            let session = ProxySession(conn, lang: code, token: token)
            activeSessions[ObjectIdentifier(session)] = session
        } else if isHebrew(raw) && readIvritConfig() != nil {
            print("[voice] Connected (he → ivrit.ai STT)")
            let session = LocalSession(locale: "he-IL", useIvrit: true)
            activeSessions[ObjectIdentifier(session)] = session
            session.receive(conn)
        } else {
            let locale = appleLocale(raw)
            print("[voice] Connected (\(locale) → Apple STT)")
            let session = LocalSession(locale: locale)
            activeSessions[ObjectIdentifier(session)] = session
            session.receive(conn)
        }

        conn.stateUpdateHandler = { state in
            if case .cancelled = state { activeSessions.removeAll() }
            if case .failed = state { activeSessions.removeAll() }
        }
    }
    listener.start(queue: .main)
    print("[voice] Voice server on ws://127.0.0.1:\(PORT)")
    print("[voice] Native languages → Anthropic | Hebrew → ivrit.ai | Others → Apple STT")
    if let engine = readIvritConfig() {
        print("[voice] ivrit.ai configured ✓ (\(ivritEngineLabel(engine)))")
    } else {
        print("[voice] ivrit.ai not configured — Hebrew will use Apple STT (set up via setup.sh for better quality)")
    }
}

// MARK: - App entry

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ n: Notification) {
        if SFSpeechRecognizer.authorizationStatus() == .authorized {
            startServer()
        } else {
            NSApp.activate(ignoringOtherApps: true)
            SFSpeechRecognizer.requestAuthorization { status in
                DispatchQueue.main.async {
                    if status == .authorized { startServer() }
                    else {
                        print("[voice] Speech Recognition denied. Grant in System Settings > Privacy > Speech Recognition.")
                        NSApp.terminate(nil)
                    }
                }
            }
        }
    }
}

let delegate = AppDelegate()
app.delegate = delegate
app.run()
