#!/bin/bash
# Costruisce WattbikeLogger-macos-arm64.pkg (Installer.app a step)
# Se MACOS_SIGN_IDENTITY_APP / INSTALLER (+ notary env) sono settati → firma e notarizza.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION="${1:-}"
APP_SRC="${2:-$ROOT/dist/WattbikeLogger.app}"
OUT_DIR="${3:-$ROOT/out}"
ARCH_LABEL="${4:-macos-arm64}"

SIGN_APP="${MACOS_SIGN_IDENTITY_APP:-}"
SIGN_PKG="${MACOS_SIGN_IDENTITY_INSTALLER:-}"

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
APP="$WORKDIR/root/Applications/WattbikeLogger.app"
xattr -cr "$APP" || true

if [[ -n "$SIGN_APP" ]]; then
  echo "Firma app con: $SIGN_APP"
  codesign --force --deep --options runtime --timestamp --sign "$SIGN_APP" "$APP"
  codesign --verify --deep --strict "$APP"
else
  codesign --force --deep -s - "$APP" 2>/dev/null || true
fi

cp "$PKG_DIR/scripts/postinstall" "$WORKDIR/scripts/postinstall"
chmod 755 "$WORKDIR/scripts/postinstall"

cp "$PKG_DIR/Resources/Welcome.html" "$WORKDIR/resources/"
cp "$PKG_DIR/Resources/Conclusion.html" "$WORKDIR/resources/"
cp "$ROOT/LICENSE" "$WORKDIR/resources/License.txt"

sed "s/VERSION_PLACEHOLDER/${VERSION}/g" "$PKG_DIR/Distribution.xml" > "$WORKDIR/Distribution.xml"

COMPONENT="$WORKDIR/WattbikeLogger-component.pkg"
pkgbuild \
  --root "$WORKDIR/root" \
  --scripts "$WORKDIR/scripts" \
  --component-plist "$PKG_DIR/component.plist" \
  --identifier "com.gianlu.wattbikelogger" \
  --version "$VERSION" \
  --install-location "/" \
  --min-os-version "12.0" \
  ${SIGN_PKG:+--sign "$SIGN_PKG"} \
  "$COMPONENT"

OUT_PKG="$OUT_DIR/WattbikeLogger-${ARCH_LABEL}.pkg"
productbuild \
  --distribution "$WORKDIR/Distribution.xml" \
  --resources "$WORKDIR/resources" \
  --package-path "$WORKDIR" \
  ${SIGN_PKG:+--sign "$SIGN_PKG"} \
  "$OUT_PKG"

# Notarizzazione (richiede APPLE_ID, APPLE_TEAM_ID, APPLE_APP_SPECIFIC_PASSWORD)
if [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" && -n "$SIGN_PKG" ]]; then
  echo "Invio a Apple notarytool…"
  xcrun notarytool submit "$OUT_PKG" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_SPECIFIC_PASSWORD" \
    --wait
  xcrun stapler staple "$OUT_PKG"
  spctl --assess --type install -v "$OUT_PKG" || true
  echo "PKG notarizzato e stapled."
else
  echo "Nota: pkg non notarizzato (Gatekeeper avviserà al download). Vedi scripts/macos/pkg/README-SIGNING.md"
fi

ls -lh "$OUT_PKG"
echo "Creato: $OUT_PKG"
