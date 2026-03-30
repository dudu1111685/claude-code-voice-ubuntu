#!/bin/bash
set -euo pipefail

# Claude Code Voice — macOS installer
# Python + Soniox streaming STT (no Swift, no Xcode required)

INSTALL_DIR="$HOME/.local/share/claude-code-voice"
VENV_DIR="$INSTALL_DIR/venv"
MODELS_DIR="$INSTALL_DIR/models"
PLIST_NAME="com.claude-code-voice"
PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

# Vosk fallback model
VOSK_MODEL_URL="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_MODEL_NAME="vosk-model-small-en-us-0.15"

# Resolve script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
if [ ! -f "$SCRIPT_DIR/voice_server.py" ]; then
  SCRIPT_DIR="$INSTALL_DIR/scripts"
fi

echo "=== Claude Code Voice (macOS) ==="
echo ""

# 1. Check Python
echo "[1/5] Checking dependencies..."

if ! command -v python3 &>/dev/null; then
  echo "  ERROR: Python 3 not found."
  echo "  Install: brew install python@3.12"
  exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
  echo "  ERROR: Python >= 3.10 required (found $PYTHON_VERSION)"
  echo "  Install: brew install python@3.12"
  exit 1
fi

echo "  Done (Python $PYTHON_VERSION)"

# 2. Create venv and install packages
echo "[2/5] Setting up Python environment..."
mkdir -p "$INSTALL_DIR"

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet websockets soniox vosk
echo "  Done"

# 3. Download Vosk model (fallback)
echo "[3/5] Setting up fallback speech model..."
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

# 4. Install voice server
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

# 5. Soniox API key
echo ""
echo "=== Soniox API Key ==="
echo ""
echo "Soniox provides streaming speech-to-text for 60+ languages."
echo "Get a free API key at https://console.soniox.com (includes \$200 credit)."
echo ""

EXISTING_KEY=""
if python3 -c "
import json, os
path = os.path.expanduser('~/.claude/settings.json')
with open(path) as f: s = json.load(f)
key = s.get('env', {}).get('SONIOX_API_KEY', '')
if key: print(key[:8] + '...'); exit(0)
exit(1)
" 2>/dev/null; then
  echo "  Soniox API key already configured."
  read -rp "  Update it? [y/N]: " UPDATE_KEY
  if [[ "$UPDATE_KEY" != [yY]* ]]; then
    EXISTING_KEY="skip"
  fi
fi

if [ "$EXISTING_KEY" != "skip" ]; then
  read -rp "  Soniox API key (or press Enter to skip): " SONIOX_KEY
  if [ -n "$SONIOX_KEY" ]; then
    python3 - "$SONIOX_KEY" << 'PYEOF'
import json, os, sys
key = sys.argv[1]
path = os.path.expanduser("~/.claude/settings.json")
with open(path) as f:
    s = json.load(f)
s.setdefault("env", {})["SONIOX_API_KEY"] = key
with open(path, "w") as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
print("  Soniox API key saved!")
PYEOF
  else
    echo "  Skipped. Voice will use Anthropic proxy + Vosk fallback."
    echo "  You can add it later to ~/.claude/settings.json under env.SONIOX_API_KEY"
  fi
fi

# 6. Install launchd service
echo ""
echo "[5/5] Setting up launchd service..."

# Stop existing service
launchctl unload "$PLIST" 2>/dev/null || true

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>$PLIST_NAME</string>
    <key>ProgramArguments</key><array>
        <string>$VENV_DIR/bin/python3</string>
        <string>$INSTALL_DIR/voice_server.py</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/tmp/claude-code-voice.log</string>
    <key>StandardErrorPath</key><string>/tmp/claude-code-voice.log</string>
</dict></plist>
EOF

launchctl load "$PLIST"
echo "  Voice server started"

echo ""
echo "=== Done ==="
echo "Restart Claude Code, enable /voice, and speak."
echo "Switch language with /config."
echo ""
echo "Logs: tail -f /tmp/claude-code-voice.log"
echo ""
echo "Uninstall: curl -fsSL https://raw.githubusercontent.com/dudu1111685/claude-code-voice-ubuntu/main/uninstall.sh | bash"
