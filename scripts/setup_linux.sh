#!/bin/bash
set -euo pipefail

# Claude Code Voice (Linux/Ubuntu)
# Installs Python-based voice server with Vosk STT.

INSTALL_DIR="$HOME/.local/share/claude-code-voice"
MODELS_DIR="$INSTALL_DIR/models"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_NAME="claude-code-voice"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/$SERVICE_NAME.service"

# Default Vosk model (small English, ~40MB)
VOSK_MODEL_URL="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_MODEL_NAME="vosk-model-small-en-us-0.15"

# If running via curl|bash, clone the repo first
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [ ! -f "$SCRIPT_DIR/voice_server.py" ]; then
  if [ ! -f "scripts/voice_server.py" ]; then
    echo "Downloading claude-code-voice..."
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 https://github.com/dudu1111685/claude-code-voice-ubuntu.git "$INSTALL_DIR" 2>/dev/null
    SCRIPT_DIR="$INSTALL_DIR/scripts"
  else
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)/scripts"
    [ -f "$SCRIPT_DIR/voice_server.py" ] || SCRIPT_DIR="$(pwd)/scripts"
  fi
fi

echo "=== Claude Code Voice (Linux) ==="
echo ""

# 1. Check/install system dependencies
echo "[1/5] Checking dependencies..."

if ! command -v python3 &>/dev/null; then
  echo "  Installing python3..."
  sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv
fi

if ! python3 -c "import venv" 2>/dev/null; then
  echo "  Installing python3-venv..."
  sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv
fi

# unzip needed for Vosk model
if ! command -v unzip &>/dev/null; then
  echo "  Installing unzip..."
  sudo apt-get update -qq && sudo apt-get install -y -qq unzip
fi

echo "  Done"

# 2. Create venv and install Python packages
echo "[2/5] Setting up Python environment..."
mkdir -p "$INSTALL_DIR"

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet websockets vosk "ivrit[faster-whisper]"
echo "  Done"

# 3. Download Vosk model
echo "[3/5] Setting up speech recognition model..."
mkdir -p "$MODELS_DIR"

if [ ! -d "$MODELS_DIR/$VOSK_MODEL_NAME" ]; then
  echo "  Downloading Vosk model ($VOSK_MODEL_NAME)..."
  TMPZIP=$(mktemp /tmp/vosk-model-XXXXXX.zip)
  curl -fsSL "$VOSK_MODEL_URL" -o "$TMPZIP"
  unzip -qo "$TMPZIP" -d "$MODELS_DIR"
  rm -f "$TMPZIP"
  echo "  Model installed"
else
  echo "  Model already installed"
fi

# 4. Install voice server script
echo "[4/5] Installing voice server..."
cp "$SCRIPT_DIR/voice_server.py" "$INSTALL_DIR/voice_server.py"
chmod +x "$INSTALL_DIR/voice_server.py"
# Install ivrit local helper (used by both Linux and macOS)
if [ -f "$SCRIPT_DIR/transcribe_ivrit_local.py" ]; then
  cp "$SCRIPT_DIR/transcribe_ivrit_local.py" "$INSTALL_DIR/transcribe_ivrit_local.py"
  chmod +x "$INSTALL_DIR/transcribe_ivrit_local.py"
fi

# Configure settings.json
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

# 5. Create and start systemd user service
echo "[5/5] Setting up systemd service..."

# Stop existing service if running
systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true

mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Claude Code Voice Server
After=network.target

[Service]
Type=simple
ExecStart=$VENV_DIR/bin/python3 $INSTALL_DIR/voice_server.py
Restart=always
RestartSec=3
Environment=HOME=$HOME

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
systemctl --user start "$SERVICE_NAME"
echo "  Voice server started"

# 6. Optional: ivrit.ai setup for Hebrew
echo ""
echo "=== Hebrew (ivrit.ai) Setup ==="
echo ""
echo "For high-quality Hebrew speech recognition, you can configure ivrit.ai."
echo "Two modes available:"
echo "  1) Local  — runs on your CPU/GPU, no internet needed (uses faster-whisper)"
echo "  2) Cloud  — uses RunPod serverless API (requires API key, pay-per-use)"
echo "  3) Skip   — Hebrew will use Vosk (lower quality)"
echo ""
read -rp "Choose mode [1=local / 2=cloud / 3=skip]: " IVRIT_MODE

if [[ "$IVRIT_MODE" == "1" ]]; then
  echo ""
  echo "Setting up local ivrit.ai (faster-whisper)..."
  echo ""
  echo "Device options: cpu, cuda (NVIDIA GPU), cuda:0, cuda:1"
  read -rp "  Device [cpu]: " IVRIT_DEVICE
  IVRIT_DEVICE="${IVRIT_DEVICE:-cpu}"

  IVRIT_COMPUTE=""
  if [[ "$IVRIT_DEVICE" == cuda* ]]; then
    echo "  Compute type options: float16 (recommended for GPU), int8_float16, float32"
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
echo "Check status:  systemctl --user status $SERVICE_NAME"
echo "View logs:     journalctl --user -u $SERVICE_NAME -f"
echo ""
echo "Uninstall: curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/uninstall.sh | bash"
