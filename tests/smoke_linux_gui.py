"""Exercise the real GTK/WebKit UI with temporary data and simulated networking.

Run on an Ubuntu 22.04 graphical session, or in CI with::

    dbus-run-session -- xvfb-run -a -s '-screen 0 1440x1100x24' \
        .venv/bin/python tests/smoke_linux_gui.py --output-dir dist/linux-smoke

Requires the system GI bindings, GTK 3, WebKit2 4.0/4.1 and python3-gi-cairo.
No credentials, autostart entries, uninstall actions or external network requests
are used. This is a source integration check; the distributable has a separate
startup check so the production entry point does not need a testing back door.
"""

import argparse
import json
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class MemoryCrypto:
    """Only synthetic smoke accounts are ever stored in the temporary database."""

    def encrypt(self, text):
        return text.encode("utf-8")

    def decrypt(self, data):
        return bytes(data).decode("utf-8")


class NoAutoStart:
    def is_enabled(self):
        return False


def bounded_call(function, timeout=10, description="operation"):
    """Keep a missing JS callback or GTK idle task from hanging the check."""
    done = threading.Event()
    result = {}

    def run():
        try:
            result["value"] = function()
        except BaseException as exc:
            result["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError(f"Timed out waiting for {description}")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "linux-smoke")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("This check requires Linux with GTK/WebKit, not a Windows WebView2 session")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "checks": []}

    from config.settings import SettingsManager
    from database.db import Database
    from ui.api import AppApi
    from ui.branding import APP_TITLE, ICON_PNG_PATH
    from ui.main_window import MainWindow

    with tempfile.TemporaryDirectory(prefix="wofo-gtk-smoke-") as directory, \
            patch("database.db.get_crypto", return_value=MemoryCrypto()), \
            patch("ui.api.auth.check_network", return_value=True) as network_mock, \
            patch("ui.api.auth.login", return_value=True) as login_mock, \
            patch("ui.api.auth.logout", return_value=True), \
            patch("ui.main_window.TrayIcon.start", return_value=False):
        settings = SettingsManager(Path(directory), auto_start=NoAutoStart())
        settings.update(daemon_enabled=False, show_notifications=False)
        app = MainWindow(Database(Path(directory) / "accounts.db"), settings)
        window = app.window

        def evaluate(script):
            return bounded_call(lambda: window.evaluate_js(script), description="JavaScript evaluation")

        def wait_for(script, timeout=12):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = evaluate(script)
                if result:
                    return result
                threading.Event().wait(0.08)
            raise AssertionError(f"WebView condition timed out: {script}")

        def gtk_call(function):
            # All access to native GTK widgets happens on the GUI thread.
            from gi.repository import GLib
            done = threading.Event()
            result = {}

            def idle():
                try:
                    result["value"] = function()
                except BaseException as exc:
                    result["error"] = exc
                finally:
                    done.set()
                return False

            GLib.idle_add(idle)
            if not done.wait(10):
                raise TimeoutError("Timed out waiting for GTK main loop")
            if "error" in result:
                raise result["error"]
            return result.get("value")

        def capture(path):
            import gi
            gi.require_foreign("cairo")
            from gi.repository import WebKit2
            done = threading.Event()
            result = {}

            def find_webview(widget):
                if isinstance(widget, WebKit2.WebView):
                    return widget
                for child in getattr(widget, "get_children", lambda: [])():
                    found = find_webview(child)
                    if found is not None:
                        return found
                return None

            def finished(view, task, *_):
                try:
                    surface = view.get_snapshot_finish(task)
                    surface.write_to_png(str(path))
                except BaseException as exc:
                    result["error"] = exc
                finally:
                    done.set()

            def start():
                view = find_webview(window.native)
                if view is None:
                    raise AssertionError("GTK window has no WebKit2.WebView")
                view.get_snapshot(WebKit2.SnapshotRegion.VISIBLE,
                                  WebKit2.SnapshotOptions.NONE, None, finished, None)

            gtk_call(start)
            if not done.wait(12):
                raise TimeoutError("Timed out capturing WebKit snapshot")
            if "error" in result:
                raise result["error"]
            assert path.is_file() and path.stat().st_size > 1000

        def bridge(method, *values):
            # Resolve a real JS -> Python -> JS Promise, including error paths.
            arguments = ",".join(json.dumps(value, ensure_ascii=True) for value in values)
            evaluate("window.__wofoSmokeResult = null; "
                     f"window.pywebview.api.{method}({arguments}).then("
                     "result => { window.__wofoSmokeResult = {result}; }, "
                     "() => { window.__wofoSmokeResult = {error: true}; });")
            result = wait_for("window.__wofoSmokeResult")
            assert not result.get("error"), f"Bridge method failed: {method}"
            return result["result"]

        def checks():
            try:
                assert window.events.loaded.wait(35), "GTK/WebKit page did not load"
                assert window.gui.renderer == "gtkwebkit2", window.gui.renderer
                wait_for("document.getElementById('bridge-banner').hidden")
                assert evaluate("typeof document.getElementById('account-dialog').showModal === 'function'")

                def native_branding():
                    from gi.repository import GdkPixbuf
                    native = window.native
                    assert native.get_title() == APP_TITLE
                    icon = native.get_icon()
                    assert icon is not None, "GTK titlebar icon is missing"
                    expected = GdkPixbuf.Pixbuf.new_from_file(str(ICON_PNG_PATH))
                    assert (icon.get_width(), icon.get_height()) == (
                        expected.get_width(), expected.get_height())
                    assert bytes(icon.get_pixels()) == bytes(expected.get_pixels())
                    return [icon.get_width(), icon.get_height()]

                report["native_icon_size"] = gtk_call(native_branding)
                report["checks"].extend(["gtk_renderer", "native_title", "native_png_icon", "html_bridge"])

                # Create all five accounts through the actual HTML form.
                for number in range(1, 6):
                    wait_for("!document.getElementById('add-account-button').disabled")
                    evaluate("document.getElementById('add-account-button').click()")
                    assert evaluate("document.getElementById('account-dialog').open")
                    evaluate(f"document.getElementById('account-user-id').value = 'smoke-account-{number}';"
                             "document.getElementById('account-password').value = 'synthetic-smoke-password';"
                             "document.getElementById('account-form').requestSubmit();")
                    wait_for("!document.getElementById('account-dialog').open && "
                             f"document.querySelectorAll('#account-list tr').length === {number}")
                    assert evaluate("document.getElementById('account-password').value === ''")
                wait_for("document.getElementById('add-account-button').disabled")
                assert evaluate("document.getElementById('account-count').textContent.trim() === '5 / 5'")
                result = bridge("save_account", {"user_id": "smoke-account-6", "password": "synthetic",
                                                "service": "电信", "is_default": False})
                assert not result["ok"]
                accounts = app.api.get_state()["accounts"]
                assert len(accounts) == 5 and all("password" not in item for item in accounts)
                report["checks"].append("account_forms_and_five_account_limit")

                second = next(item for item in accounts if item["user_id"] == "smoke-account-2")
                selector = f"[data-focus-key=\"select-{second['id']}\"]"
                wait_for(f"!document.querySelector('{selector}').disabled")
                evaluate(f"document.querySelector('{selector}').click()")
                wait_for(f"document.querySelector('{selector}').checked && "
                         f"!document.querySelector('{selector}').disabled")
                assert app.api.get_state()["selected_account_id"] == second["id"]
                reopened = Database(Path(directory) / "accounts.db")
                try:
                    assert AppApi(reopened, settings).get_state()["selected_account_id"] == second["id"]
                finally:
                    reopened.close()

                wait_for("!document.getElementById('daemon-switch').disabled")
                evaluate("document.getElementById('daemon-switch').click()")
                wait_for("document.getElementById('daemon-badge').textContent === '守护中'")
                with app.api._lock:
                    login_mock.reset_mock()
                    network_mock.side_effect = [False, True]
                    try:
                        app.api._check_and_reconnect()
                    finally:
                        network_mock.side_effect = None
                    login_mock.assert_called_once_with("smoke-account-2", "synthetic-smoke-password", "电信")
                report["checks"].extend(["guardian_selection_persists", "daemon_uses_selected_account"])

                # Editing and deleting remain possible when capacity is full.
                fifth = next(item for item in accounts if item["user_id"] == "smoke-account-5")
                edit = f"[data-focus-key=\"edit-{fifth['id']}\"]"
                wait_for(f"!document.querySelector('{edit}').disabled")
                evaluate(f"document.querySelector('{edit}').click()")
                assert evaluate("document.getElementById('account-dialog').open")
                evaluate("document.getElementById('account-user-id').value = 'smoke-account-edited';"
                         "document.getElementById('account-form').requestSubmit();")
                wait_for("!document.getElementById('account-dialog').open && "
                         "document.getElementById('account-list').textContent.includes('smoke-account-edited')")
                assert app._db.get_account(fifth["id"]).password == "synthetic-smoke-password"
                trash = f"[data-focus-key=\"trash-{fifth['id']}\"]"
                wait_for(f"!document.querySelector('{trash}').disabled")
                evaluate(f"document.querySelector('{trash}').click()")
                assert evaluate("document.getElementById('delete-dialog').open")
                evaluate("document.getElementById('delete-form').requestSubmit()")
                wait_for("!document.getElementById('delete-dialog').open && "
                         "document.querySelectorAll('#account-list tr').length === 4 && "
                         "!document.getElementById('add-account-button').disabled")
                report["checks"].append("edit_and_delete_at_capacity")

                evaluate("document.getElementById('settings-nav').click()")
                assert evaluate("document.getElementById('settings-dialog').open")
                assert evaluate("document.getElementById('uninstall-button').disabled")
                assert evaluate("!document.getElementById('tray-settings-note').hidden")
                evaluate("document.getElementById('setting-interval').value = '45';"
                         "document.getElementById('settings-form').requestSubmit();")
                wait_for("!document.getElementById('settings-dialog').open")
                assert settings.get().check_interval == 45
                assert not bridge("hide_window")["ok"]
                report["checks"].extend(["settings_form", "no_tray_keeps_window_visible"])

                wait_for("!document.getElementById('login-button').disabled")
                evaluate("document.getElementById('login-button').click()")
                wait_for("document.getElementById('last-event').textContent.includes('网络已连接')")
                evaluate("document.getElementById('logs-nav').click()")
                wait_for("!document.getElementById('logs-page').hidden && "
                         "document.getElementById('overview-page').hidden && "
                         "document.querySelectorAll('#log-list .log-entry').length > 0")
                assert evaluate("document.getElementById('logs-nav').getAttribute('aria-current') === 'page'")
                assert "synthetic" not in evaluate("document.getElementById('log-list').textContent")
                evaluate("document.getElementById('toast-region').replaceChildren()")
                capture(output / "logs.png")
                wait_for("!document.getElementById('clear-logs-button').disabled")
                evaluate("document.getElementById('clear-logs-button').click()")
                wait_for("document.querySelectorAll('#log-list .log-entry').length === 0 && "
                         "!document.getElementById('logs-empty').hidden")
                assert app.api.get_state()["logs"] == []
                evaluate("document.getElementById('overview-nav').click()")
                wait_for("!document.getElementById('overview-page').hidden && "
                         "document.getElementById('logs-page').hidden")
                evaluate("document.getElementById('toast-region').replaceChildren()")
                assert not evaluate("document.documentElement.scrollWidth > innerWidth")
                capture(output / "overview.png")
                report["checks"].extend(["simulated_connect", "logs_navigation", "logs_clear", "overview_navigation"])

                window.resize(800, 620)
                wait_for("innerWidth <= 800")
                assert not evaluate("document.documentElement.scrollWidth > innerWidth")
                capture(output / "overview-small.png")
                report["checks"].append("small_window_layout")
                report.update(ok=True, renderer=window.gui.renderer,
                              snapshots=["overview.png", "overview-small.png", "logs.png"])
                # Exercise the public bridge's delayed orderly shutdown.
                # Do not poll another JS call after the 150ms close timer starts.
                evaluate("window.pywebview.api.quit_app(); true")
            except BaseException as exc:
                traceback.print_exc()
                report.update(ok=False, error=f"{type(exc).__name__}: {exc}")
                try:
                    capture(output / "failure.png")
                except BaseException:
                    pass
                app._quit_app()

        threading.Thread(target=checks, daemon=True, name="gtk-smoke-checks").start()
        try:
            app.run()
        except BaseException as exc:
            traceback.print_exc()
            report.update(ok=False, error=f"{type(exc).__name__}: {exc}")
        finally:
            app._cleanup()
        if report["ok"]:
            report["ok"] = app._cleaned and app._quitting and app.api._stop.is_set()
            if report["ok"]:
                report["checks"].append("clean_quit")

    serialized = json.dumps(report, ensure_ascii=True, indent=2)
    (output / "report.json").write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
