"""
系统托盘模块
跨平台托盘图标和右键菜单
"""

import logging
from typing import Callable

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

log = logging.getLogger("campus_login.tray")


class TrayIcon:
    """系统托盘图标管理器"""

    def __init__(self, parent: ttk.Window, on_login: Callable = None,
                 on_logout: Callable = None, on_show: Callable = None,
                 on_quit: Callable = None, on_toggle_daemon: Callable = None):
        self.parent = parent
        self.on_login = on_login
        self.on_logout = on_logout
        self.on_show = on_show
        self.on_quit = on_quit
        self.on_toggle_daemon = on_toggle_daemon

        self._tray = None
        self._connected = False
        self._daemon_running = False
        self._available = True

        # 尝试导入 pystray
        try:
            import pystray
            from pystray import MenuItem
            self._pystray = pystray
            self._MenuItem = MenuItem
            self._use_pystray = True
        except ImportError as e:
            log.warning(f"托盘功能不可用: {e}")
            self._use_pystray = False
            self._available = False

    def _create_icon_image(self, daemon_running: bool = False):
        """创建托盘图标图像"""
        import PIL.Image
        import PIL.ImageDraw

        # 根据状态选择颜色
        if daemon_running:
            color = '#4CAF50'  # 绿色 - 守护运行中
        elif self._connected:
            color = '#2196F3'  # 蓝色 - 已连接
        else:
            color = '#F44336'  # 红色 - 未连接

        img = PIL.Image.new('RGBA', (64, 64), (255, 255, 255, 0))
        draw = PIL.ImageDraw.Draw(img)

        # 画圆形
        draw.ellipse([4, 4, 60, 60], fill=color)

        # 添加网络图标样式
        if daemon_running:
            # 守护模式：在圆中画一个循环箭头
            draw.ellipse([22, 22, 42, 42], fill='white')
        elif self._connected:
            # 已连接：画一个对勾
            draw.line([24, 32, 30, 38], fill='white', width=3)
            draw.line([30, 38, 42, 24], fill='white', width=3)

        return img

    def _setup_pystray(self):
        """设置 pystray 托盘"""
        if not self._use_pystray:
            return

        img = self._create_icon_image(self._daemon_running)

        # 构建菜单文本
        daemon_text = "关闭守护" if self._daemon_running else "启用守护"

        menu = self._pystray.Menu(
            self._MenuItem("显示主窗口", self._handle_show),
            self._pystray.Menu.SEPARATOR,
            self._MenuItem("快速登录", self._handle_login),
            self._MenuItem("登出", self._handle_logout),
            self._pystray.Menu.SEPARATOR,
            self._MenuItem(daemon_text, self._handle_toggle_daemon),
            self._pystray.Menu.SEPARATOR,
            self._MenuItem("退出", self._handle_quit)
        )

        self._tray = self._pystray.Icon(
            "campus_login",
            img,
            "校园网自动登录",
            menu
        )

        # 设置双击事件
        self._tray.on_double_click = self._handle_show

    def _handle_show(self, icon=None, item=None):
        """显示主窗口"""
        if self.on_show:
            self.on_show()
        self.hide()

    def _handle_login(self, icon=None, item=None):
        """快速登录"""
        if self.on_login:
            self.on_login()

    def _handle_logout(self, icon=None, item=None):
        """登出"""
        if self.on_logout:
            self.on_logout()

    def _handle_toggle_daemon(self, icon=None, item=None):
        """切换守护"""
        if self.on_toggle_daemon:
            self.on_toggle_daemon()

    def _handle_quit(self, icon=None, item=None):
        """退出程序"""
        if self.on_quit:
            self.on_quit()
        self.hide()

    def show(self):
        """显示托盘图标"""
        if self._tray:
            try:
                if hasattr(self._tray, 'visible') and self._tray.visible:
                    return
                import threading
                self._tray_thread = threading.Thread(target=self._run_tray, daemon=True)
                self._tray_thread.start()
            except Exception as e:
                log.error(f"托盘启动失败: {e}")

    def _run_tray(self):
        """运行托盘（阻塞）"""
        if self._tray:
            try:
                self._tray.run()
            except Exception:
                pass

    def hide(self):
        """隐藏托盘图标"""
        if self._tray:
            try:
                self._tray.visible = False
                self._tray.stop()
            except Exception:
                pass

    def update_status(self, connected: bool, daemon_running: bool = None):
        """更新状态"""
        if daemon_running is not None:
            self._daemon_running = daemon_running

        if self._connected != connected or self._daemon_running != daemon_running:
            self._connected = connected
            if self._tray:
                try:
                    self.hide()
                    self._setup_pystray()
                    self.show()
                except Exception:
                    pass

    def set_daemon_status(self, running: bool):
        """设置守护状态并更新托盘"""
        if self._daemon_running != running:
            self._daemon_running = running
            if self._tray:
                try:
                    self.hide()
                    self._setup_pystray()
                    self.show()
                except Exception:
                    pass

    def notify(self, title: str, message: str):
        """显示通知"""
        if self._tray:
            try:
                self._tray.notify(message, title)
            except Exception:
                pass

    def minimize_to_tray(self):
        """最小化到托盘"""
        self.parent.withdraw()
        try:
            self._setup_pystray()
            self.show()
        except Exception as e:
            log.error(f"托盘启动失败: {e}")

    def restore_from_tray(self):
        """从托盘恢复"""
        self.hide()
        self.parent.deiconify()
        self.parent.focus()
