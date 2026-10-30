#!/usr/bin/env bash
# Crea un .deb installabile (doppio click / apt / Software Install)
# Include regole udev ANT+ nel postinst per plug-and-play.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERSION="${1:-}"
BIN_SRC="${2:-}"
OUT_DIR="${3:-$ROOT/out}"
ARCH_DEB="${4:-amd64}"

if [[ -z "$VERSION" ]]; then
  VERSION="$(python3 -c "from pathlib import Path; import re; t=Path('$ROOT/wattbike_logger/__init__.py').read_text(); print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', t).group(1))")"
fi

if [[ -z "$BIN_SRC" ]]; then
  if [[ -f "$ROOT/dist/WattbikeLogger-linux-x64" ]]; then
    BIN_SRC="$ROOT/dist/WattbikeLogger-linux-x64"
  elif [[ -f "$ROOT/dist/WattbikeLogger" ]]; then
    BIN_SRC="$ROOT/dist/WattbikeLogger"
  else
    echo "Binario Linux mancante in dist/" >&2
    exit 1
  fi
fi

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/wattbike-deb.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

PKG_NAME="wattbike-logger"
PKG_ROOT="$WORKDIR/${PKG_NAME}_${VERSION}_${ARCH_DEB}"
mkdir -p "$PKG_ROOT/DEBIAN" \
  "$PKG_ROOT/usr/local/bin" \
  "$PKG_ROOT/usr/share/applications" \
  "$PKG_ROOT/usr/share/doc/$PKG_NAME" \
  "$PKG_ROOT/lib/udev/rules.d" \
  "$PKG_ROOT/usr/share/wattbike-logger"

install -m 755 "$BIN_SRC" "$PKG_ROOT/usr/local/bin/WattbikeLogger"
install -m 644 "$ROOT/wattbike_logger/resources/42-ant-usb-sticks.rules" \
  "$PKG_ROOT/lib/udev/rules.d/42-ant-usb-sticks.rules"
cp "$ROOT/wattbike_logger/resources/42-ant-usb-sticks.rules" \
  "$PKG_ROOT/usr/share/wattbike-logger/42-ant-usb-sticks.rules"
cp "$ROOT/LICENSE" "$PKG_ROOT/usr/share/doc/$PKG_NAME/copyright"
cat > "$PKG_ROOT/usr/share/doc/$PKG_NAME/changelog.Debian" <<EOF
$PKG_NAME ($VERSION) unstable; urgency=low

  * Release $VERSION

 -- Gianluca <noreply@users.noreply.github.com>  $(date -Ru)
EOF
gzip -9n "$PKG_ROOT/usr/share/doc/$PKG_NAME/changelog.Debian"

cat > "$PKG_ROOT/usr/share/applications/wattbike-logger.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Wattbike Logger
Comment=Wattbike ANT+ → Excel
Exec=/usr/local/bin/WattbikeLogger
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;Sports;
EOF

INSTALLED_SIZE="$(du -sk "$PKG_ROOT/usr" "$PKG_ROOT/lib" 2>/dev/null | awk '{s+=$1} END{print s}')"
cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: $PKG_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH_DEB
Installed-Size: $INSTALLED_SIZE
Maintainer: Gianluca <noreply@users.noreply.github.com>
Depends: libusb-1.0-0
Description: Wattbike ANT+ Logger → Excel/CSV
 Desktop app to log Wattbike ANT+ Bicycle Power data to Excel/CSV.
 Includes udev rules for ANT USB sticks (VID 0fcf).
EOF

cat > "$PKG_ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
  udevadm trigger --subsystem-match=usb --attr-match=idVendor=0fcf --action=add || true
fi
# Aggiungi utente al gruppo plugdev se esiste
if getent group plugdev >/dev/null 2>&1; then
  u="${SUDO_USER:-}"
  if [ -n "$u" ] && [ "$u" != "root" ]; then
    usermod -aG plugdev "$u" 2>/dev/null || true
  fi
fi
exit 0
EOF
chmod 755 "$PKG_ROOT/DEBIAN/postinst"

cat > "$PKG_ROOT/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
exit 0
EOF
chmod 755 "$PKG_ROOT/DEBIAN/prerm"

mkdir -p "$OUT_DIR"
OUT_DEB="$OUT_DIR/WattbikeLogger-linux-x64.deb"
dpkg-deb --root-owner-group --build "$PKG_ROOT" "$OUT_DEB"
ls -lh "$OUT_DEB"
echo "Creato: $OUT_DEB"
