"""Storage regressions run against temporary data and an in-memory encryption key."""

import json
import plistlib
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet

from config.settings import AutoStartManager, Settings, SettingsManager
from database.db import Database


def temporary_directory():
    # Use the workspace on Windows: some sandbox system-temp directories deny
    # nested file creation even when mkdtemp itself succeeds.
    return tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".storage-test-")


class MemoryCrypto:
    def __init__(self):
        self.fernet = Fernet(Fernet.generate_key())

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode())

    def decrypt(self, value):
        return self.fernet.decrypt(value).decode()


class FakeAutoStart:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.fail = False
        self.calls = []

    def is_enabled(self):
        return self.enabled

    def enable(self):
        self.calls.append(True)
        if self.fail:
            return False
        self.enabled = True
        return True

    def disable(self):
        self.calls.append(False)
        if self.fail:
            return False
        self.enabled = False
        return True


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temporary_directory()
        self.addCleanup(self.temporary.cleanup)
        self.crypto = MemoryCrypto()
        self.crypto_patch = patch("database.db.get_crypto", return_value=self.crypto)
        self.crypto_patch.start()
        self.addCleanup(self.crypto_patch.stop)
        self.path = Path(self.temporary.name) / "accounts.db"
        self.db = Database(self.path)
        self.addCleanup(self.db.close)
        self.db.add_account("first", "initial password", set_default=True)
        self.first = self.db.get_default_account()
        self.db.add_account("second", "another password")
        self.second = next(a for a in self.db.get_all_accounts() if a.user_id == "second")

    def test_duplicate_add_rolls_back_default_and_next_commit(self):
        self.assertFalse(self.db.add_account("second", "replacement", set_default=True))
        self.assertTrue(self.db.update_account(self.second.id, service="移动"))
        self.assertEqual(self.db.get_default_account().id, self.first.id)
        self.assertEqual(self.db.get_account(self.second.id).password, "another password")

    def test_duplicate_update_rolls_back_default_and_password(self):
        self.assertFalse(self.db.update_account(
            self.second.id, user_id="first", password="replacement", is_default=True,
        ))
        self.assertEqual(self.db.get_default_account().id, self.first.id)
        self.assertEqual(self.db.get_account(self.second.id).password, "another password")

    def test_missing_ids_do_not_report_success_or_clear_default(self):
        self.assertFalse(self.db.update_account(9999, is_default=True))
        self.assertFalse(self.db.delete_account(9999))
        self.assertFalse(self.db.set_default(9999))
        self.assertEqual(self.db.get_default_account().id, self.first.id)

    def test_reopen_preserves_ciphertext_and_account_data(self):
        before = self.db._conn.execute(
            "SELECT encrypted_password FROM accounts WHERE id = ?", (self.first.id,)
        ).fetchone()[0]
        self.assertNotIn(b"initial password", before)
        self.db.close()
        reopened = Database(self.path)
        self.addCleanup(reopened.close)
        restored = reopened.get_account(self.first.id)
        self.assertEqual(restored.password, "initial password")
        self.assertTrue(restored.is_default)
        after = reopened._conn.execute(
            "SELECT encrypted_password FROM accounts WHERE id = ?", (self.first.id,)
        ).fetchone()[0]
        self.assertEqual(before, after)

    def test_read_cannot_observe_half_finished_default_transaction(self):
        entered = threading.Event()
        release = threading.Event()
        reading = threading.Event()
        connection = self.db._conn

        class GatedConnection:
            def __enter__(self):
                connection.__enter__()
                return self

            def __exit__(self, *args):
                return connection.__exit__(*args)

            def execute(self, sql, *args):
                cursor = connection.execute(sql, *args)
                if sql == "UPDATE accounts SET is_default = 0":
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("test gate timed out")
                return cursor

            def close(self):
                connection.close()

        self.db._conn = GatedConnection()

        def read_accounts():
            reading.set()
            return self.db.get_all_accounts()

        with ThreadPoolExecutor(max_workers=2) as workers:
            mutation = workers.submit(self.db.add_account, "second", "replacement", "电信", True)
            try:
                self.assertTrue(entered.wait(2))
                read = workers.submit(read_accounts)
                self.assertTrue(reading.wait(2))
                with self.assertRaises(FutureTimeoutError):
                    read.result(timeout=0.05)
            finally:
                release.set()
            self.assertFalse(mutation.result(timeout=2))
            accounts = read.result(timeout=2)
        self.assertEqual([a.id for a in accounts if a.is_default], [self.first.id])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temporary_directory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.auto_start = FakeAutoStart()
        self.manager = SettingsManager(self.path, self.auto_start)

    def test_invalid_existing_values_are_sanitized(self):
        self.manager.config_file.write_text(json.dumps({
            "check_interval": 0, "daemon_enabled": "false", "start_minimized": 1,
        }), encoding="utf-8")
        loaded = SettingsManager(self.path, self.auto_start).get()
        self.assertEqual(loaded.check_interval, 30)
        self.assertTrue(loaded.daemon_enabled)
        self.assertFalse(loaded.start_minimized)
        self.assertEqual(Settings.from_dict([]), Settings())

    def test_interval_and_boolean_validation_precedes_changes(self):
        for value in (True, 9, 301, 30.0, "30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.manager.update(check_interval=value, auto_start=True)
        with self.assertRaises(ValueError):
            self.manager.update(daemon_enabled="false")
        self.assertEqual(self.auto_start.calls, [])
        self.assertFalse(self.manager.config_file.exists())
        self.assertEqual(self.manager.get().check_interval, 30)

    def test_successful_update_persists_without_changing_daemon_preference(self):
        self.manager.update(check_interval=45, auto_start=True)
        self.assertEqual(self.auto_start.calls, [True])
        loaded = SettingsManager(self.path, self.auto_start).get()
        self.assertEqual(loaded.check_interval, 45)
        self.assertTrue(loaded.auto_start)
        self.assertTrue(loaded.daemon_enabled)

    def test_save_failure_keeps_original_file_and_in_memory_settings(self):
        self.manager.save()
        original = self.manager.config_file.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.manager.update(check_interval=60, auto_start=True)
        self.assertEqual(self.manager.config_file.read_bytes(), original)
        self.assertEqual(self.manager.get().check_interval, 30)
        self.assertFalse(self.manager.get().auto_start)
        self.assertFalse(self.auto_start.enabled)
        self.assertEqual(self.auto_start.calls, [True, False])
        self.assertEqual(list(self.path.glob(".settings-*.tmp")), [])

    def test_autostart_failure_is_reported_without_saving(self):
        self.auto_start.fail = True
        with self.assertRaises(OSError):
            self.manager.update(auto_start=True)
        self.assertFalse(self.manager.get().auto_start)
        self.assertFalse(self.manager.config_file.exists())

    def test_save_failure_propagates(self):
        with patch.object(Path, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                self.manager.save()

    def test_actual_autostart_state_is_loaded_without_existing_file(self):
        self.auto_start.enabled = True
        self.assertTrue(SettingsManager(self.path, self.auto_start).get().auto_start)


class AutoStartTests(unittest.TestCase):
    def setUp(self):
        self.manager = AutoStartManager()
        self.args = [r"C:\Program Files\Python\python.exe", r"C:\Campus App\main.py"]

    def test_source_and_frozen_launch_arguments(self):
        with patch.object(sys, "frozen", False, create=True):
            args = self.manager._get_app_args()
        self.assertEqual(args[0], sys.executable)
        self.assertEqual(Path(args[1]).name, "main.py")
        self.assertTrue(Path(args[1]).is_absolute())
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(self.manager._get_app_args(), [sys.executable])

    def test_windows_command_quotes_source_and_executable(self):
        registry = SimpleNamespace(
            HKEY_CURRENT_USER=1, KEY_WRITE=2, REG_SZ=3,
            OpenKey=Mock(return_value=object()), SetValueEx=Mock(), CloseKey=Mock(),
        )
        with patch.dict(sys.modules, {"winreg": registry}), patch.object(
            self.manager, "_get_app_args", return_value=self.args,
        ):
            self.assertTrue(self.manager._enable_windows())
        command = registry.SetValueEx.call_args.args[-1]
        self.assertEqual(command, subprocess.list2cmdline(self.args))
        self.assertIn('"C:\\Campus App\\main.py"', command)

    def test_macos_uses_plist_argument_array_and_escapes_xml(self):
        with temporary_directory() as directory:
            args = ["/Applications/Campus & Network.app/run", "/Campus <Test>/main.py"]
            with patch.object(Path, "home", return_value=Path(directory)), patch.object(
                self.manager, "_get_app_args", return_value=args,
            ):
                self.assertTrue(self.manager._enable_macos())
            target = Path(directory) / "Library/LaunchAgents/com.campus.login.plist"
            self.assertEqual(plistlib.loads(target.read_bytes())["ProgramArguments"], args)

    def test_linux_exec_quotes_spaces_and_escapes_field_codes(self):
        args = ["/opt/python bin/python", "/home/user/Campus 100%/main.py"]
        with temporary_directory() as directory:
            with patch.object(Path, "home", return_value=Path(directory)), patch.object(
                self.manager, "_get_app_args", return_value=args,
            ):
                self.assertTrue(self.manager._enable_linux())
            content = (Path(directory) / ".config/autostart/campus_login.desktop").read_text(
                encoding="utf-8",
            )
        self.assertIn('Exec="/opt/python bin/python" "/home/user/Campus 100%%/main.py"', content)


if __name__ == "__main__":
    unittest.main()
