#!/usr/bin/env bash
# Installer Linux: binario in ~/.local/bin + .desktop
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_SRC="$(find "$DIR" -maxdepth 1 -type f -name 'WattbikeLogger-linux*' | head -1 || true)"
if [[ -z "$BIN_SRC" ]]; then
  echo "WattbikeLogger-linux-x64 non trovato." >&2
  exit 1
fi

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
install -m 755 "$BIN_SRC" "$HOME/.local/bin/WattbikeLogger"
cat > "$HOME/.local/share/applications/wattbike-logger.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Wattbike Logger
Comment=Wattbike ANT+ → Excel
Exec=$HOME/.local/bin/WattbikeLogger
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;Sports;
EOF
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installato: ~/.local/bin/WattbikeLogger"
"$HOME/.local/bin/WattbikeLogger" &
