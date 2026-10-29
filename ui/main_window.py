"""pywebview 窗口与系统托盘的生命周期管理。"""

import logging
import platform
from pathlib import Path

from config.settings import get_settings
from database.db import get_db
from ui.api import AppApi
from ui.branding import APP_ID, APP_NAME, APP_TITLE, ICON_PATH, ICON_PNG_PATH
from ui.tray import TrayIcon

log = logging.getLogger("campus_login.ui")


class MainWindow:
    def __init__(self, db=None, settings=None):
        try:
            import webview
        except ImportError as exc:
            raise RuntimeError("缺少 pywebview，请先运行 python -m pip install -r requirements.txt") from exc
        self._webview = webview
        self._db = db if db is not None else get_db()
        self._settings = settings if settings is not None else get_settings()
        self._quitting = self._cleaned = False
        self._native_icon = None
        self._set_application_id()
        self.api = AppApi(
            self._db, self._settings, on_hide=self._hide_window,
            on_quit=self._quit_app, on_status=self._update_tray, on_notify=self._notify,
            on_uninstall=self._uninstall_app,
        )
        # PyInstaller 解包后 __file__ 也指向 ui 目录。
        index = Path(__file__).resolve().parent / "web" / "index.html"
        if not index.is_file():
            raise FileNotFoundError(f"找不到界面文件：{index}；打包时需包含 ui/web")
        self.window = webview.create_window(
            APP_TITLE, url=str(index), js_api=self.api,
            width=1060, height=840, min_size=(800, 620),
            background_color="#10141c", text_select=False,
        )
        self.tray = TrayIcon(
            on_login=self._tray_login, on_logout=self._tray_logout,
            on_show=self._show_window, on_quit=self._quit_app,
            on_toggle_daemon=self._toggle_daemon,
            on_availability=self._tray_availability_changed,
        )
        # 此事件同步运行在 GUI 线程，原生窗口已创建但尚未显示。
        self.window.events.before_show += self._apply_native_icon
        self.window.events.closing += self._on_closing
        self.window.events.closed += self._cleanup

    @staticmethod
    def _set_application_id():
        system = platform.system()
        if system == "Linux":
            try:
                import gi
                gi.require_version("GLib", "2.0")
                from gi.repository import GLib
                # GTK 3 uses prgname for Wayland app_id. Match the installed
                # wofo-login.desktop so the shell can find our launcher/icon.
                GLib.set_prgname("wofo-login")
                GLib.set_application_name(APP_NAME)
            except Exception:
                log.warning("无法设置 Linux 应用标识", exc_info=True)
            return
        if system != "Windows":
            return
        try:
            import ctypes
            setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
            setter.argtypes = [ctypes.c_wchar_p]
            setter.restype = ctypes.c_long
            if setter(APP_ID) < 0:
                log.warning("无法设置 Windows 应用标识")
        except Exception:
            log.warning("无法设置 Windows 应用标识", exc_info=True)

    def _apply_native_icon(self):
        system = platform.system()
        if system == "Linux":
            try:
                native = self.window.native
                if native is not None:
                    native.set_icon_from_file(str(ICON_PNG_PATH))
                    native.set_wmclass(APP_ID, APP_ID)
                    native.set_title(APP_TITLE)
            except Exception:
                log.warning("无法设置 Linux 主窗口图标", exc_info=True)
            return
        if system != "Windows":
            return
        try:
            from System.Drawing import Icon
            native = self.window.native
            if native is not None:
                # 保存引用，避免 .NET 图标在窗口生命周期内被提前释放。
                self._native_icon = Icon(str(ICON_PATH))
                native.Icon = self._native_icon
                native.ShowIcon = True
                native.Text = APP_TITLE
        except Exception:
            log.warning("无法设置主窗口图标：%s", ICON_PATH, exc_info=True)

    def _start_background(self):
        if self._cleaned:
            return
        available = self.tray.start()
        if self._cleaned:
            self.tray.stop()
            return
        self.api._set_tray_available(available)
        self.api._start()
        if available and self._settings.get().start_minimized and not self._quitting and not self._cleaned:
            self.window.hide()

    def _tray_availability_changed(self, available):
        if self._cleaned or self._quitting:
            return
        self.api._set_tray_available(available)
        if not available:
            # GNOME 的托盘扩展可能被禁用或重启；此时应恢复窗口以保留退出入口。
            self._show_window()

    def _update_tray(self, connected, active):
        self.tray.update_status(connected, active, self._settings.get().daemon_enabled)

    def _notify(self, title, message):
        self.tray.notify(title, message)

    def _tray_result(self, result):
        if not result["ok"]:
            with self.api._lock:
                self.api._event(result["message"], "error")
            self._show_window()
            self.tray.notify(APP_NAME, result["message"])

    def _tray_login(self):
        self._tray_result(self.api.login())

    def _tray_logout(self):
        self._tray_result(self.api.logout())

    def _toggle_daemon(self):
        self._tray_result(self.api.toggle_daemon(not self.api.get_state()["daemon"]["enabled"]))

    def _hide_window(self):
        if not self.tray.available:
            raise RuntimeError("系统托盘不可用")
        self.window.hide()

    def _show_window(self):
        self.window.show()
        self.window.restore()

    def _on_closing(self):
        if self._quitting:
            return True
        if self.tray.available:
            self.window.hide()
            return False
        self._quitting = True
        return True

    def _quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        self.window.destroy()

    def _uninstall_app(self):
        from app_lifecycle import run_uninstall
        result = run_uninstall()
        if result == 0:
            self._quit_app()
        return result

    def _cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self.api._close()
        self.tray.stop()
        # 进行中的 HTTP 请求结束前不关闭共享数据库。
        worker = self.api._thread
        if not worker or not worker.is_alive():
            self._db.close()

    def run(self):
        try:
            system = platform.system()
            gui = {"Windows": "edgechromium", "Linux": "gtk"}.get(system)
            import inspect
            options = {"gui": gui, "debug": False}
            if "icon" in inspect.signature(self._webview.start).parameters:
                options["icon"] = str(ICON_PATH if system == "Windows" else ICON_PNG_PATH)
            self._webview.start(self._start_background, **options)
        finally:
            self._cleanup()
