# Hebrew Voice for Claude Code (macOS)

Adds Hebrew speech-to-text to Claude Code's `/voice` command using Apple's native on-device `SFSpeechRecognizer`. No API keys, no cloud services, no binary patching — runs entirely on your Mac and survives Claude Code updates.

## Quick install

```bash
curl -fsSL https://raw.githubusercontent.com/eladcandroid/claude-code-hebrew-voice/main/setup.sh | bash
```

## Requirements

- macOS (Apple Silicon or Intel)
- Xcode Command Line Tools (`xcode-select --install`)
- Claude Code with `/voice` support

## Usage

After install, restart Claude Code:

1. `/voice` to enable voice mode
2. Hold **Space** to record
3. Speak Hebrew
4. Release — transcript appears

> **First run:** macOS will prompt for Speech Recognition permission — click **Allow**.

## Switching languages

The server reads `language` from `~/.claude/settings.json` on every recording. To switch:

1. Type `/config` in Claude Code
2. Change the language (e.g., `he`, `en`, `ja`, `es`, ...)
3. Next `/voice` recording uses the new language

Supports all languages available in Apple's `SFSpeechRecognizer`: Hebrew, English, Spanish, French, German, Japanese, Korean, Portuguese, Italian, Russian, Chinese, Arabic, Hindi, Turkish, Dutch, Polish, Ukrainian, Greek, Czech, Danish, Swedish, Norwegian, and more.

## How it works

Claude Code has an undocumented `VOICE_STREAM_BASE_URL` env var that redirects its voice WebSocket. This project runs a native macOS app on `localhost:19876` that receives the audio stream and transcribes it using Apple's on-device `SFSpeechRecognizer` for Hebrew.

```
┌─────────────┐    audio    ┌───────────────────────────────┐
│ Claude Code  │───chunks──▶│ HebrewVoice.app               │
│ /voice + ␣   │◀──text────│ WebSocket server + Apple STT  │
└─────────────┘             └───────────────────────────────┘
```

Everything is a single Swift binary — WebSocket server and speech recognition combined. No external runtimes needed.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/eladcandroid/claude-code-hebrew-voice/main/uninstall.sh | bash
```

## Project structure

```
├── setup.sh              # One-command install
├── uninstall.sh           # Full uninstall
└── scripts/
    └── server.swift       # WebSocket server + Apple STT (single file)
```
