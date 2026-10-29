#!/usr/bin/env python3
"""
WOFO Login · 佛山大学校园网断线守护
入口文件
"""

import logging
import argparse
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def main(argv=None):
    """主函数"""
    from ui.branding import APP_NAME, APP_TITLE, APP_VERSION

    parser = argparse.ArgumentParser(prog=APP_NAME, description=APP_TITLE)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--install", action="store_true", help="安装到当前用户并添加 Windows 卸载入口")
    modes.add_argument("--uninstall", action="store_true", help="打开卸载向导（默认保留账号数据）")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    args = parser.parse_args(argv)
    try:
        if args.install:
            from app_lifecycle import run_install
            return run_install()
        if args.uninstall:
            from app_lifecycle import run_uninstall
            return run_uninstall()
        from ui.main_window import MainWindow
        app = MainWindow()
        app.run()
        return 0
    except KeyboardInterrupt:
        print("\n已退出")
        return 0
    except Exception as e:
        logging.error(f"程序异常退出: {e}", exc_info=True)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                f"程序无法启动：{e}\n\n请确认已安装依赖和 Microsoft Edge WebView2 Runtime。",
                APP_NAME, 0x10,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
