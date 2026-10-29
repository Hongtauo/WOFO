"""与 GUI 框架无关的系统托盘，不重启图标即可刷新状态。"""

import logging
import threading

from ui.branding import APP_ID, APP_NAME, ICON_PNG_PATH

log = logging.getLogger("campus_login.tray")


class TrayIcon:
    def __init__(self, on_login=None, on_logout=None, on_show=None,
                 on_quit=None, on_toggle_daemon=None):
        self._callbacks = dict(login=on_login, logout=on_logout, show=on_show,
                               quit=on_quit, daemon=on_toggle_daemon)
        self._tray = self._thread = None
        self._ready, self._stopped = threading.Event(), threading.Event()
        self._lock = threading.RLock()
        self._connected = self._daemon_running = self._daemon_enabled = self.available = False

    def _callback(self, name):
        def handle(icon, item):
            callback = self._callbacks.get(name)
            if callback:
                threading.Thread(target=callback, daemon=True).start()
        return handle

    def _image(self):
        from PIL import Image, ImageDraw
        with Image.open(ICON_PNG_PATH) as source:
            image = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(image)
        color = "#69e4c0" if self._connected else "#e4ac69"
        # 同一品牌图标，右下角状态点：绿=连接，橙=离线；白色内点=守护中。
        draw.ellipse((44, 44, 62, 62), fill=color, outline="#111d27", width=3)
        if self._daemon_running:
            draw.ellipse((51, 51, 55, 55), fill="#ffffff")
        return image

    def start(self):
        with self._lock:
            if self._thread:
                return self.available
            try:
                import pystray
                menu = pystray.Menu(
                    pystray.MenuItem("显示主窗口", self._callback("show"), default=True),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("快速登录", self._callback("login")),
                    pystray.MenuItem("登出并暂停守护", self._callback("logout")),
                    pystray.MenuItem(
                        lambda item: "暂停守护" if self._daemon_enabled else "开启守护",
                        self._callback("daemon"),
                    ),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("退出", self._callback("quit")),
                )
                self._tray = pystray.Icon(APP_ID, self._image(), APP_NAME, menu)
                self._thread = threading.Thread(target=self._run, name="campus-tray", daemon=True)
                self._thread.start()
            except Exception:
                log.exception("系统托盘不可用")
                return False
        self._ready.wait(timeout=3)
        if not self._ready.is_set():
            self.stop()
            return False
        return self.available

    def _run(self):
        def setup(icon):
            if self._stopped.is_set():
                icon.stop()
                return
            icon.visible = True
            self.available = True
            self._ready.set()
        try:
            self._tray.run(setup=setup)
        except Exception:
            log.exception("系统托盘启动失败")
            self._ready.set()
        finally:
            self.available = False

    def update_status(self, connected, daemon_running=False, daemon_enabled=None):
        with self._lock:
            enabled = daemon_running if daemon_enabled is None else daemon_enabled
            if (connected, daemon_running, enabled) == (self._connected, self._daemon_running, self._daemon_enabled):
                return
            self._connected, self._daemon_running = connected, daemon_running
            self._daemon_enabled = enabled
            if self._tray and self.available:
                self._tray.icon = self._image()
                self._tray.title = APP_NAME + " · " + ("已连接" if connected else "未连接")
                self._tray.update_menu()

    def notify(self, title, message):
        if self._tray and self.available:
            try:
                self._tray.notify(message, APP_NAME)
            except Exception:
                log.debug("系统通知不可用", exc_info=True)

    def stop(self):
        self._stopped.set()
        self.available = False
        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                log.debug("停止托盘失败", exc_info=True)
