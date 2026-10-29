"""界面包按需加载，命令行安装/卸载无需初始化图形界面。"""

__all__ = ["MainWindow", "TrayIcon"]


def __getattr__(name):
    if name == "MainWindow":
        from .main_window import MainWindow
        return MainWindow
    if name == "TrayIcon":
        from .tray import TrayIcon
        return TrayIcon
    raise AttributeError(name)
