#!/bin/bash
set -euo pipefail

# Linux → delegate to Linux-specific uninstall
if [ "$(uname -s)" = "Linux" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
  if [ -f "$SCRIPT_DIR/scripts/uninstall_linux.sh" ]; then
    exec bash "$SCRIPT_DIR/scripts/uninstall_linux.sh"
  elif [ -f "$HOME/.local/share/claude-code-voice/scripts/uninstall_linux.sh" ]; then
    exec bash "$HOME/.local/share/claude-code-voice/scripts/uninstall_linux.sh"
  else
    # Inline minimal Linux uninstall
    echo "=== Uninstalling Claude Code Voice (Linux) ==="
    systemctl --user stop claude-code-voice 2>/dev/null || true
    systemctl --user disable claude-code-voice 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/claude-code-voice.service"
    pkill -f "voice_server.py" 2>/dev/null || true
    rm -rf "$HOME/.local/share/claude-code-voice"
    echo "=== Uninstall complete ==="
    exit 0
  fi
fi

# ── macOS uninstall below ─────────────────────────────────────────

echo "=== Uninstalling Claude Code Voice ==="
echo ""

# 1. Stop and remove launch agents (current + legacy names)
for name in com.claude-code-voice com.claude-code-voice.server com.hebrew-voice.server; do
  PLIST="$HOME/Library/LaunchAgents/$name.plist"
  if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "[✓] Removed $name"
  fi
done

# 2. Kill any running voice server
pkill -f "voice-server" 2>/dev/null || true
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

# 4. Reset Speech Recognition permission
for bid in com.claude-code-voice com.claude-code-voice.server com.hebrew-voice.server; do
  tccutil reset SpeechRecognition "$bid" 2>/dev/null || true
done
echo "[✓] Reset Speech Recognition permission"

# 5. Remove install directories
for dir in "$HOME/.local/share/claude-code-voice" "$HOME/.local/share/hebrew-voice"; do
  if [ -d "$dir" ]; then
    rm -rf "$dir"
    echo "[✓] Removed $dir"
  fi
done

echo ""
echo "=== Uninstall complete ==="
echo "Restart Claude Code for changes to take effect."
