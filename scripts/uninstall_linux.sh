#!/bin/bash
set -euo pipefail

echo "=== Uninstalling Claude Code Voice (Linux) ==="
echo ""

SERVICE_NAME="claude-code-voice"

# 1. Stop and remove systemd service
if systemctl --user is-active "$SERVICE_NAME" &>/dev/null; then
  systemctl --user stop "$SERVICE_NAME"
fi
systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/$SERVICE_NAME.service"
systemctl --user daemon-reload 2>/dev/null || true
echo "[✓] Removed systemd service"

# 2. Kill any running voice server
pkill -f "voice_server.py" 2>/dev/null || true
echo "[✓] Stopped voice server"

# 3. Remove VOICE_STREAM_BASE_URL from settings.json
SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ] && grep -q VOICE_STREAM_BASE_URL "$SETTINGS"; then
  python3 - << 'PYEOF'
import json, os
path = os.path.expanduser("~/.claude/settings.json")
with open(path) as f:
    s = json.load(f)
s.get("env", {}).pop("VOICE_STREAM_BASE_URL", None)
with open(path, "w") as f:
    json.dump(s, f, indent=2, ensure_ascii=False)
PYEOF
  echo "[✓] Removed VOICE_STREAM_BASE_URL from settings.json"
else
  echo "[–] settings.json already clean"
fi

# 4. Remove install directory (venv, models, scripts)
INSTALL_DIR="$HOME/.local/share/claude-code-voice"
if [ -d "$INSTALL_DIR" ]; then
  rm -rf "$INSTALL_DIR"
  echo "[✓] Removed $INSTALL_DIR"
fi

echo ""
echo "=== Uninstall complete ==="
echo "Restart Claude Code for changes to take effect."
