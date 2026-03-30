# Voice Support for Claude Code

Adds on-device and cloud speech-to-text to Claude Code's `/voice` command. Uses Soniox streaming STT as primary engine (60+ languages, real-time partial results), with Anthropic proxy and Vosk as fallback. Single Python voice server for macOS and Linux.

## Architecture

```
Claude Code (/voice)
  |
  +-- WebSocket (ws://127.0.0.1:19876)
  |
  +-- voice_server.py
        |
        +-- SONIOX_API_KEY set?
        |     YES -> Soniox streaming STT (60+ languages, <200ms latency)
        |     NO  -> Fallback path:
        |              +-- Native language + OAuth? -> Anthropic proxy
        |              +-- Otherwise -> Vosk local STT
```

**Thread-safe design:** The Soniox SDK is synchronous. The voice server bridges it with the async WebSocket using `asyncio.Queue` + `ThreadPoolExecutor`:

```
async handle_connection
  +-- asyncio.Queue (audio_queue): WS -> thread
  +-- asyncio.Queue (transcript_queue): thread -> WS
  +-- async ws_reader: reads WS, puts on audio_queue
  +-- async ws_writer: reads transcript_queue, sends to WS
  +-- executor thread: soniox_worker (sync SDK calls)
```

## Install

```bash
# macOS or Linux
curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/setup.sh | bash
```

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/uninstall.sh | bash
```

## Soniox setup

Soniox provides streaming speech-to-text for 60+ languages with <200ms latency.

1. Get a free API key at [console.soniox.com](https://console.soniox.com) (includes $200 credit)
2. Run `setup.sh` -- it will ask for your API key
3. Or manually add to `~/.claude/settings.json`:

```json
{
  "env": {
    "VOICE_STREAM_BASE_URL": "ws://127.0.0.1:19876",
    "SONIOX_API_KEY": "your_api_key_here"
  }
}
```

Without `SONIOX_API_KEY`, the server falls back to Anthropic proxy (for native languages) or Vosk local STT.

## Switch language

`/config` -> change language -> next recording uses it. Default: English.

## Supported languages

With Soniox: 60+ languages including en, he, es, fr, de, ja, ko, pt, it, ru, zh, ar, hi, tr, nl, pl, uk, el, cs, da, sv, no, and many more.

Without Soniox (fallback): Native languages via Anthropic proxy, others via Vosk (limited language support).

## Service management

**Linux:**
```bash
systemctl --user status claude-code-voice
systemctl --user restart claude-code-voice
journalctl --user -u claude-code-voice -f
```

**macOS:**
```bash
launchctl list | grep claude-code-voice
tail -f /tmp/claude-code-voice.log
```

## WebSocket protocol

- Port: 19876
- Client sends: binary PCM (16-bit mono 16kHz) + JSON (CloseStream, KeepAlive)
- Server sends: `{"type": "TranscriptText", "data": "full replacement text"}` (partial results)
- Server sends: `{"type": "TranscriptEndpoint"}` (utterance complete)
- Non-empty TranscriptText within 1.5s of first audio (Soniox: <200ms)
- CloseStream: 5-second finalize timeout

## Key files

- `scripts/voice_server.py` -- main voice server (Soniox + fallback)
- `setup.sh` -- cross-platform installer entry point
- `scripts/setup_linux.sh` -- Linux installer (systemd)
- `scripts/setup_macos.sh` -- macOS installer (launchd)
- `uninstall.sh` -- cross-platform uninstaller
- `tests/test_voice_server.py` -- comprehensive test suite
