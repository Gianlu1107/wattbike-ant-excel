#!/bin/bash
# Installer macOS: rimuove quarantena Gatekeeper e copia in /Applications
# Doppio click apre brevemente Terminale solo per l'installazione; l'app poi parte senza.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

APP=""
if [[ -d "WattbikeLogger.app" ]]; then
  APP="WattbikeLogger.app"
else
  APP="$(find . -maxdepth 2 -name 'WattbikeLogger.app' -type d | head -1 || true)"
fi

if [[ -z "$APP" || ! -d "$APP" ]]; then
  osascript -e 'display dialog "WattbikeLogger.app non trovato accanto a questo installer." buttons {"OK"} default button 1 with icon stop'
  exit 1
fi

osascript -e 'display dialog "Installo Wattbike Logger in Applicazioni.\n(Potrebbe chiedere la password di amministratore.)" buttons {"Annulla","Installa"} default button 2' >/dev/null || {
  osascript -e 'tell application "Terminal" to close front window' >/dev/null 2>&1 || true
  exit 0
}

# Rimuove attributo quarantena (download da browser / GitHub)
xattr -cr "$APP" 2>/dev/null || true
chmod -R a+rX "$APP"

DEST="/Applications/WattbikeLogger.app"
rm -rf "$DEST"
cp -R "$APP" "$DEST"
xattr -cr "$DEST" 2>/dev/null || true

open "$DEST"
osascript -e 'display dialog "Installazione completata.\nTrovi Wattbike Logger in Applicazioni (senza Terminale)." buttons {"OK"} default button 1' >/dev/null || true

# Chiude la finestra Terminale dell'installer
osascript -e 'tell application "Terminal" to close front window' >/dev/null 2>&1 || true
exit 0
