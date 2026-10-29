"""与 GUI 框架无关的系统托盘，不重启图标即可刷新状态。"""

import logging
import os
import platform
import threading

from ui.branding import APP_ID, APP_NAME, ICON_PNG_PATH

log = logging.getLogger("campus_login.tray")


class TrayIcon:
    def __init__(self, on_login=None, on_logout=None, on_show=None,
                 on_quit=None, on_toggle_daemon=None, on_availability=None):
        self._callbacks = dict(login=on_login, logout=on_logout, show=on_show,
                               quit=on_quit, daemon=on_toggle_daemon)
        self._tray = self._thread = None
        self._glib = None
        self._started = False
        self._on_availability = on_availability
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

    def _create_tray(self):
        import pystray
        # 旧 GTK StatusIcon 和 Xorg 后端可能无图标或菜单，不能据此隐藏窗口。
        if self._glib and pystray.Icon.__module__ != "pystray._appindicator":
            raise RuntimeError("Linux 系统托盘需要 AppIndicator 支持")
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
        return pystray.Icon(APP_ID, self._image(), APP_NAME, menu)

    def start(self):
        with self._lock:
            if self._started or self._stopped.is_set():
                return self.available
            self._started = True
            try:
                if platform.system() == "Linux":
                    import gi
                    gi.require_version("Gtk", "3.0")
                    from gi.repository import GLib
                    self._glib = GLib
                    os.environ.setdefault("PYSTRAY_BACKEND", "appindicator")
                    # pywebview owns GTK's main loop. Construct GTK objects there
                    # and attach pystray, rather than starting another GLib loop.
                    GLib.idle_add(self._start_gtk)
                else:
                    self._tray = self._create_tray()
                    self._thread = threading.Thread(target=self._run, name="campus-tray", daemon=True)
                    self._thread.start()
            except Exception:
                log.exception("系统托盘不可用")
                return False
        self._ready.wait(timeout=3)
        if not self._ready.is_set():
            if self._glib:
                # A desktop host can appear later (e.g. a GNOME Shell restart).
                # Keep the window accessible until it actually connects.
                log.warning("未检测到 AppIndicator 托盘宿主，将保留主窗口")
            else:
                self.stop()
            return False
        return self.available

    def _set_available(self, available):
        changed = self.available != available
        self.available = available
        if available:
            self._ready.set()
        if changed and self._on_availability:
            try:
                self._on_availability(available)
            except Exception:
                log.exception("更新托盘可用状态失败")

    def _setup(self, icon):
        if self._stopped.is_set():
            icon.stop()
            return
        icon.visible = True
        if self._glib:
            self._glib.idle_add(self._check_gtk_available)
        else:
            self._set_available(True)

    def _start_gtk(self):
        if self._stopped.is_set():
            self._ready.set()
            return False
        try:
            self._tray = self._create_tray()
            # pystray's public ready event only means GTK has initialized. The
            # native connected property confirms a desktop can expose the menu.
            indicator = self._tray._appindicator
            indicator.connect("connection-changed", self._gtk_connection_changed)
            self._tray.run_detached(setup=self._setup)
        except Exception:
            log.exception("AppIndicator 系统托盘不可用，将保留主窗口")
            self._ready.set()
        return False

    def _gtk_connection_changed(self, indicator, connected):
        self._check_gtk_available()

    def _check_gtk_available(self):
        if self._stopped.is_set():
            return False
        try:
            connected = bool(self._tray._appindicator.get_property("connected"))
            self._set_available(connected and self._tray.visible)
        except Exception:
            self._set_available(False)
            log.warning("无法确认 AppIndicator 托盘连接，将保留主窗口", exc_info=True)
            self._ready.set()
        return False

    def _run(self):
        try:
            self._tray.run(setup=self._setup)
        except Exception:
            log.exception("系统托盘启动失败")
            self._ready.set()
        finally:
            self._set_available(False)

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
        self._set_available(False)
        if self._tray:
            if self._glib:
                self._glib.idle_add(self._stop_icon)
            else:
                self._stop_icon()

    def _stop_icon(self):
        try:
            self._tray.visible = False
            self._tray.stop()
        except Exception:
            log.debug("停止托盘失败", exc_info=True)
        return False
