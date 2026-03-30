#!/bin/bash
set -euo pipefail

# Claude Code Voice — cross-platform installer
# Uses Soniox streaming STT (primary) with Anthropic/Vosk fallback

INSTALL_DIR="$HOME/.local/share/claude-code-voice"
OS="$(uname -s)"

# If running via curl|bash, clone the repo first
if [ ! -f "scripts/voice_server.py" ] && [ ! -f "$INSTALL_DIR/scripts/voice_server.py" ]; then
  echo "Downloading claude-code-voice..."
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 https://github.com/dudu1111685/claude-code-voice-ubuntu.git "$INSTALL_DIR" 2>/dev/null
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
[ -f "$SCRIPT_DIR/scripts/voice_server.py" ] || SCRIPT_DIR="$INSTALL_DIR"

case "$OS" in
  Linux)  exec bash "$SCRIPT_DIR/scripts/setup_linux.sh" ;;
  Darwin) exec bash "$SCRIPT_DIR/scripts/setup_macos.sh" ;;
  *)      echo "Unsupported OS: $OS (Linux and macOS supported)"; exit 1 ;;
esac
