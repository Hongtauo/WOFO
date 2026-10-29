#!/usr/bin/env bash
# Native Ubuntu 22.04 build; dependencies are installed separately by CI/users.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$repo_root"

if [[ $(uname -s) != Linux ]]; then
    printf '%s\n' 'Build this GUI on Ubuntu 22.04; Linux cross-compilation is unsupported.' >&2
    exit 1
fi
# shellcheck source=/dev/null
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 22.04 ]]; then
    printf '%s\n' 'This package targets Ubuntu 22.04 and must be built on that release.' >&2
    exit 1
fi

python_bin=${PYTHON:-python3}
for tool in "$python_bin" dpkg dpkg-deb dpkg-shlibdeps desktop-file-validate readelf ldd tar sha256sum; do
    command -v "$tool" >/dev/null || { printf 'Missing build tool: %s\n' "$tool" >&2; exit 1; }
done

architecture=$(dpkg --print-architecture)
if [[ $architecture != amd64 ]]; then
    printf 'This release currently supports amd64; got %s.\n' "$architecture" >&2
    exit 1
fi

"$python_bin" - <<'PY'
import sys
if sys.version_info[:2] != (3, 10):
    raise SystemExit("Build with Ubuntu's Python 3.10 in a --system-site-packages venv.")
import gi
import cairo
import PyInstaller
from importlib.metadata import version
from packaging.version import Version
if Version(version("pywebview")) < Version("6.0"):
    raise SystemExit("The Ubuntu GTK build requires pywebview >= 6.0, < 7.")
for name, api in (("Gtk", "3.0"), ("WebKit2", "4.0"), ("Soup", "2.4"),
                  ("AyatanaAppIndicator3", "0.1"), ("DBus", "1.0")):
    gi.require_version(name, api)
    __import__("gi.repository", fromlist=[name])
PY

version=$("$python_bin" -c 'from ui.branding import APP_VERSION; print(APP_VERSION)')
if [[ ! $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'Invalid release version: %s\n' "$version" >&2
    exit 1
fi

build_root="$repo_root/build/linux-gui"
dist_root="$repo_root/dist/linux"
mkdir -p "$build_root" "$dist_root"
build_root=$(realpath "$build_root")
dist_root=$(realpath "$dist_root")
stage=$(mktemp -d "$build_root/package.XXXXXX")
cleanup() {
    # Only remove the exact mktemp tree contained in this project's build dir.
    local resolved
    resolved=$(realpath -m -- "$stage")
    case "$resolved" in
        "$build_root"/package.*) rm -rf -- "$resolved" ;;
        *) printf 'Refusing to clean unexpected staging path: %s\n' "$resolved" >&2 ;;
    esac
}
trap cleanup EXIT

export PYSTRAY_BACKEND=appindicator
"$python_bin" -m PyInstaller --noconfirm --clean \
    --distpath "$dist_root" --workpath "$build_root/pyinstaller" \
    "$repo_root/WOFO Login Linux.spec"

app_dir="$dist_root/wofo-login-gui"
test -x "$app_dir/wofo-login-gui"
test -f "$app_dir/_internal/ui/web/index.html"
test -f "$app_dir/_internal/ui/assets/wofo-login.png"
test -f "$app_dir/_internal/webview/js/api.js"
"$app_dir/wofo-login-gui" --version
desktop-file-validate "$repo_root/packaging/linux/wofo-login.desktop"

# Check every bundled ELF, including _gi/_cairo and the Python standard-library
# extensions. This catches missing native runtime libraries before packaging.
elf_args=()
while IFS= read -r -d '' candidate; do
    if readelf -h "$candidate" >/dev/null 2>&1; then
        dependencies=$(LD_LIBRARY_PATH="$app_dir/_internal${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
            ldd "$candidate" 2>&1 || true)
        if [[ $dependencies == *'not found'* ]]; then
            printf 'Unresolved runtime dependency in %s:\n%s\n' "$candidate" "$dependencies" >&2
            exit 1
        fi
        elf_args+=("-e$candidate")
    fi
done < <(find "$app_dir" -type f -print0)

# Resolve non-bundled ELF dependencies using Ubuntu's dpkg metadata. Libraries
# shipped inside wheel packages have no dpkg ownership, so ignore only that
# case; the ldd check above still verifies that every dependency can load.
mkdir -p "$stage/shlibdeps/debian"
cat > "$stage/shlibdeps/debian/control" <<'CONTROL'
Source: wofo-login-gui
Section: net
Priority: optional
Maintainer: WOFO contributors <noreply@users.noreply.github.com>

Package: wofo-login-gui
Architecture: any
Description: WOFO Login campus network reconnect assistant
CONTROL
shlibdeps=$(cd "$stage/shlibdeps" && dpkg-shlibdeps --ignore-missing-info -O \
    "-l$app_dir/_internal" "${elf_args[@]}")
shlibs=${shlibdeps#shlibs:Depends=}
if [[ -z $shlibs || $shlibs == "$shlibdeps" ]]; then
    printf 'dpkg-shlibdeps returned no usable dependency metadata: %s\n' "$shlibdeps" >&2
    exit 1
fi

package_root="$stage/deb"
mkdir -p "$package_root/DEBIAN" "$package_root/opt/wofo-login-gui" \
    "$package_root/usr/bin" "$package_root/usr/share/applications" \
    "$package_root/usr/share/icons/hicolor/scalable/apps" \
    "$package_root/usr/share/doc/wofo-login-gui"
cp -a "$app_dir/." "$package_root/opt/wofo-login-gui/"
install -m 755 "$repo_root/packaging/linux/wofo-login-gui" "$package_root/usr/bin/wofo-login-gui"
install -m 644 "$repo_root/packaging/linux/wofo-login.desktop" "$package_root/usr/share/applications/wofo-login.desktop"
install -m 644 "$repo_root/ui/assets/wofo-login.svg" "$package_root/usr/share/icons/hicolor/scalable/apps/wofo-login.svg"
install -m 644 "$repo_root/LICENSE" "$package_root/usr/share/doc/wofo-login-gui/copyright"
install -m 644 "$repo_root/packaging/linux/README.txt" "$package_root/usr/share/doc/wofo-login-gui/README"
install -m 755 "$repo_root/packaging/linux/postinst" "$package_root/DEBIAN/postinst"
install -m 755 "$repo_root/packaging/linux/postrm" "$package_root/DEBIAN/postrm"

"$python_bin" - "$package_root" <<'PY'
import sys
from pathlib import Path
from PIL import Image
root = Path(sys.argv[1])
with Image.open("ui/assets/wofo-login.png") as source:
    for size in (64, 128, 256, 512):
        target = root / f"usr/share/icons/hicolor/{size}x{size}/apps/wofo-login.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS).save(target)
PY

installed_size=$(du -sk "$package_root/opt" "$package_root/usr" | awk '{total += $1} END {print total}')
cat > "$package_root/DEBIAN/control" <<CONTROL
Package: wofo-login-gui
Version: $version
Section: net
Priority: optional
Architecture: $architecture
Maintainer: WOFO contributors <noreply@users.noreply.github.com>
Installed-Size: $installed_size
Depends: $shlibs, libgtk-3-0, libwebkit2gtk-4.0-37, gir1.2-gtk-3.0, gir1.2-webkit2-4.0, gir1.2-ayatanaappindicator3-0.1, gir1.2-freedesktop
Recommends: fonts-noto-cjk
Suggests: gnome-shell-extension-appindicator
Homepage: https://github.com/Hongtauo/WOFO
Description: WOFO Login - 佛山大学校园网断线守护
 GTK/WebKit desktop client for Foshan University campus network login and
 automatic reconnection. Includes a Python runtime and uses Ubuntu's WebKit.
CONTROL

printf '%s\n' "$shlibs, libgtk-3-0, libwebkit2gtk-4.0-37, gir1.2-gtk-3.0, gir1.2-webkit2-4.0, gir1.2-ayatanaappindicator3-0.1, gir1.2-freedesktop" \
    > "$package_root/usr/share/doc/wofo-login-gui/runtime-dependencies.txt"

artifact="WOFO-Login_${version}_ubuntu22.04_${architecture}"
dpkg-deb --root-owner-group --build "$package_root" "$dist_root/$artifact.deb"

portable_root="$stage/portable/WOFO-Login"
mkdir -p "$portable_root/app"
cp -a "$app_dir/." "$portable_root/app/"
install -m 755 "$repo_root/packaging/linux/run.sh" "$portable_root/run.sh"
install -m 644 "$repo_root/packaging/linux/README.txt" "$portable_root/README.txt"
install -m 644 "$repo_root/LICENSE" "$portable_root/LICENSE"
install -m 644 "$package_root/usr/share/doc/wofo-login-gui/runtime-dependencies.txt" "$portable_root/runtime-dependencies.txt"
tar -C "$stage/portable" -czf "$dist_root/$artifact.tar.gz" WOFO-Login
(cd "$dist_root" && sha256sum "$artifact.deb" "$artifact.tar.gz" > SHA256SUMS)

dpkg-deb --info "$dist_root/$artifact.deb"
printf '\nGUI binary: %s\nDebian package: %s\nPortable archive: %s\n' \
    "$app_dir/wofo-login-gui" "$dist_root/$artifact.deb" "$dist_root/$artifact.tar.gz"
