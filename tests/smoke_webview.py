"""手动运行的 Windows WebView2 集成检查，使用临时账号和模拟网络。

    .venv/Scripts/python.exe tests/smoke_webview.py

打开一个临时窗口，检查真实桥接与表单，保存界面截图后自动退出。
"""

import json
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
from ui.api import AppApi
from ui.branding import APP_TITLE, ICON_PATH
from ui.main_window import MainWindow
from test_api import _MemoryCrypto


class NoAutoStart:
    def is_enabled(self):
        return False


def main():
    temporary_root = ROOT / "tests" / ".tmp"
    temporary_root.mkdir(exist_ok=True)
    report = {"ok": False}
    with tempfile.TemporaryDirectory(dir=temporary_root) as directory, \
            patch("database.db.get_crypto", return_value=_MemoryCrypto()), \
            patch("ui.api.auth.check_network", return_value=True) as network_mock, \
            patch("ui.api.auth.login", return_value=True) as login_mock, \
            patch("ui.api.auth.logout", return_value=True):
        settings = SettingsManager(Path(directory), auto_start=NoAutoStart())
        settings.update(daemon_enabled=False, show_notifications=False)
        app = MainWindow(Database(Path(directory) / "accounts.db"), settings)
        window = app.window

        def wait_for(script, timeout=12):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                result = window.evaluate_js(script)
                if result:
                    return result
                threading.Event().wait(0.1)
            raise AssertionError(f"WebView condition timed out: {script}")

        def capture(path):
            from System import Action
            from System.IO import FileStream, FileMode
            from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
            task_holder = []
            stream = FileStream(str(path), FileMode.Create)
            try:
                window.native.Invoke(Action(lambda: task_holder.append(
                    window.native.webview.CoreWebView2.CapturePreviewAsync(
                        CoreWebView2CapturePreviewImageFormat.Png, stream,
                    )
                )))
                task_holder[0].Wait(10000)
            finally:
                stream.Dispose()

        def checks():
            try:
                if not window.events.loaded.wait(25):
                    raise AssertionError("WebView2 page did not load")
                # 在 GUI 线程确认真实 WinForms 标题、标题栏图标和资源像素，
                # 防止只修改网页图标或依赖 Python/pywebview 的默认图标。
                def check_native_branding():
                    from System.Drawing import Icon
                    from System.Drawing.Imaging import ImageFormat
                    assert window.title == APP_TITLE
                    assert window.native.Text == APP_TITLE
                    assert window.native.ShowIcon
                    assert window.native.Icon is not None
                    assert app._native_icon is not None
                    expected_icon = Icon(str(ICON_PATH))
                    actual = window.native.Icon.ToBitmap()
                    expected = expected_icon.ToBitmap()
                    try:
                        assert (actual.Width, actual.Height) == (expected.Width, expected.Height)
                        assert all(actual.GetPixel(x, y).ToArgb() == expected.GetPixel(x, y).ToArgb()
                                   for y in range(actual.Height) for x in range(actual.Width))
                        icon_capture = ROOT / "dist" / "webview-native-icon.png"
                        icon_capture.parent.mkdir(exist_ok=True)
                        actual.Save(str(icon_capture), ImageFormat.Png)
                        report.update(native_title=window.native.Text,
                                      native_icon_size=[actual.Width, actual.Height],
                                      native_icon=str(icon_capture))
                    finally:
                        actual.Dispose()
                        expected.Dispose()
                        expected_icon.Dispose()

                from System import Action
                window.native.Invoke(Action(check_native_branding))
                wait_for("document.getElementById('bridge-banner').hidden")
                assert not window.evaluate_js("document.querySelector('.sidebar nav').innerText.includes('我的账号')")
                assert window.evaluate_js("document.querySelector('#accounts-section .section-header p').textContent.trim() === '选择一个校园网账号进行守护。'")
                window.evaluate_js("document.getElementById('add-account-button').click()")
                assert window.evaluate_js("document.getElementById('account-dialog').open")
                window.evaluate_js("""
                    document.getElementById('account-user-id').value = '20260001';
                    document.getElementById('account-password').value = 'integration-demo-only';
                    document.getElementById('account-form').requestSubmit();
                """)
                wait_for("!document.getElementById('account-dialog').open && document.querySelectorAll('#account-list tr').length === 1")
                assert window.evaluate_js("document.getElementById('account-password').value === ''")
                assert "password" not in app.api.get_state()["accounts"][0]
                wait_for("!document.getElementById('add-account-button').disabled")
                window.evaluate_js("document.getElementById('add-account-button').click()")
                window.evaluate_js("""
                    document.getElementById('account-user-id').value = '20260002';
                    document.getElementById('account-password').value = 'integration-second-only';
                    document.getElementById('account-service').value = '移动';
                    const defaultOption = document.getElementById('account-default');
                    if (defaultOption) defaultOption.checked = false;
                    document.getElementById('account-form').requestSubmit();
                """)
                wait_for("!document.getElementById('account-dialog').open && document.querySelectorAll('#account-list tr').length === 2")
                accounts = app.api.get_state()["accounts"]
                first = next(account for account in accounts if account["user_id"] == "20260001")
                second = next(account for account in accounts if account["user_id"] == "20260002")
                assert app._db.get_default_account().id == first["id"]
                wait_for("!document.getElementById('daemon-switch').disabled")
                window.evaluate_js("document.getElementById('daemon-switch').click()")
                wait_for("document.getElementById('daemon-badge').textContent === '守护中'")
                wait_for(f"!document.querySelector('[data-focus-key=\"select-{second['id']}\"]').disabled")
                window.evaluate_js(f"document.querySelector('[data-focus-key=\"select-{second['id']}\"]').click()")
                wait_for(f"document.querySelector('[data-focus-key=\"select-{second['id']}\"]').checked && !document.querySelector('[data-focus-key=\"select-{second['id']}\"]').disabled")
                assert app.api.get_state()["selected_account_id"] == second["id"]
                assert app._db.get_default_account().id == second["id"]
                # 重新打开数据库和桥接层，验证守护选择可以跨进程恢复。
                reopened = Database(Path(directory) / "accounts.db")
                try:
                    restored = AppApi(reopened, settings)
                    assert restored.get_state()["selected_account_id"] == second["id"]
                finally:
                    reopened.close()
                # 通过真实守护路径模拟一次断线；锁定桥接层，隔离后台定时检测。
                with app.api._lock:
                    login_mock.reset_mock()
                    network_mock.side_effect = [False, True]
                    try:
                        app.api._check_and_reconnect()
                    finally:
                        network_mock.side_effect = None
                    login_mock.assert_called_once_with(
                        "20260002", "integration-second-only", "移动",
                    )
                wait_for("!document.getElementById('settings-nav').disabled")
                window.evaluate_js("document.getElementById('settings-nav').click()")
                assert window.evaluate_js("document.getElementById('settings-dialog').open")
                assert window.evaluate_js("document.getElementById('uninstall-button').disabled")
                window.evaluate_js("""
                    document.getElementById('setting-interval').value = '45';
                    document.getElementById('settings-form').requestSubmit();
                """)
                wait_for("!document.getElementById('settings-dialog').open")
                assert settings.get().check_interval == 45
                # 仅模拟卸载回调，验证界面锁定/取消恢复；不调用真正的卸载或注册表。
                window.evaluate_js("document.getElementById('settings-nav').click()")
                assert window.evaluate_js("document.getElementById('uninstall-button').disabled")
                wizard_open = threading.Event()
                wizard_cancel = threading.Event()

                def mock_uninstall_wizard():
                    wizard_open.set()
                    wizard_cancel.wait(8)

                with patch.object(sys, "frozen", True, create=True), \
                        patch.object(app.api, "_on_uninstall", mock_uninstall_wizard):
                    wait_for("!document.getElementById('uninstall-button').disabled")
                    window.evaluate_js("document.getElementById('uninstall-button').click()")
                    assert wizard_open.wait(2)
                    wait_for("document.getElementById('settings-submit').disabled && document.getElementById('uninstall-button').disabled")
                    wizard_cancel.set()
                    wait_for("!document.getElementById('settings-submit').disabled && !document.getElementById('uninstall-button').disabled")
                wait_for("document.getElementById('uninstall-button').disabled")
                window.evaluate_js("document.querySelector('#settings-dialog [data-close-dialog]').click()")
                wait_for("!document.getElementById('settings-dialog').open")
                wait_for("!document.getElementById('login-button').disabled")
                window.evaluate_js("document.getElementById('login-button').click()")
                wait_for("document.getElementById('last-event').textContent.includes('网络已连接')")

                # 填充到五个账号，检查容量限制不会妨碍既有账号编辑和删除。
                for number in (3, 4, 5):
                    result = app.api.save_account({
                        "user_id": f"2026000{number}", "password": "integration-capacity-only",
                        "service": "电信", "is_default": False,
                    })
                    assert result["ok"], result["message"]
                wait_for("document.querySelectorAll('#account-list tr').length === 5 && document.getElementById('add-account-button').disabled")
                assert window.evaluate_js("document.getElementById('account-count').textContent.trim() === '5 / 5'")
                window.evaluate_js("document.getElementById('add-account-button').click()")
                assert not window.evaluate_js("document.getElementById('account-dialog').open")
                fifth = next(account for account in app.api.get_state()["accounts"]
                             if account["user_id"] == "20260005")
                assert not window.evaluate_js(f"document.querySelector('[data-focus-key=\"edit-{fifth['id']}\"]').disabled")
                window.evaluate_js(f"document.querySelector('[data-focus-key=\"edit-{fifth['id']}\"]').click()")
                assert window.evaluate_js("document.getElementById('account-dialog').open")
                window.evaluate_js("""
                    document.getElementById('account-user-id').value = '20260005-edited';
                    document.getElementById('account-form').requestSubmit();
                """)
                wait_for("!document.getElementById('account-dialog').open && document.getElementById('account-list').textContent.includes('20260005-edited')")
                assert app._db.get_account(fifth["id"]).password == "integration-capacity-only"
                wait_for(f"!document.querySelector('[data-focus-key=\"trash-{fifth['id']}\"]').disabled")
                window.evaluate_js(f"document.querySelector('[data-focus-key=\"trash-{fifth['id']}\"]').click()")
                assert window.evaluate_js("document.getElementById('delete-dialog').open")
                window.evaluate_js("document.getElementById('delete-form').requestSubmit()")
                wait_for("!document.getElementById('delete-dialog').open && document.querySelectorAll('#account-list tr').length === 4 && !document.getElementById('add-account-button').disabled")
                for account in app.api.get_state()["accounts"]:
                    if account["id"] not in (first["id"], second["id"]):
                        assert app.api.delete_account(account["id"])["ok"]
                wait_for("document.querySelectorAll('#account-list tr').length === 2")

                window.evaluate_js("document.getElementById('logs-nav').click()")
                wait_for("!document.getElementById('logs-page').hidden && document.getElementById('overview-page').hidden")
                wait_for("document.querySelectorAll('#log-list .log-entry').length > 0")
                assert window.evaluate_js("document.getElementById('logs-nav').getAttribute('aria-current') === 'page'")
                assert "integration-" not in window.evaluate_js("document.getElementById('log-list').textContent")
                wait_for("!document.getElementById('clear-logs-button').disabled")
                window.evaluate_js("document.getElementById('clear-logs-button').click()")
                wait_for("document.querySelectorAll('#log-list .log-entry').length === 0 && !document.getElementById('logs-empty').hidden")
                assert app.api.get_state()["logs"] == []
                window.evaluate_js("document.getElementById('overview-nav').click()")
                wait_for("!document.getElementById('overview-page').hidden && document.getElementById('logs-page').hidden")
                wait_for("!document.getElementById('login-button').disabled")
                previous_event = app.api.get_state()["last_event"]["id"]
                window.evaluate_js("document.getElementById('login-button').click()")
                wait_for("!document.getElementById('login-button').disabled")
                assert app.api.get_state()["last_event"]["id"] > previous_event
                window.evaluate_js("document.getElementById('logs-nav').click()")
                wait_for("document.getElementById('log-list').textContent.includes('网络已连接')")
                # 等待临时 toast 淡出，使截图呈现稳定的产品界面。
                window.evaluate_js("document.getElementById('toast-region').replaceChildren()")
                logs_screenshot = ROOT / "dist" / "webview-logs-preview.png"
                logs_screenshot.parent.mkdir(exist_ok=True)
                capture(logs_screenshot)
                window.evaluate_js("document.getElementById('overview-nav').click()")
                wait_for("!document.getElementById('overview-page').hidden && document.getElementById('logs-page').hidden")
                screenshot = ROOT / "dist" / "webview-preview.png"
                screenshot.parent.mkdir(exist_ok=True)
                capture(screenshot)
                report.update(ok=True, renderer=window.gui.renderer,
                              bridge_methods=window.evaluate_js("Object.keys(window.pywebview.api)"),
                              viewport=window.evaluate_js("({width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth})"),
                              screenshot=str(screenshot), logs_screenshot=str(logs_screenshot),
                              guard_account=second["user_id"], max_accounts=5,
                              checks=["native_title", "native_icon_matches_custom_asset",
                                      "guardian_selection_persists", "daemon_uses_selected_account",
                                      "five_account_limit", "edit_and_delete_at_capacity",
                                      "logs_navigation", "logs_clear", "overview_navigation"],
                              tray=app.tray.available)
                window.resize(800, 620)
                wait_for("innerWidth <= 800")
                assert not window.evaluate_js("document.documentElement.scrollWidth > innerWidth")
                capture(ROOT / "dist" / "webview-preview-small.png")
            except Exception as exc:
                import traceback
                traceback.print_exc()
                report.update(ok=False, error=str(exc))
                try:
                    report["page"] = window.evaluate_js("({url:location.href,title:document.title,text:document.body.innerText.slice(0,500)})")
                except Exception:
                    pass
            finally:
                app._quit_app()

        app.window.events.loaded += lambda: None
        threading.Thread(target=checks, daemon=True).start()
        # 在测试目录中保存 WebView2 的临时浏览器数据。
        try:
            app._webview.start(app._start_background, gui="edgechromium", debug=False,
                               icon=str(ICON_PATH),
                               storage_path=str(Path(directory) / "webview"))
        finally:
            app._cleanup()
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
