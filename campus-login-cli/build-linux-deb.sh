#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=${VERSION:-1.0.0}
MACHINE=$(uname -m)
case "$MACHINE" in
    x86_64) DEB_ARCH=amd64 ;;
    aarch64|arm64) DEB_ARCH=arm64 ;;
    *) echo "不支持的架构: $MACHINE" >&2; exit 1 ;;
esac

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "构建机需要 PyInstaller: python3 -m pip install pyinstaller" >&2
    exit 1
fi
if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "构建机缺少 dpkg-deb（Debian/Ubuntu 上安装 dpkg-dev）" >&2
    exit 1
fi

BUILD_ROOT=$(mktemp -d)
trap 'rm -rf "$BUILD_ROOT"' EXIT HUP INT TERM
PACKAGE_ROOT="$BUILD_ROOT/campus-login_${VERSION}_${DEB_ARCH}"
PYINSTALLER_CONFIG_DIR="$BUILD_ROOT/pyinstaller-cache"
export PYINSTALLER_CONFIG_DIR

cd "$PROJECT_DIR"
pyinstaller --noconfirm --clean --onefile --name campus-login campus_login_cli.py

install -D -m 0755 dist/campus-login "$PACKAGE_ROOT/usr/bin/campus-login"
install -D -m 0644 packaging/linux/campus-login.service \
    "$PACKAGE_ROOT/lib/systemd/system/campus-login.service"
install -D -m 0600 packaging/linux/campus-login.default \
    "$PACKAGE_ROOT/etc/default/campus-login"
install -D -m 0644 README.md \
    "$PACKAGE_ROOT/usr/share/doc/campus-login/README.md"
install -d -m 0755 "$PACKAGE_ROOT/DEBIAN"
install -m 0755 packaging/linux/postinst "$PACKAGE_ROOT/DEBIAN/postinst"
install -m 0755 packaging/linux/prerm "$PACKAGE_ROOT/DEBIAN/prerm"
install -m 0755 packaging/linux/postrm "$PACKAGE_ROOT/DEBIAN/postrm"
printf '%s\n' '/etc/default/campus-login' > "$PACKAGE_ROOT/DEBIAN/conffiles"

INSTALLED_SIZE=$(du -sk "$PACKAGE_ROOT" | cut -f1)
cat > "$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: campus-login
Version: $VERSION
Section: net
Priority: optional
Architecture: $DEB_ARCH
Installed-Size: $INSTALLED_SIZE
Maintainer: Campus Login Maintainers
Description: Dr.COM ePortal campus network auto-login client
 Standalone command-line client with connectivity checks, login, logout,
 and a systemd service for automatic reconnection.
EOF

mkdir -p "$PROJECT_DIR/dist"
dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" \
    "$PROJECT_DIR/dist/campus-login_${VERSION}_${DEB_ARCH}.deb"
echo "已生成: dist/campus-login_${VERSION}_${DEB_ARCH}.deb"
