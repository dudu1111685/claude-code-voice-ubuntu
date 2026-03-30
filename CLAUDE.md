# Voice Support for Claude Code

Adds native on-device speech-to-text to Claude Code's `/voice` command. macOS uses Apple's SFSpeechRecognizer; Linux uses Vosk for offline STT. Hebrew uses ivrit.ai (via RunPod) for high-quality transcription. Survives updates.

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

## Hebrew (ivrit.ai) setup

For high-quality Hebrew transcription, the project integrates [ivrit.ai](https://www.ivrit.ai/en/api/) powered by RunPod serverless inference.

### How to set up

Run `setup.sh` — it will ask if you want to configure ivrit.ai and which mode to use.

Or manually add to `~/.claude/settings.json`:

**Local mode (CPU/GPU, no internet needed):**
```json
{
  "ivritAi": {
    "engine": "local",
    "device": "cpu",
    "computeType": "float32",
    "model": "ivrit-ai/faster-whisper-v2-d4"
  }
}
```

Device options: `cpu`, `cuda` (NVIDIA), `cuda:0`, `cuda:1`, `mps` (Apple Silicon).
Compute type: `float32` (CPU default), `float16` (GPU default), `int8` (faster, less accurate).
Models: `ivrit-ai/faster-whisper-v2-d4` (best), `ivrit-ai/whisper-large-v3-turbo-ct2` (faster).

**Cloud mode (RunPod, pay-per-use):**
```json
{
  "ivritAi": {
    "engine": "runpod",
    "apiKey": "rp_YOUR_RUNPOD_API_KEY",
    "endpointId": "YOUR_ENDPOINT_ID"
  }
}
```

### Getting a RunPod API Key (for cloud mode)

1. Create an account at [runpod.io](https://www.runpod.io/?ref=06octndf)
2. Go to **Settings → API Keys → Create API Key** (starts with `rp_...`)
3. Go to **Serverless → Endpoints → New Endpoint**
   - Search for the `ivrit-ai` template, or deploy from the RunPod explore page
   - Copy the **Endpoint ID** from the endpoint URL
4. Video walkthrough: [https://www.youtube.com/watch?v=IkqArVv_Uts](https://www.youtube.com/watch?v=IkqArVv_Uts)
5. More info: [https://www.ivrit.ai/en/api/](https://www.ivrit.ai/en/api/)

Without ivrit.ai configured, Hebrew falls back to Vosk (Linux) or Apple STT (macOS).

## Supported languages

en, he, es, fr, de, ja, ko, pt, it, ru, zh, ar, hi, tr, nl, pl, uk, el, cs, da, sv, no — and any other language supported by the STT engine.

## Linux notes

- Service management: `systemctl --user {status|restart|stop} claude-code-voice`
- Logs: `journalctl --user -u claude-code-voice -f`
- Models stored in: `~/.local/share/claude-code-voice/models/`
