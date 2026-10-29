#!/bin/sh
set -eu

app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYSTRAY_BACKEND="${PYSTRAY_BACKEND:-appindicator}"
exec "$app_dir/app/wofo-login-gui" "$@"
