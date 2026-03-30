# Hebrew Voice for Claude Code (macOS)

Adds Hebrew speech-to-text to Claude Code's `/voice` command using Apple's native on-device `SFSpeechRecognizer`. No API keys, no cloud services, no binary patching — runs entirely on your Mac and survives Claude Code updates.

## How it works

Claude Code has an undocumented `VOICE_STREAM_BASE_URL` env var that redirects its voice WebSocket to a custom server. This project runs a local server on `localhost:19876` that receives Claude Code's audio stream and transcribes it using Apple's `SFSpeechRecognizer` for Hebrew.

```
┌─────────────┐    audio    ┌──────────────┐   WAV file   ┌─────────────────┐
│ Claude Code  │───chunks───▶│ voice-server │─────────────▶│ Transcribe.app  │
│ /voice + ␣   │◀──text─────│ (localhost)   │◀────text────│ (Apple STT)     │
└─────────────┘             └──────────────┘              └─────────────────┘
```

## Requirements

- macOS (Apple Silicon or Intel)
- [Bun](https://bun.sh) runtime (`brew install bun`)
- Xcode Command Line Tools (`xcode-select --install`)
- Claude Code with `/voice` support

## Install

```bash
git clone https://github.com/user/claude-code-hebrew-voice.git
cd claude-code-hebrew-voice
./setup.sh
```

The setup script:
- Compiles and signs the native STT app
- Adds `VOICE_STREAM_BASE_URL` to `~/.claude/settings.json`
- Installs a LaunchAgent that starts the voice server on login

Then restart Claude Code.

## Usage

1. Type `/voice` to enable voice mode
2. Hold **Space** to record
3. Speak Hebrew
4. Release — transcript appears

> **First run:** macOS will prompt for Speech Recognition permission — click **Allow**.

## Project structure

```
├── setup.sh                    # One-command install
├── scripts/
│   ├── voice-server.js         # Local WebSocket voice server (Bun)
│   ├── transcribe.swift        # Apple SFSpeechRecognizer wrapper
│   ├── entitlements.plist      # macOS entitlements for audio access
│   └── Transcribe.app/         # Signed app bundle (built by setup.sh)
├── CLAUDE.md
└── README.md
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.hebrew-voice.server.plist
rm ~/Library/LaunchAgents/com.hebrew-voice.server.plist
```

Then remove `VOICE_STREAM_BASE_URL` from `~/.claude/settings.json`.
