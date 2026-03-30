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
"$VENV_DIR/bin/pip" install --quiet websockets vosk
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

echo ""
echo "=== Done ==="
echo "Restart Claude Code, enable /voice, and speak."
echo "Switch language with /config."
echo ""
echo "Check status:  systemctl --user status $SERVICE_NAME"
echo "View logs:     journalctl --user -u $SERVICE_NAME -f"
echo ""
echo "Uninstall: curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/uninstall.sh | bash"
