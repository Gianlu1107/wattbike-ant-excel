#!/bin/bash
# Costruisce WattbikeLogger-macos-arm64.pkg (Installer.app a step)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="${1:-}"
APP_SRC="${2:-$ROOT/dist/WattbikeLogger.app}"
OUT_DIR="${3:-$ROOT/out}"
ARCH_LABEL="${4:-macos-arm64}"

if [[ -z "$VERSION" ]]; then
  VERSION="$(python3 -c "from pathlib import Path; import re; t=Path('$ROOT/wattbike_logger/__init__.py').read_text(); print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', t).group(1))")"
fi

if [[ ! -d "$APP_SRC" ]]; then
  echo "Manca $APP_SRC — esegui prima pyinstaller." >&2
  exit 1
fi

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/wattbike-pkg.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

mkdir -p "$WORKDIR/root/Applications" "$WORKDIR/scripts" "$WORKDIR/resources" "$OUT_DIR"
cp -R "$APP_SRC" "$WORKDIR/root/Applications/WattbikeLogger.app"
xattr -cr "$WORKDIR/root/Applications/WattbikeLogger.app" || true
codesign --force --deep -s - "$WORKDIR/root/Applications/WattbikeLogger.app" 2>/dev/null || true

cp "$PKG_DIR/scripts/postinstall" "$WORKDIR/scripts/postinstall"
chmod 755 "$WORKDIR/scripts/postinstall"

cp "$PKG_DIR/Resources/Welcome.html" "$WORKDIR/resources/"
cp "$PKG_DIR/Resources/Conclusion.html" "$WORKDIR/resources/"
cp "$ROOT/LICENSE" "$WORKDIR/resources/License.txt"

# Distribution con versione aggiornata
sed "s/VERSION_PLACEHOLDER/${VERSION}/g" "$PKG_DIR/Distribution.xml" > "$WORKDIR/Distribution.xml"

COMPONENT="$WORKDIR/WattbikeLogger-component.pkg"
pkgbuild \
  --root "$WORKDIR/root" \
  --scripts "$WORKDIR/scripts" \
  --identifier "com.gianlu.wattbikelogger" \
  --version "$VERSION" \
  --install-location "/" \
  --min-os-version "12.0" \
  "$COMPONENT"

OUT_PKG="$OUT_DIR/WattbikeLogger-${ARCH_LABEL}.pkg"
productbuild \
  --distribution "$WORKDIR/Distribution.xml" \
  --resources "$WORKDIR/resources" \
  --package-path "$WORKDIR" \
  "$OUT_PKG"

# Firma ad-hoc del pkg (opzionale; Gatekeeper può comunque chiedere conferma)
productsign --sign - "$OUT_PKG" "${OUT_PKG}.signed" 2>/dev/null && mv "${OUT_PKG}.signed" "$OUT_PKG" || true

ls -lh "$OUT_PKG"
echo "Creato: $OUT_PKG"
