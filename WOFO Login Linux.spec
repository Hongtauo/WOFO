# -*- mode: python ; coding: utf-8 -*-
"""Ubuntu 22.04 GTK build. Build with scripts/build-linux-gui.sh on Ubuntu."""

import posixpath
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

if sys.platform != "linux":
    raise SystemExit("The Linux GUI must be built on Ubuntu 22.04, not cross-compiled.")

root = Path(SPECPATH)

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "ui/web"), "ui/web"),
        (str(root / "ui/assets"), "ui/assets"),
        *collect_data_files("webview", subdir="js"),
    ],
    hiddenimports=[
        "cryptography",
        "webview.platforms.gtk",
        "pystray._appindicator",
        "gi",
        "gi._gi",
        "gi._gi_cairo",
        "cairo",
        "gi.repository.Gtk",
        "gi.repository.Gdk",
        "gi.repository.Gio",
        "gi.repository.GLib",
        "gi.repository.DBus",
        "gi.repository.WebKit2",
        "gi.repository.Soup",
        "gi.repository.AyatanaAppIndicator3",
    ],
    hookspath=[str(root / "packaging/linux/hooks")],
    hooksconfig={
        "gi": {
            "module-versions": {
                "Gtk": "3.0",
                "Gdk": "3.0",
                "WebKit2": "4.0",
                "JavaScriptCore": "4.0",
                "Soup": "2.4",
                "AyatanaAppIndicator3": "0.1",
            },
            # Use the installed desktop's themes, icons and translations.
            "icons": [],
            "themes": [],
            "languages": [],
        },
    },
    runtime_hooks=[str(root / "packaging/linux/runtime_gtk.py")],
    excludes=[
        "tkinter", "ttkbootstrap", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "webview.platforms.edgechromium", "webview.platforms.winforms",
        "webview.platforms.mshtml", "webview.platforms.cocoa",
        "webview.platforms.qt", "webview.platforms.android",
        "pythonnet", "clr", "clr_loader", "win32api", "win32con",
        "pystray._win32", "pystray._darwin", "pystray._xorg",
        "gi.repository.AppIndicator3",
    ],
    noarchive=False,
    optimize=0,
)

# WebKit launches distro-provided helper processes. Mixing a bundled copy of
# GTK/GLib with the system WebKit subprocesses causes ABI and loader failures.
# Bundle Python and Python extension modules; apt supplies the native GUI stack.
a.exclude_system_libraries()

# The corresponding default hooks redirect native GTK/GIO/pixbuf paths into the
# bundle, which would hide the installed system modules we deliberately use.
system_gui_runtime_hooks = {
    "pyi_rth_gtk", "pyi_rth_gio", "pyi_rth_gdkpixbuf",
}
a.scripts = [entry for entry in a.scripts if entry[0] not in system_gui_runtime_hooks]
a.datas = [
    entry for entry in a.datas
    if not entry[0].startswith(("lib/gdk-pixbuf/", "gio_modules/"))
]

# Drop aliases whose target was a removed system library.
binary_destinations = {entry[0] for entry in a.binaries if entry[2] != "SYMLINK"}
a.binaries = [
    entry for entry in a.binaries
    if entry[2] != "SYMLINK"
    or posixpath.normpath(posixpath.join(posixpath.dirname(entry[0]), entry[1]))
    in binary_destinations
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="wofo-login-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="wofo-login-gui",
)
