# Voice Support for Claude Code

Adds native on-device speech-to-text to Claude Code's `/voice` command. macOS uses Apple's SFSpeechRecognizer; Linux uses Vosk for offline STT. Survives updates.

## Install

```bash
# macOS
curl -fsSL https://raw.githubusercontent.com/eladcandroid/claude-code-voice/main/setup.sh | bash

# Linux (Ubuntu/Debian)
curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/setup.sh | bash
```

## Uninstall

```bash
# macOS
curl -fsSL https://raw.githubusercontent.com/eladcandroid/claude-code-voice/main/uninstall.sh | bash

# Linux
curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/uninstall.sh | bash
```

## Switch language

`/config` → change language → next recording uses it. Default: English. Set to `he` for Hebrew.

## Supported languages

en, he, es, fr, de, ja, ko, pt, it, ru, zh, ar, hi, tr, nl, pl, uk, el, cs, da, sv, no — and any other language supported by the STT engine.

## Linux notes

- Service management: `systemctl --user {status|restart|stop} claude-code-voice`
- Logs: `journalctl --user -u claude-code-voice -f`
- Models stored in: `~/.local/share/claude-code-voice/models/`
