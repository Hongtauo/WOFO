"""窗口、系统托盘与打包资源共用的应用标识。"""

from pathlib import Path

APP_NAME = "WOFO Login"
APP_TITLE = "WOFO Login · 佛山大学校园网断线守护"
APP_DISPLAY_NAME = APP_TITLE
APP_ID = "WOFO.Login"
APP_VERSION = "1.0.1"

# 源码运行和 PyInstaller 解包后的目录结构一致。
ASSET_DIR = Path(__file__).resolve().parent / "assets"
ICON_PATH = ASSET_DIR / "wofo-login.ico"
ICON_PNG_PATH = ASSET_DIR / "wofo-login.png"
