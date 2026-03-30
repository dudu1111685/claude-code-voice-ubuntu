# Claude Code Voice

Adds native on-device speech-to-text to Claude Code's `/voice` command. Natively supported languages proxy to Anthropic's server; unsupported languages (Hebrew, Arabic, etc.) transcribe locally on-device. No API keys, no binary patching — survives Claude Code updates.

**Supported platforms:** macOS and Linux (Ubuntu/Debian).

## Quick install

```bash
# macOS
curl -fsSL https://raw.githubusercontent.com/eladcandroid/claude-code-voice/main/setup.sh | bash

# Linux (Ubuntu/Debian)
curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/setup.sh | bash
```

The installer auto-detects your platform and runs the appropriate setup.

## Requirements

### macOS
- macOS (Apple Silicon or Intel)
- Xcode Command Line Tools (`xcode-select --install`)
- Claude Code with `/voice` support

### Linux (Ubuntu/Debian)
- Ubuntu 20.04+ or Debian 11+ (other distros may work)
- Python 3.8+
- Claude Code with `/voice` support
- ~100MB disk space (Python venv + Vosk model)

## Usage

After install, restart Claude Code:

1. `/voice` to enable voice mode
2. Hold **Space** to record
3. Speak
4. Release — transcript appears

> **First run:** macOS will prompt for Speech Recognition permission — click **Allow**.

## Switching languages

Type `/config` in Claude Code to change the language. The voice server picks it up immediately — no restart needed.

### Supported languages

| Language | `/config` value | Backend |
|----------|----------------|---------|
| English | `en` (default) | Anthropic |
| Spanish | `es` | Anthropic |
| French | `fr` | Anthropic |
| German | `de` | Anthropic |
| Japanese | `ja` | Anthropic |
| Korean | `ko` | Anthropic |
| Portuguese | `pt` | Anthropic |
| Italian | `it` | Anthropic |
| Russian | `ru` | Anthropic |
| Hindi | `hi` | Anthropic |
| Indonesian | `id` | Anthropic |
| Polish | `pl` | Anthropic |
| Turkish | `tr` | Anthropic |
| Dutch | `nl` | Anthropic |
| Ukrainian | `uk` | Anthropic |
| Greek | `el` | Anthropic |
| Czech | `cs` | Anthropic |
| Danish | `da` | Anthropic |
| Swedish | `sv` | Anthropic |
| Norwegian | `no` | Anthropic |
| **Hebrew** | `he` | Apple STT |
| **Arabic** | `ar` | Apple STT |
| **Chinese** | `zh` | Apple STT |

The 20 natively supported languages are proxied to Anthropic's server for best quality. Other languages are transcribed locally (Apple STT on macOS, Vosk on Linux).

## How it works

Claude Code has an undocumented `VOICE_STREAM_BASE_URL` env var that redirects its voice WebSocket. This project runs a local server on `localhost:19876` that acts as a smart router:

- **Native languages** (20) → proxied to Anthropic's voice server with OAuth token
- **Other languages** → transcribed locally on-device

```
                          ┌─ native lang ──▶ Anthropic server
┌─────────────┐   audio   │                  (streaming STT)
│ Claude Code  │──chunks──▶│ voice-server
│ /voice + ␣   │◀──text───│
└─────────────┘           └─ other lang ──▶ Local STT
                                             (on-device)
```

### macOS
Single Swift binary — WebSocket server, proxy, and Apple SFSpeechRecognizer combined. No external runtimes needed.

### Linux
Python-based server using [Vosk](https://alphacephei.com/vosk/) for offline speech recognition. Runs as a systemd user service with auto-restart. Dependencies are isolated in a Python venv.

## Uninstall

```bash
# macOS
curl -fsSL https://raw.githubusercontent.com/eladcandroid/claude-code-voice/main/uninstall.sh | bash

# Linux
curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/uninstall.sh | bash
```

## Linux management

```bash
# Check service status
systemctl --user status claude-code-voice

# View logs
journalctl --user -u claude-code-voice -f

# Restart
systemctl --user restart claude-code-voice
```

## Project structure

```
├── setup.sh                # Cross-platform installer (auto-detects OS)
├── uninstall.sh            # Cross-platform uninstaller
└── scripts/
    ├── server.swift        # macOS: WebSocket server + proxy + Apple STT
    ├── voice_server.py     # Linux: WebSocket server + proxy + Vosk STT
    ├── setup_linux.sh      # Linux-specific setup
    └── uninstall_linux.sh  # Linux-specific uninstall
```
