"""Bridge regressions: no GUI, network requests, user configuration or key files."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from config.settings import Settings
from database.db import Database
from ui.api import AppApi


class _MemoryCrypto:
    def encrypt(self, text):
        return text.encode("utf-8")

    def decrypt(self, data):
        return bytes(data).decode("utf-8")


class _SettingsManager:
    """Settings persistence substitute that cannot touch the user's computer."""

    def __init__(self, **values):
        self.settings = Settings(**values)
        self.saves = 0

    def get(self):
        return self.settings

    def save(self):
        self.saves += 1
        return True

    def update(self, **values):
        for name, value in values.items():
            setattr(self.settings, name, value)
        return self.save()


class AppApiTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).parent / ".tmp"
        temp_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        crypto_patch = patch("database.db.get_crypto", return_value=_MemoryCrypto())
        crypto_patch.start()
        self.addCleanup(crypto_patch.stop)
        self.db = Database(Path(self.temp.name) / "accounts.db")
        self.addCleanup(self.db.close)
        self.settings = _SettingsManager(daemon_enabled=False)
        self.api = AppApi(self.db, self.settings)
        for attribute, target in (
            ("network", "ui.api.auth.check_network"),
            ("login_mock", "ui.api.auth.login"),
            ("logout_mock", "ui.api.auth.logout"),
        ):
            patcher = patch(target, return_value=True)
            setattr(self, attribute, patcher.start())
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: self.api._close())

    def wait_state(self, predicate, message="API state did not settle", timeout=3):
        deadline = time.monotonic() + timeout
        pause = threading.Event()
        while time.monotonic() < deadline:
            state = self.api.get_state()
            if predicate(state):
                return state
            pause.wait(0.01)
        self.fail(f"{message}: {self.api.get_state()!r}")

    def start_api(self):
        self.api._start()
        return self.wait_state(
            lambda state: state["network"]["last_checked"]
            and not state["network"]["checking"]
            and not state["busy"],
            "initial network check did not finish",
        )

    def add_account(self, user_id="20260001", password="test-secret-alpha", **extra):
        result = self.api.save_account({
            "user_id": user_id,
            "password": password,
            "service": "电信",
            **extra,
        })
        self.assertTrue(result["ok"], result)
        return next(account for account in result["state"]["accounts"]
                    if account["user_id"] == user_id)

    def assert_private(self, result, *secrets):
        encoded = json.dumps(result, ensure_ascii=False)
        for secret in secrets:
            self.assertNotIn(secret, encoded)

        def inspect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn("password", key.lower())
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        inspect(result)

    def test_account_crud_default_and_password_privacy(self):
        first = self.add_account()
        second = self.add_account("20260002", "test-secret-beta", service="移动")
        response = self.api.select_account(first["id"])
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["state"]["selected_account_id"], first["id"])

        response = self.api.set_default(second["id"])
        self.assertTrue(response["ok"], response)
        self.assertEqual(self.db.get_default_account().id, second["id"])
        self.assertEqual(response["state"]["selected_account_id"], second["id"])
        self.assert_private(response, "test-secret-alpha", "test-secret-beta")

        response = self.api.save_account({
            "id": first["id"], "user_id": "20269999", "password": "", "service": "联通",
        })
        self.assertTrue(response["ok"], response)
        stored = self.db.get_account(first["id"])
        self.assertEqual(stored.user_id, "20269999")
        self.assertEqual(stored.password, "test-secret-alpha")
        self.assertEqual(stored.service, "联通")
        self.assert_private(response, "test-secret-alpha", "test-secret-beta")
        self.assert_private(self.api.get_state(), "test-secret-alpha", "test-secret-beta")

        response = self.api.delete_account(first["id"])
        self.assertTrue(response["ok"], response)
        self.assertEqual(len(response["state"]["accounts"]), 1)
        self.assertIsNone(self.db.get_account(first["id"]))

    def test_account_limit_allows_five_editing_and_replacement_after_delete(self):
        accounts = [self.add_account(f"20260{index:03}") for index in range(5)]
        state = self.api.get_state()
        self.assertEqual(state["account_limit"], 5)
        self.assertEqual(len(state["accounts"]), 5)

        response = self.api.save_account({
            "user_id": "20269999", "password": "sixth-account-secret", "service": "电信",
        })
        self.assertFalse(response["ok"], response)
        self.assertTrue(response["message"])
        self.assertEqual(len(self.db.get_all_accounts()), 5)
        self.assert_private(response, "sixth-account-secret", "test-secret-alpha")

        response = self.api.save_account({
            "id": accounts[-1]["id"], "user_id": "20268888", "password": "",
            "service": "移动", "is_default": True,
        })
        self.assertTrue(response["ok"], response)
        self.assertEqual(len(response["state"]["accounts"]), 5)
        self.assertEqual(response["state"]["selected_account_id"], accounts[-1]["id"])
        self.assertEqual(self.db.get_account(accounts[-1]["id"]).password, "test-secret-alpha")
        self.assertTrue(self.api.delete_account(accounts[0]["id"])["ok"])
        self.add_account("20267777")
        self.assertEqual(len(self.db.get_all_accounts()), 5)

    def test_concurrent_account_saves_cannot_exceed_five(self):
        for index in range(4):
            self.add_account(f"20260{index:03}")
        ready = threading.Barrier(8)

        def add_fifth(index):
            ready.wait(timeout=3)
            return self.api.save_account({
                "user_id": f"20269{index:03}", "password": "concurrent-secret",
                "service": "电信",
            })

        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(add_fifth, range(8)))
        self.assertEqual(sum(response["ok"] for response in responses), 1)
        self.assertEqual(len(self.db.get_all_accounts()), 5)
        self.assert_private(responses, "concurrent-secret", "test-secret-alpha")

    def test_selected_guard_account_drives_reconnect_and_survives_restart(self):
        first = self.add_account()
        second = self.add_account("20260002", "second-account-secret", is_default=True)
        self.assertEqual(self.api.get_state()["selected_account_id"], second["id"])
        response = self.api.select_account(first["id"])
        self.assertTrue(response["ok"], response)
        self.assertEqual(self.db.get_default_account().id, first["id"])
        self.assertEqual(response["state"]["daemon"]["account_id"], first["id"])
        self.assertTrue(self.api.toggle_daemon(True)["ok"])

        self.network.return_value = False
        self.api._check_and_reconnect()
        self.login_mock.assert_called_once_with(
            first["user_id"], "test-secret-alpha", first["service"],
        )
        self.assert_private(self.api.get_state(), "test-secret-alpha", "second-account-secret")

        self.api._close()
        self.api = AppApi(self.db, self.settings)
        restored = self.api.get_state()
        self.assertEqual(restored["selected_account_id"], first["id"])
        self.assertEqual(restored["daemon"]["account_id"], first["id"])

    def test_logs_are_bounded_timestamped_isolated_and_clearable(self):
        state = self.api.get_state()
        self.assertEqual(state["log_limit"], 500)
        limit = state["log_limit"]
        for index in range(limit + 7):
            with self.api._lock:
                self.api._event(f"retention-event-{index}", "info")

        state = self.api.get_state()
        logs = state["logs"]
        self.assertEqual(len(logs), limit)
        self.assertEqual(logs[0]["message"], "retention-event-7")
        self.assertEqual(logs[-1]["message"], f"retention-event-{limit + 6}")
        self.assertEqual([event["id"] for event in logs], sorted({event["id"] for event in logs}))
        for event in logs:
            self.assertEqual(event["level"], "info")
            recorded = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
            self.assertEqual(recorded.utcoffset(), timezone.utc.utcoffset(recorded))

        logs[0]["message"] = "mutated browser snapshot"
        self.assertEqual(self.api.get_state()["logs"][0]["message"], "retention-event-7")
        last_event = self.api.get_state()["last_event"]
        response = self.api.clear_logs()
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["message"])
        self.assertEqual(response["state"]["logs"], [])
        self.assertEqual(response["state"]["last_event"], last_event)
        self.assertEqual(self.api.get_state()["logs"], [])
        self.assertTrue(self.api.clear_logs()["ok"])
        with self.api._lock:
            self.api._event("after-clear", "success")
        state = self.api.get_state()
        self.assertEqual(len(state["logs"]), 1)
        self.assertGreater(state["logs"][0]["id"], last_event["id"])

    def test_account_and_settings_actions_are_logged_without_passwords(self):
        def log_ids():
            return [event["id"] for event in self.api.get_state()["logs"]]

        previous = log_ids()
        first = self.add_account(password="private-first-password")
        self.assertGreater(len(log_ids()), len(previous))
        second = self.add_account("20260002", "private-second-password")
        actions = (
            lambda: self.api.select_account(first["id"]),
            lambda: self.api.set_default(second["id"]),
            lambda: self.api.save_account({
                "id": first["id"], "user_id": "20260001", "password": "private-edited-password",
                "service": "移动",
            }),
            lambda: self.api.toggle_daemon(True),
            lambda: self.api.update_settings({"check_interval": 60}),
            lambda: self.api.delete_account(first["id"]),
        )
        for action in actions:
            previous = log_ids()
            response = action()
            self.assertTrue(response["ok"], response)
            self.assertGreater(len(log_ids()), len(previous))
            self.assert_private(response, "private-first-password", "private-second-password",
                                "private-edited-password")

    def test_network_lifecycle_and_failures_appear_in_log_without_exception_secrets(self):
        account = self.add_account(password="private-login-password")
        self.start_api()
        initial = self.api.get_state()["logs"]
        self.assertTrue(initial)
        previous_id = initial[-1]["id"]
        self.login_mock.side_effect = RuntimeError("private-login-password")
        with self.assertLogs("campus_login.api", level="ERROR"):
            self.assertTrue(self.api.login()["ok"])
            state = self.wait_state(
                lambda state: not state["busy"] and state["last_event"]["level"] == "error",
            )
        new_logs = [event for event in state["logs"] if event["id"] > previous_id]
        self.assertGreaterEqual(len(new_logs), 2)  # Start and failure remain inspectable.
        self.assertTrue(any(event["level"] == "error" for event in new_logs))
        self.assert_private(state, "private-login-password")

        previous_id = state["logs"][-1]["id"]
        self.api._close()
        state = self.api.get_state()
        self.assertGreater(state["logs"][-1]["id"], previous_id)
        self.assertEqual(self.db.get_account(account["id"]).user_id, account["user_id"])

    def test_new_account_requires_password_and_valid_service(self):
        for payload in (
            {"user_id": "20260001", "password": "", "service": "电信"},
            {"user_id": "", "password": "secret", "service": "电信"},
            {"user_id": "20260001", "password": "secret", "service": "unknown"},
            None,
            [],
        ):
            with self.subTest(payload=payload):
                result = self.api.save_account(payload)
                self.assertFalse(result["ok"], result)
                self.assertTrue(result["message"])
        self.assertEqual(self.db.get_all_accounts(), [])

    def test_invalid_account_ids_return_friendly_errors(self):
        account = self.add_account()
        for method in (self.api.select_account, self.api.set_default, self.api.delete_account):
            for account_id in (99999, "invalid", None, True):
                with self.subTest(method=method.__name__, account_id=account_id):
                    result = method(account_id)
                    self.assertFalse(result["ok"], result)
                    self.assertTrue(result["message"])
        self.assertIsNotNone(self.db.get_account(account["id"]))

    def test_duplicate_edit_does_not_lose_account_or_default(self):
        first = self.add_account()
        second = self.add_account("20260002", "test-secret-beta")
        self.assertTrue(self.api.set_default(second["id"])["ok"])
        response = self.api.save_account({
            "id": second["id"], "user_id": first["user_id"],
            "password": "", "service": "移动", "is_default": True,
        })
        self.assertFalse(response["ok"], response)
        self.assertTrue(response["message"])
        self.assertEqual(self.db.get_account(second["id"]).user_id, "20260002")
        self.assertEqual(self.db.get_default_account().id, second["id"])
        self.assertEqual(len(self.api.get_state()["accounts"]), 2)

    def test_invalid_settings_do_not_mutate_preferences(self):
        original = self.settings.get().to_dict()
        for payload in (
            {"check_interval": 0},
            {"check_interval": -1},
            {"check_interval": "fast"},
            {"check_interval": True},
            {"show_notifications": "yes"},
            None,
            [],
        ):
            with self.subTest(payload=payload):
                response = self.api.update_settings(payload)
                self.assertFalse(response["ok"], response)
                self.assertEqual(self.settings.get().to_dict(), original)
        response = self.api.update_settings({
            "check_interval": 60, "show_notifications": False, "start_minimized": True,
        })
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["state"]["settings"]["check_interval"], 60)
        self.assertFalse(self.settings.get().show_notifications)
        self.assertTrue(self.settings.get().start_minimized)

    def test_daemon_waits_for_account_and_retains_preference_when_last_deleted(self):
        self.settings.get().daemon_enabled = True
        self.start_api()
        state = self.api.get_state()
        self.assertTrue(state["daemon"]["enabled"])
        self.assertFalse(state["daemon"]["active"])
        account = self.add_account()
        self.wait_state(lambda state: state["daemon"]["active"])
        response = self.api.delete_account(account["id"])
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["state"]["daemon"]["enabled"])
        self.assertFalse(response["state"]["daemon"]["active"])
        self.assertTrue(self.settings.get().daemon_enabled)

    def test_close_preserves_daemon_preference_and_database_ownership(self):
        account = self.add_account()
        self.api.toggle_daemon(True)
        self.start_api()
        self.api._close()
        self.assertTrue(self.settings.get().daemon_enabled)
        self.assertEqual(self.db.get_account(account["id"]).user_id, account["user_id"])

    def test_saved_account_wake_does_not_duplicate_first_auto_login(self):
        self.settings.get().daemon_enabled = True
        self.network.return_value = False
        first_login = threading.Event()
        duplicate_login = threading.Event()

        def failed_login(*args):
            if self.login_mock.call_count == 1:
                first_login.set()
            else:
                duplicate_login.set()
            return False

        self.login_mock.side_effect = failed_login
        self.add_account()  # Wakes the worker before its initial check has begun.
        self.api._start()
        self.assertTrue(first_login.wait(2), "initial daemon login did not run")
        self.wait_state(lambda state: not state["busy"] and state["last_event"])
        self.assertFalse(duplicate_login.wait(0.1),
                         "the same wake triggered an immediate second daemon login")
        self.assertEqual(self.login_mock.call_count, 1)

    def test_queued_manual_login_wake_does_not_trigger_immediate_auto_retry(self):
        self.settings.get().daemon_enabled = True
        self.network.return_value = False
        first_login = threading.Event()
        duplicate_login = threading.Event()

        def failed_login(*args):
            if self.login_mock.call_count == 1:
                first_login.set()
            else:
                duplicate_login.set()
            return False

        self.login_mock.side_effect = failed_login
        self.add_account()
        self.assertTrue(self.api.login()["ok"])
        self.api._start()
        self.assertTrue(first_login.wait(2), "queued manual login did not run")
        self.wait_state(lambda state: not state["busy"] and state["last_event"])
        self.assertFalse(duplicate_login.wait(0.1),
                         "the manual login wake triggered an immediate automatic retry")
        self.assertEqual(self.login_mock.call_count, 1)

    def test_login_is_asynchronous_and_overlapping_operation_is_serialized(self):
        self.add_account()
        self.start_api()
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocking_login(*args):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release the login")
            return True

        self.login_mock.side_effect = blocking_login
        started = time.monotonic()
        response = self.api.login()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(response["ok"], response)
        self.assertTrue(entered.wait(2), "worker did not begin login")
        self.assertTrue(self.api.get_state()["busy"])
        self.api.logout()
        self.assertEqual(self.logout_mock.call_count, 0,
                         "logout ran concurrently with the pending login")
        release.set()
        self.wait_state(lambda state: not state["busy"])
        self.assertTrue(self.api.get_state()["last_event"])

    def test_logout_disables_reconnect_and_keeps_network_offline(self):
        self.add_account()
        self.api.toggle_daemon(True)
        self.start_api()
        self.network.side_effect = lambda: self.logout_mock.call_count == 0
        response = self.api.logout()
        self.assertTrue(response["ok"], response)
        self.wait_state(lambda state: not state["busy"] and not state["network"]["connected"])
        state = self.api.get_state()
        self.assertFalse(state["daemon"]["enabled"])
        self.assertFalse(state["daemon"]["active"])
        self.assertFalse(self.settings.get().daemon_enabled)
        self.assertEqual(self.login_mock.call_count, 0)
        self.assertEqual(self.logout_mock.call_count, 1)

    def test_network_exception_does_not_kill_worker(self):
        self.add_account()
        checked = threading.Event()

        def failed_check():
            checked.set()
            raise RuntimeError("simulated probe failure")

        self.network.side_effect = failed_check
        with self.assertLogs("campus_login.api", level="ERROR"):
            self.api._start()
            self.assertTrue(checked.wait(2), "worker did not perform initial check")
            self.wait_state(lambda state: not state["network"]["checking"] and not state["busy"])
            self.network.side_effect = None
        self.network.return_value = True
        response = self.api.login()
        self.assertTrue(response["ok"], response)
        self.wait_state(lambda state: not state["busy"] and state["network"]["connected"])
        self.assertEqual(self.login_mock.call_count, 1)

    def test_login_exception_clears_busy_and_worker_accepts_retry(self):
        self.add_account()
        self.start_api()
        self.login_mock.side_effect = RuntimeError("simulated login failure")
        with self.assertLogs("campus_login.api", level="ERROR"):
            self.assertTrue(self.api.login()["ok"])
            failed_state = self.wait_state(
                lambda state: not state["busy"] and state["last_event"],
                "login exception left the worker busy",
            )
        self.assertEqual(failed_state["last_event"]["level"], "error")
        self.assertFalse(failed_state["network"]["checking"])
        failed_event_id = failed_state["last_event"]["id"]

        self.login_mock.side_effect = None
        self.assertTrue(self.api.login()["ok"])
        recovered = self.wait_state(
            lambda state: not state["busy"]
            and state["last_event"]["id"] > failed_event_id,
        )
        self.assertEqual(recovered["last_event"]["level"], "success")
        self.assertTrue(recovered["network"]["connected"])
        self.assertEqual(self.login_mock.call_count, 2)

    def test_manual_login_without_account_returns_error(self):
        self.start_api()
        result = self.api.login()
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["message"])
        self.login_mock.assert_not_called()

    def test_source_run_cannot_launch_uninstall(self):
        called = threading.Event()
        self.api = AppApi(self.db, self.settings, on_uninstall=called.set)
        with patch.object(sys, "frozen", False, create=True):
            self.assertFalse(self.api.get_state()["can_uninstall"])
            self.assertFalse(self.api.uninstall_app()["ok"])
        self.assertFalse(called.is_set())

    def test_uninstall_wizard_blocks_duplicates_and_recovers_after_cancel(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def canceled_wizard():
            entered.set()
            release.wait(3)

        self.api = AppApi(self.db, self.settings, on_uninstall=canceled_wizard)
        with patch.object(sys, "platform", "win32"), \
                patch.object(sys, "frozen", True, create=True):
            self.assertTrue(self.api.get_state()["can_uninstall"])
            response = self.api.uninstall_app()
            self.assertTrue(response["ok"])
            self.assertTrue(response["state"]["uninstall_pending"])
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.api.uninstall_app()["ok"])
            release.set()
            self.wait_state(lambda state: not state["uninstall_pending"])
            self.assertFalse(self.api._stop.is_set())

    def test_uninstall_callback_failure_is_reported_and_does_not_leave_pending(self):
        self.api = AppApi(self.db, self.settings, on_uninstall=lambda: None)
        self.api._on_uninstall = lambda: (_ for _ in ()).throw(OSError("mock failure"))
        with patch.object(sys, "platform", "win32"), \
                patch.object(sys, "frozen", True, create=True), \
                self.assertLogs("campus_login.api", level="ERROR"):
            self.assertTrue(self.api.uninstall_app()["ok"])
            state = self.wait_state(lambda state: not state["uninstall_pending"] and state["last_event"])
        self.assertEqual(state["last_event"]["level"], "error")

    def test_window_actions_call_handlers_and_report_unavailable_tray(self):
        self.api._close()
        actions = []
        quit_called = threading.Event()

        def quit_handler():
            actions.append("quit")
            quit_called.set()

        self.api = AppApi(self.db, self.settings,
                          on_hide=lambda: actions.append("hide"),
                          on_quit=quit_handler)
        self.assertFalse(self.api.hide_window()["ok"])
        self.api._set_tray_available(True)
        self.assertTrue(self.api.get_state()["tray_available"])
        self.assertTrue(self.api.hide_window()["ok"])
        self.assertTrue(self.api.quit_app()["ok"])
        self.assertTrue(quit_called.wait(2), "quit handler was not called")
        self.assertEqual(actions, ["hide", "quit"])


if __name__ == "__main__":
    unittest.main()
