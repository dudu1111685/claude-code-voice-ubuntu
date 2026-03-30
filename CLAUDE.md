# Hebrew Voice Support for Claude Code

Adds Hebrew to Claude Code's `/voice` command using Apple's native on-device `SFSpeechRecognizer`. No binary patching, no API keys — survives Claude Code updates automatically.

## How it works

Claude Code's voice streams audio via WebSocket. The `VOICE_STREAM_BASE_URL` env var redirects it to a local server (`localhost:19876`) that transcribes Hebrew via Apple's `SFSpeechRecognizer` instead of Anthropic's server.

## Usage

```bash
./setup.sh   # One-time: builds STT app, installs service, configures settings
```

After setup, restart Claude Code. `/voice` (spacebar push-to-talk) transcribes Hebrew.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.hebrew-voice.server.plist
rm ~/Library/LaunchAgents/com.hebrew-voice.server.plist
# Remove VOICE_STREAM_BASE_URL from ~/.claude/settings.json
```
