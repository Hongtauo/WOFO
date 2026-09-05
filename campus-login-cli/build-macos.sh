#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

BUILD_CACHE=$(mktemp -d)
trap 'rm -rf "$BUILD_CACHE"' EXIT HUP INT TERM
PYINSTALLER_CONFIG_DIR="$BUILD_CACHE/pyinstaller-cache"
export PYINSTALLER_CONFIG_DIR

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "构建机需要 PyInstaller: python3 -m pip install pyinstaller" >&2
    exit 1
fi

pyinstaller --noconfirm --clean --onefile --name campus-login campus_login_cli.py
echo "已生成: dist/campus-login"
