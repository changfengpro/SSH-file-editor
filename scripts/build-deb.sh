#!/usr/bin/env sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=$(tr -d '[:space:]' < "$ROOT_DIR/VERSION")
PACKAGE=sfe
BUILD_ROOT="$ROOT_DIR/build/deb"
PKG_ROOT="$BUILD_ROOT/${PACKAGE}_${VERSION}_all"
DIST_DIR="$ROOT_DIR/dist"
DEB_PATH="$DIST_DIR/${PACKAGE}_${VERSION}_all.deb"

rm -rf "$BUILD_ROOT"
mkdir -p \
  "$PKG_ROOT/DEBIAN" \
  "$PKG_ROOT/usr/bin" \
  "$PKG_ROOT/usr/lib/sfe" \
  "$PKG_ROOT/usr/share/doc/sfe" \
  "$DIST_DIR"

sed "s/@VERSION@/$VERSION/g" "$ROOT_DIR/packaging/debian/control" > "$PKG_ROOT/DEBIAN/control"
install -m 0755 "$ROOT_DIR/packaging/debian/sfe" "$PKG_ROOT/usr/bin/sfe"
install -m 0644 "$ROOT_DIR/sfe.py" "$PKG_ROOT/usr/lib/sfe/sfe.py"
install -m 0644 "$ROOT_DIR/sfe_core.py" "$PKG_ROOT/usr/lib/sfe/sfe_core.py"
install -m 0644 "$ROOT_DIR/README.md" "$PKG_ROOT/usr/share/doc/sfe/README.md"

find "$PKG_ROOT" -type d -exec chmod 0755 {} +
dpkg-deb --build --root-owner-group "$PKG_ROOT" "$DEB_PATH"
printf '%s\n' "$DEB_PATH"
