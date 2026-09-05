#!/usr/bin/env python3
"""
校园网自动登录器
入口文件
"""

import logging
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

# 导入并运行 UI
from ui.main_window import MainWindow


def main():
    """主函数"""
    try:
        app = MainWindow()
        app.run()
    except KeyboardInterrupt:
        print("\n已退出")
    except Exception as e:
        logging.error(f"程序异常退出: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
