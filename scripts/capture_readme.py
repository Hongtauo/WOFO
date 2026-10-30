"""Capture the real Windows WebView using disposable, clearly fictional data.

Run with .venv/Scripts/python.exe scripts/capture_readme.py. No saved accounts,
encryption keys, autostart entries or browser profiles are used. Authentication
and connectivity are simulated; the local WebView loads only bundled UI assets.
"""

import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import SettingsManager
from database.db import Database
from ui.branding import ICON_PATH
from ui.main_window import MainWindow


class DemoCrypto:
    def encrypt(self, value):
        return value.encode("utf-8")

    def decrypt(self, value):
        return bytes(value).decode("utf-8")


class NoAutoStart:
    def is_enabled(self):
        return False


def main():
    if sys.platform != "win32":
        raise SystemExit("Run this capture on Windows with WebView2 installed.")
    destination = ROOT / "docs/images/main-window.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = ROOT / "tests/.tmp"
    temporary_root.mkdir(exist_ok=True)
    outcome = {}

    def demo_tray_start(tray):
        tray.available = True
        return True

    with tempfile.TemporaryDirectory(dir=temporary_root) as directory, \
            patch("database.db.get_crypto", return_value=DemoCrypto()), \
            patch("ui.api.auth.check_network", return_value=True), \
            patch("ui.api.auth.login", return_value=True), \
            patch("ui.api.auth.logout", return_value=True), \
            patch("ui.main_window.TrayIcon.start", demo_tray_start):
        directory = Path(directory)
        settings = SettingsManager(directory, auto_start=NoAutoStart())
        settings.update(daemon_enabled=True, show_notifications=False)
        db = Database(directory / "accounts.db")
        for name, service, selected in (("demo-student-01", "电信", True),
                                         ("demo-student-02", "移动", False)):
            assert db.add_account(name, "dummy-preview-only", service, set_default=selected)
        app = MainWindow(db, settings)
        window = app.window
        assert app.api.login()["ok"]

        def capture():
            try:
                if not window.events.loaded.wait(25):
                    raise TimeoutError("The demonstration page did not load")
                window.resize(1120, 900)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if window.evaluate_js("""document.getElementById('bridge-banner').hidden &&
                        document.getElementById('daemon-badge').textContent === '守护中' &&
                        document.querySelectorAll('#account-list tr').length === 2 &&
                        document.getElementById('status-heading').textContent.includes('已连接') &&
                        document.fonts.status === 'loaded' && innerWidth >= 1100"""):
                        break
                    threading.Event().wait(0.1)
                else:
                    raise TimeoutError("The demonstration state did not settle")
                visible_accounts = window.evaluate_js(
                    "Array.from(document.querySelectorAll('#account-list tr')).map(row => row.innerText)")
                assert len(visible_accounts) == 2
                assert all("demo-student-0" in row for row in visible_accounts)
                assert "dummy-preview-only" not in window.evaluate_js("document.body.innerText")
                window.evaluate_js("document.getElementById('toast-region').replaceChildren()")
                viewport = window.evaluate_js("""({width:innerWidth, height:innerHeight,
                    overflow:document.documentElement.scrollWidth > innerWidth,
                    accountBottom:document.getElementById('accounts-section').getBoundingClientRect().bottom})""")
                assert not viewport["overflow"] and viewport["accountBottom"] <= viewport["height"]

                from System import Action
                from System.IO import FileStream, FileMode
                from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
                tasks = []
                stream = FileStream(str(destination), FileMode.Create)
                try:
                    window.native.Invoke(Action(lambda: tasks.append(
                        window.native.webview.CoreWebView2.CapturePreviewAsync(
                            CoreWebView2CapturePreviewImageFormat.Png, stream))))
                    if not tasks[0].Wait(10000):
                        raise TimeoutError("Screenshot capture timed out")
                finally:
                    stream.Dispose()
                outcome.update(ok=True, viewport=viewport)
            except Exception as exc:
                outcome.update(ok=False, error=f"{type(exc).__name__}: {exc}")
            finally:
                app._quit_app()

        threading.Thread(target=capture, daemon=True, name="readme-capture").start()
        try:
            app._webview.start(app._start_background, gui="edgechromium", debug=False,
                               icon=str(ICON_PATH), storage_path=str(directory / "webview"))
        finally:
            app._cleanup()
    print(outcome)
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
