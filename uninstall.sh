#!/bin/bash
set -euo pipefail

# Claude Code Voice — cross-platform uninstaller

OS="$(uname -s)"

echo "=== Uninstalling Claude Code Voice ==="
echo ""

# 1. Stop services
case "$OS" in
  Linux)
    systemctl --user stop claude-code-voice 2>/dev/null || true
    systemctl --user disable claude-code-voice 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/claude-code-voice.service"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "[done] Removed systemd service"
    ;;
  Darwin)
    for name in com.claude-code-voice com.claude-code-voice.server com.hebrew-voice.server; do
      PLIST="$HOME/Library/LaunchAgents/$name.plist"
      if [ -f "$PLIST" ]; then
        launchctl unload "$PLIST" 2>/dev/null || true
        rm -f "$PLIST"
        echo "[done] Removed $name"
      fi
    done
    # Reset Speech Recognition permission (legacy)
    for bid in com.claude-code-voice com.claude-code-voice.server com.hebrew-voice.server; do
      tccutil reset SpeechRecognition "$bid" 2>/dev/null || true
    done
    ;;
esac

# 2. Kill running processes
pkill -f "voice_server.py" 2>/dev/null || true
pkill -f "voice-server" 2>/dev/null || true
echo "[done] Stopped voice server"

# 3. Remove settings
SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ]; then
  python3 -c "
import json, os
path = os.path.expanduser('~/.claude/settings.json')
with open(path) as f: s = json.load(f)
env = s.get('env', {})
env.pop('VOICE_STREAM_BASE_URL', None)
env.pop('SONIOX_API_KEY', None)
# Remove legacy ivritAi config
s.pop('ivritAi', None)
with open(path, 'w') as f: json.dump(s, f, indent=2, ensure_ascii=False)
" 2>/dev/null || true
  echo "[done] Cleaned settings.json"
fi

# 4. Remove install directories
for dir in "$HOME/.local/share/claude-code-voice" "$HOME/.local/share/hebrew-voice"; do
  if [ -d "$dir" ]; then
    rm -rf "$dir"
    echo "[done] Removed $dir"
  fi
done

echo ""
echo "=== Uninstall complete ==="
echo "Restart Claude Code for changes to take effect."
