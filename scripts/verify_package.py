"""检查发布 EXE 的代码、静态资源、Windows 图标和版本信息。"""

import struct
import sys
import types
from collections import Counter
from pathlib import Path

import pefile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ui.branding import APP_NAME, APP_TITLE


def normalized(code):
    return code.replace(co_filename="", co_consts=tuple(
        normalized(value) if isinstance(value, types.CodeType) else value
        for value in code.co_consts
    ))


def main():
    executable = ROOT / "dist" / "webview" / f"{APP_NAME}.exe"
    archive = CArchiveReader(str(executable))
    for folder in ("ui/web", "ui/assets"):
        for path in (ROOT / folder).iterdir():
            if path.is_file():
                name = str(path.relative_to(ROOT)).replace("/", "\\")
                assert archive.extract(name) == path.read_bytes(), f"资源不一致：{name}"
    embedded = archive.open_embedded_archive("PYZ.pyz")
    for name in ("app_lifecycle", "ui.api", "ui.branding", "ui.main_window", "ui.tray"):
        source = ROOT / (name.replace(".", "/") + ".py")
        expected = compile(source.read_text(encoding="utf-8"), str(source), "exec")
        assert normalized(embedded.extract(name)) == normalized(expected), f"代码不一致：{name}"
    pe = pefile.PE(str(executable))
    resources = [pe.get_data(item.data.struct.OffsetToData, item.data.struct.Size)
                 for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries if entry.id == 3
                 for group in entry.directory.entries for item in group.directory.entries]
    icon = (ROOT / "ui/assets/wofo-login.ico").read_bytes()
    count = struct.unpack_from("<H", icon, 4)[0]
    icon_images = []
    for index in range(count):
        size, offset = struct.unpack_from("<II", icon, 6 + index * 16 + 8)
        icon_images.append(icon[offset:offset + size])
    assert Counter(resources) == Counter(icon_images), "EXE 图标与自定义 ICO 不一致"
    entries = {}
    for section in pe.FileInfo:
        for info in section:
            for table in getattr(info, "StringTable", []):
                entries.update(table.entries)
    assert entries[b"ProductName"].decode("utf-8") == APP_TITLE
    assert entries[b"FileDescription"].decode("utf-8") == APP_TITLE
    print(f"Verified: current source, web assets, {count} icon sizes, Windows version information.")


if __name__ == "__main__":
    main()
