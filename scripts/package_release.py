"""验证已构建 EXE，并生成附带安装/卸载入口的便携发布包。"""

import shutil
import zipfile
from pathlib import Path

from verify_package import main as verify_package

ROOT = Path(__file__).resolve().parent.parent


def main():
    verify_package()
    release = ROOT / "dist" / "webview"
    shutil.copy2(ROOT / "scripts/install.cmd", release / "安装.cmd")
    shutil.copy2(ROOT / "scripts/uninstall.cmd", release / "卸载.cmd")
    (release / "使用说明.txt").write_text(
        "WOFO Login · 佛山大学校园网断线守护\n\n"
        "启动：直接运行 WOFO Login.exe。\n"
        "安装（可选）：双击 安装.cmd，无需管理员权限。\n"
        "安装后会提供桌面、开始菜单和 Windows‘已安装的应用’入口。\n\n"
        "卸载：打开程序偏好设置，点击‘卸载 WOFO Login’。\n"
        "也可先从托盘退出主程序，再双击 卸载.cmd；已安装版可从 Windows 卸载。\n"
        "卸载默认保留账号、密码密钥和设置，只有单独确认才删除数据。\n"
        "便携版卸载只移除当前 EXE，不删除同目录其他文件。\n\n"
        "数据目录：%USERPROFILE%/.config/campus_login/\n"
        "运行环境：Windows + Microsoft Edge WebView2 Runtime。\n",
        encoding="utf-8-sig",
    )
    members = ("WOFO Login.exe", "安装.cmd", "卸载.cmd", "使用说明.txt")
    destination = ROOT / "dist" / "WOFO Login.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in members:
            archive.write(release / name, f"WOFO Login/{name}")
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [f"WOFO Login/{name}" for name in members]
    print(f"Release ready: {destination}")


if __name__ == "__main__":
    main()
