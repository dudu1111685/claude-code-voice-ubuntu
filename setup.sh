#!/bin/bash
set -euo pipefail

# Claude Code Voice — cross-platform installer.
# macOS: Swift + Apple SFSpeechRecognizer
# Linux: Python + Vosk STT

INSTALL_DIR="$HOME/.local/share/claude-code-voice"
OS="$(uname -s)"

# Linux → delegate to Linux-specific setup
if [ "$OS" = "Linux" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  if [ -f "$SCRIPT_DIR/scripts/setup_linux.sh" ]; then
    exec bash "$SCRIPT_DIR/scripts/setup_linux.sh"
  else
    # Running via curl|bash — clone first, then run Linux setup
    echo "Downloading claude-code-voice..."
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 https://github.com/dudu1111685/claude-code-voice-ubuntu.git "$INSTALL_DIR" 2>/dev/null
    exec bash "$INSTALL_DIR/scripts/setup_linux.sh"
  fi
fi

# ── macOS setup below ─────────────────────────────────────────────

# If running via curl|bash, clone the repo first
if [ ! -f "scripts/server.swift" ]; then
  echo "Downloading claude-code-voice..."
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 https://github.com/eladcandroid/claude-code-voice.git "$INSTALL_DIR" 2>/dev/null
  cd "$INSTALL_DIR"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS="$SCRIPT_DIR/scripts"
APP="$SCRIPTS/VoiceServer.app"

echo "=== Claude Code Voice ==="
echo ""

if ! command -v swiftc &>/dev/null; then
  echo "ERROR: Xcode Command Line Tools required."
  echo "  Install: xcode-select --install"
  exit 1
fi

# 1. Build
echo "[1/3] Building..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

swiftc -O -o "$APP/Contents/MacOS/voice-server" "$SCRIPTS/server.swift" \
  -framework Network -framework Speech -framework Foundation -framework AppKit 2>/dev/null

cat > "$APP/Contents/Info.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>CFBundleIdentifier</key><string>com.claude-code-voice</string>
    <key>CFBundleName</key><string>ClaudeCodeVoice</string>
    <key>CFBundleExecutable</key><string>voice-server</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>NSSpeechRecognitionUsageDescription</key>
    <string>Voice transcription for Claude Code</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>Audio input for speech recognition</string>
</dict></plist>
EOF

codesign --force --sign - --entitlements /dev/stdin "$APP" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>com.apple.security.device.audio-input</key><true/>
</dict></plist>
EOF
echo "  Done"

# 2. Grant Speech Recognition permission
echo "[2/3] Requesting Speech Recognition permission..."
echo "  >>> If a dialog appears, click ALLOW <<<"
open -W "$APP" &
OPEN_PID=$!
sleep 10
kill "$OPEN_PID" 2>/dev/null || true
pkill -f voice-server 2>/dev/null || true
sleep 1

# 3. Configure settings + install launch agent
echo "[3/3] Configuring..."

SETTINGS="$HOME/.claude/settings.json"
if [ ! -f "$SETTINGS" ]; then
  mkdir -p "$HOME/.claude"
  echo '{}' > "$SETTINGS"
fi

python3 - << 'PYEOF'
import json, os
path = os.path.expanduser("~/.claude/settings.json")
with open(path) as f:
    s = json.load(f)
s.setdefault("env", {})["VOICE_STREAM_BASE_URL"] = "ws://127.0.0.1:19876"
with open(path, "w") as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
print("  Updated settings.json")
PYEOF

PLIST="$HOME/Library/LaunchAgents/com.claude-code-voice.plist"
launchctl unload "$PLIST" 2>/dev/null || true
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.claude-code-voice</string>
    <key>ProgramArguments</key><array>
        <string>$APP/Contents/MacOS/voice-server</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/tmp/claude-code-voice.log</string>
    <key>StandardErrorPath</key><string>/tmp/claude-code-voice.log</string>
</dict></plist>
EOF

launchctl load "$PLIST"
echo "  Voice server started"

# 4. Optional: ivrit.ai setup for Hebrew
echo ""
echo "=== Hebrew (ivrit.ai) Setup ==="
echo ""
echo "For high-quality Hebrew speech recognition, you can configure ivrit.ai."
echo "Two modes available:"
echo "  1) Local  — runs on your CPU/GPU, no internet needed (uses faster-whisper)"
echo "  2) Cloud  — uses RunPod serverless API (requires API key, pay-per-use)"
echo "  3) Skip   — Hebrew will use Apple STT"
echo ""
read -rp "Choose mode [1=local / 2=cloud / 3=skip]: " IVRIT_MODE

if [[ "$IVRIT_MODE" == "1" ]]; then
  echo ""
  echo "Setting up local ivrit.ai (faster-whisper)..."
  echo "Installing Python dependencies for local ivrit.ai..."

  # Ensure pip + ivrit[faster-whisper] are available
  if ! python3 -c "import ivrit" 2>/dev/null; then
    pip3 install --quiet "ivrit[faster-whisper]" 2>/dev/null || python3 -m pip install --quiet "ivrit[faster-whisper]"
  fi

  echo ""
  echo "Device options: cpu, mps (Apple Silicon GPU)"
  read -rp "  Device [cpu]: " IVRIT_DEVICE
  IVRIT_DEVICE="${IVRIT_DEVICE:-cpu}"

  IVRIT_COMPUTE=""
  if [[ "$IVRIT_DEVICE" == "mps" ]]; then
    echo "  Compute type options: float16 (recommended for GPU), float32"
    read -rp "  Compute type [float16]: " IVRIT_COMPUTE
    IVRIT_COMPUTE="${IVRIT_COMPUTE:-float16}"
  else
    echo "  Compute type options: float32 (default for CPU), int8 (faster, slightly less accurate)"
    read -rp "  Compute type [float32]: " IVRIT_COMPUTE
    IVRIT_COMPUTE="${IVRIT_COMPUTE:-float32}"
  fi

  echo ""
  echo "  Model options:"
  echo "    ivrit-ai/faster-whisper-v2-d4       (recommended, best accuracy)"
  echo "    ivrit-ai/whisper-large-v3-turbo-ct2  (faster, slightly less accurate)"
  read -rp "  Model [ivrit-ai/faster-whisper-v2-d4]: " IVRIT_MODEL
  IVRIT_MODEL="${IVRIT_MODEL:-ivrit-ai/faster-whisper-v2-d4}"

  python3 - "$IVRIT_DEVICE" "$IVRIT_COMPUTE" "$IVRIT_MODEL" << 'PYEOF'
import json, os, sys
device, compute_type, model = sys.argv[1], sys.argv[2], sys.argv[3]
path = os.path.expanduser("~/.claude/settings.json")
with open(path) as f:
    s = json.load(f)
s["ivritAi"] = {
    "engine": "local",
    "device": device,
    "computeType": compute_type,
    "model": model,
}
with open(path, "w") as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
print(f"  ivrit.ai configured (local, {device}, {compute_type})!")
print("  The model will be downloaded on first use from HuggingFace.")
print("  Set language to 'he' with /config to use it.")
PYEOF

elif [[ "$IVRIT_MODE" == "2" ]]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  How to get your RunPod API Key & Endpoint ID:"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  1. Create an account at https://www.runpod.io/?ref=06octndf"
  echo "  2. Go to Settings → API Keys → Create API Key"
  echo "     (copy the key starting with 'rp_...')"
  echo "  3. Go to Serverless → Endpoints → New Endpoint"
  echo "     - Search for 'ivrit-ai' template"
  echo "     - Or deploy from: https://www.runpod.io/console/explore/ivrit-ai-whisper"
  echo "     - Copy the Endpoint ID from the endpoint URL"
  echo "  4. Video guide: https://www.youtube.com/watch?v=IkqArVv_Uts"
  echo ""
  echo "  More info: https://www.ivrit.ai/en/api/"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""

  read -rp "  RunPod API Key (rp_...): " IVRIT_API_KEY
  read -rp "  RunPod Endpoint ID: " IVRIT_ENDPOINT_ID

  if [ -n "$IVRIT_API_KEY" ] && [ -n "$IVRIT_ENDPOINT_ID" ]; then
    python3 - "$IVRIT_API_KEY" "$IVRIT_ENDPOINT_ID" << 'PYEOF'
import json, os, sys
api_key, endpoint_id = sys.argv[1], sys.argv[2]
path = os.path.expanduser("~/.claude/settings.json")
with open(path) as f:
    s = json.load(f)
s["ivritAi"] = {"engine": "runpod", "apiKey": api_key, "endpointId": endpoint_id}
with open(path, "w") as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
print("  ivrit.ai configured (RunPod cloud)!")
print("  Set language to 'he' with /config to use it.")
PYEOF
  else
    echo "  Skipped — you can set this up later by re-running setup.sh"
  fi
else
  echo "Skipped. You can set up ivrit.ai later by re-running setup.sh."
fi

echo ""
echo "=== Done ==="
echo "Restart Claude Code, enable /voice, and speak."
echo "Switch language with /config."
echo ""
echo "Uninstall: curl -fsSL https://raw.githubusercontent.com/eladcandroid/claude-code-voice/main/uninstall.sh | bash"
