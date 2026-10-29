"""Installer tests use fake EXEs, mock dialogs and never touch user registry."""

import base64
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app_lifecycle as lifecycle


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ, KEY_WRITE, REG_SZ, REG_DWORD = 1, 2, 1, 4

    def __init__(self):
        self.keys = {}
        self.fail_on_name = None

    class Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def OpenKey(self, _root, path, *_args):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self.Key(self.keys[path])

    def CreateKeyEx(self, _root, path, *_args):
        return self.Key(self.keys.setdefault(path, {}))

    def QueryInfoKey(self, key):
        return (0, len(key.values), 0)

    def EnumValue(self, key, index):
        name, (value, kind) = list(key.values.items())[index]
        return name, value, kind

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def SetValueEx(self, key, name, _reserved, kind, value):
        if self.fail_on_name == name:
            self.fail_on_name = None
            raise PermissionError("simulated registry write failure")
        key.values[name] = (value, kind)

    def DeleteValue(self, key, name):
        del key.values[name]

    def DeleteKey(self, _root, path):
        if self.keys[path]:
            raise OSError("not empty")
        del self.keys[path]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).parent / ".tmp"
        temp_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=temp_root, prefix="lifecycle-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        install = self.root / "应用程序" / "WOFO Login"
        menu = self.root / "开始菜单" / "WOFO Login"
        self.paths = lifecycle.AppPaths(
            install, install / "WOFO Login.exe", self.root / "桌面" / "WOFO Login.lnk",
            menu, menu / "WOFO Login.lnk", menu / "卸载 WOFO Login.lnk",
            self.root / "账号" / "campus_login",
        )
        self.source = self.root / "下载的程序.exe"
        self.source.write_bytes(b"fake frozen exe")

    def registration(self, executable=None):
        executable = executable or self.paths.executable
        return {
            "DisplayName": "WOFO Login", "InstallLocation": str(executable.parent),
            "UninstallString": lifecycle._uninstall_command(executable),
        }

    def test_source_mode_cannot_install_or_uninstall(self):
        with patch.object(lifecycle.sys, "frozen", False, create=True), \
                patch.object(lifecycle, "_paths") as paths, \
                patch.object(lifecycle, "_message") as message, \
                patch.object(lifecycle, "_launch_uninstall_helper") as launch:
            self.assertEqual(lifecycle.run_install(), lifecycle.ERROR)
            self.assertEqual(lifecycle.run_uninstall(), lifecycle.ERROR)
            paths.assert_not_called()
            launch.assert_not_called()
            self.assertTrue(all("EXE" in call.args[0] for call in message.call_args_list))

    def test_install_copies_only_single_exe_and_registers_per_user(self):
        with patch.object(lifecycle, "_frozen_executable", return_value=self.source), \
                patch.object(lifecycle, "_paths", return_value=self.paths), \
                patch.object(lifecycle, "_read_registration", return_value={}), \
                patch.object(lifecycle, "_message", return_value=True), \
                patch.object(lifecycle, "_create_shortcuts") as shortcuts, \
                patch.object(lifecycle, "_register_install") as register, \
                patch.object(lifecycle, "_migrate_autorun") as migrate:
            self.assertEqual(lifecycle.run_install(), lifecycle.SUCCESS)
            self.assertEqual(self.paths.executable.read_bytes(), self.source.read_bytes())
            self.assertEqual(list(self.paths.install_dir.iterdir()), [self.paths.executable])
            shortcuts.assert_called_once_with(self.paths)
            register.assert_called_once_with(self.paths)
            migrate.assert_called_once_with(self.source, self.paths.executable)

    def test_failed_update_copy_keeps_previous_executable_intact(self):
        self.paths.install_dir.mkdir(parents=True)
        self.paths.executable.write_bytes(b"old valid exe")
        with patch.object(lifecycle, "_frozen_executable", return_value=self.source), \
                patch.object(lifecycle, "_paths", return_value=self.paths), \
                patch.object(lifecycle, "_read_registration", return_value=self.registration()), \
                patch.object(lifecycle, "_message", return_value=True), \
                patch.object(lifecycle.shutil, "copy2", side_effect=OSError("incomplete copy")):
            self.assertEqual(lifecycle.run_install(), lifecycle.ERROR)
            self.assertEqual(self.paths.executable.read_bytes(), b"old valid exe")
            self.assertEqual(list(self.paths.install_dir.iterdir()), [self.paths.executable])

    def test_first_install_failure_removes_new_executable_and_allows_retry(self):
        with patch.object(lifecycle, "_frozen_executable", return_value=self.source), \
                patch.object(lifecycle, "_paths", return_value=self.paths), \
                patch.object(lifecycle, "_read_registration", return_value={}), \
                patch.object(lifecycle, "_message", return_value=True), \
                patch.object(lifecycle, "_create_shortcuts", side_effect=OSError("shortcut error")), \
                patch.object(lifecycle, "_run_powershell"):
            self.assertEqual(lifecycle.run_install(), lifecycle.ERROR)
            self.assertFalse(self.paths.executable.exists())
            self.assertFalse(self.paths.install_dir.exists())
            self.assertTrue(self.source.exists())

    def test_copy_rejects_reparse_point_destination(self):
        self.paths.install_dir.mkdir(parents=True)
        self.paths.executable.write_bytes(b"unrelated")
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_file_attributes=0x400)):
            with self.assertRaisesRegex(RuntimeError, "重解析点"):
                lifecycle._atomic_copy(self.source, self.paths.executable)
        self.assertEqual(self.paths.executable.read_bytes(), b"unrelated")

    def test_install_refuses_unowned_existing_file(self):
        self.paths.install_dir.mkdir(parents=True)
        self.paths.executable.write_bytes(b"someone else's file")
        with patch.object(lifecycle, "_frozen_executable", return_value=self.source), \
                patch.object(lifecycle, "_paths", return_value=self.paths), \
                patch.object(lifecycle, "_read_registration", return_value={}), \
                patch.object(lifecycle, "_message") as message:
            self.assertEqual(lifecycle.run_install(), lifecycle.ERROR)
            self.assertEqual(self.paths.executable.read_bytes(), b"someone else's file")
            self.assertIn("无法确认归属", message.call_args.args[0])

    def test_cancel_install_leaves_no_directory(self):
        with patch.object(lifecycle, "_frozen_executable", return_value=self.source), \
                patch.object(lifecycle, "_paths", return_value=self.paths), \
                patch.object(lifecycle, "_read_registration", return_value={}), \
                patch.object(lifecycle, "_message", return_value=False):
            self.assertEqual(lifecycle.run_install(), lifecycle.CANCELED)
            self.assertFalse(self.paths.install_dir.exists())

    def test_cancel_uninstall_never_launches_helper(self):
        with patch.object(lifecycle, "_frozen_executable", return_value=self.source), \
                patch.object(lifecycle, "_paths", return_value=self.paths), \
                patch.object(lifecycle, "_read_registration", return_value={}), \
                patch.object(lifecycle, "_message", return_value=False), \
                patch.object(lifecycle, "_launch_uninstall_helper") as launch:
            self.assertEqual(lifecycle.run_uninstall(), lifecycle.CANCELED)
            launch.assert_not_called()
            self.assertTrue(self.source.exists())

    def test_uninstall_preserves_accounts_by_default(self):
        self.paths.data_dir.mkdir(parents=True)
        account = self.paths.data_dir / "accounts.db"
        account.write_bytes(b"do not delete")
        with patch.object(lifecycle, "_frozen_executable", return_value=self.source), \
                patch.object(lifecycle, "_paths", return_value=self.paths), \
                patch.object(lifecycle, "_read_registration", return_value={}), \
                patch.object(lifecycle, "_message", side_effect=[True, False]), \
                patch.object(lifecycle, "_launch_uninstall_helper") as launch:
            self.assertEqual(lifecycle.run_uninstall(), lifecycle.SUCCESS)
            script = launch.call_args.args[0]
            self.assertIn("$deleteData = $false", script)
            self.assertIn("$dataFiles = @()", script)
            self.assertEqual(account.read_bytes(), b"do not delete")
            self.assertTrue(self.source.exists())

    def test_portable_copy_does_not_remove_other_installation_resources(self):
        script = lifecycle._uninstall_script(
            self.source, self.paths, registered=True, delete_data=False, process_id=123,
        )
        self.assertIn("$registered = $false", script)
        self.assertIn("$links = @()", script)
        self.assertNotIn(str(self.paths.desktop_shortcut), script)
        self.assertIn(lifecycle._quote_ps(self.source), script)

    def test_registered_uninstall_plans_only_known_shortcuts_and_data_files(self):
        script = lifecycle._uninstall_script(
            self.paths.executable, self.paths, registered=True, delete_data=True, process_id=123,
        )
        self.assertIn("$registered = $true", script)
        self.assertIn(str(self.paths.desktop_shortcut), script)
        self.assertIn(str(self.paths.uninstall_shortcut), script)
        for filename in lifecycle.DATA_FILENAMES:
            self.assertIn(str(self.paths.data_dir / filename), script)
        self.assertNotIn("-Recurse", script)
        self.assertNotIn("Stop-Process", script)
        self.assertNotIn("taskkill", script.lower())
        self.assertIn("WaitForExit(60000)", script)
        self.assertIn("[System.IO.Directory]::Delete($directory, $false)", script)
        self.assertIn("$record.UninstallString -ieq $expectedUninstall", script)
        self.assertIn("$link.TargetPath -ieq $exe", script)
        self.assertIn(str(self.paths.executable.parent / "校园网自动登录.exe"), script)

    def test_registration_requires_same_executable_and_location(self):
        values = self.registration()
        self.assertTrue(lifecycle._registration_owned(values, self.paths.executable))
        self.assertFalse(lifecycle._registration_owned(values, self.source))
        values["InstallLocation"] = str(self.root)
        self.assertFalse(lifecycle._registration_owned(values, self.paths.executable))

    def test_powershell_encoded_command_keeps_unicode_and_quotes_literal(self):
        value = self.root / "校园网 O'Brien $() `目录" / "WOFO Login.exe"
        script = "$exe = " + lifecycle._quote_ps(value)
        command = lifecycle._encoded_command(script)
        self.assertEqual(base64.b64decode(command[-1]).decode("utf-16le"), script)
        self.assertIn("O''Brien", script)
        self.assertEqual(command[-2], "-EncodedCommand")

    def test_helper_uses_hidden_detached_process_with_no_shell(self):
        with patch.object(lifecycle.subprocess, "Popen") as popen:
            lifecycle._launch_uninstall_helper("# harmless mock script")
        self.assertNotIn("shell", popen.call_args.kwargs)
        command = popen.call_args.args[0]
        self.assertIn("Hidden", command)
        flags = popen.call_args.kwargs["creationflags"]
        if os.name == "nt":
            self.assertTrue(flags & lifecycle.subprocess.DETACHED_PROCESS)
            self.assertTrue(flags & lifecycle.subprocess.CREATE_NO_WINDOW)

    def test_install_shortcuts_have_executable_target_and_separate_arguments(self):
        with patch.object(lifecycle, "_run_powershell") as run:
            lifecycle._create_shortcuts(self.paths)
        script = run.call_args.args[0]
        self.assertIn(lifecycle._quote_ps(self.paths.executable), script)
        self.assertIn("Arguments = '--uninstall'", script)
        self.assertIn("$existing.TargetPath -ine $target", script)
        self.assertIn("$link.Arguments = $entry.Arguments", script)

    def test_registration_values_are_per_user_and_failed_writes_roll_back(self):
        fake = FakeWinreg()
        with patch.dict(sys.modules, {"winreg": fake}):
            lifecycle._register_install(self.paths)
            values = fake.keys[lifecycle.UNINSTALL_KEY]
            self.assertEqual(values["DisplayName"], (lifecycle.APP_DISPLAY_NAME, fake.REG_SZ))
            self.assertEqual(values["Publisher"], ("WOFO", fake.REG_SZ))
            self.assertEqual(values["UninstallString"][0], lifecycle._uninstall_command(self.paths.executable))
            self.assertTrue(lifecycle._registration_owned(lifecycle._read_registration(), self.paths.executable))
        failing = FakeWinreg()
        failing.fail_on_name = "DisplayVersion"
        with patch.dict(sys.modules, {"winreg": failing}):
            with self.assertRaises(PermissionError):
                lifecycle._register_install(self.paths)
        self.assertNotIn(lifecycle.UNINSTALL_KEY, failing.keys)

    def test_autorun_migrates_exact_legacy_sibling_and_preserves_arguments(self):
        fake = FakeWinreg()
        legacy = self.source.parent / "校园网自动登录.exe"
        unrelated = self.root / "other-folder" / "校园网自动登录.exe"
        fake.keys[lifecycle.AUTORUN_KEY] = {
            "校园网自动登录": (f'"{legacy}" --minimized', fake.REG_SZ),
            "WOFO Login": (f'"{unrelated}"', fake.REG_SZ),
            "Other app": (f'"{self.source}"', fake.REG_SZ),
        }
        with patch.dict(sys.modules, {"winreg": fake}):
            lifecycle._migrate_autorun(self.source, self.paths.executable)
        values = fake.keys[lifecycle.AUTORUN_KEY]
        self.assertEqual(values["校园网自动登录"][0], f'"{self.paths.executable}" --minimized')
        self.assertEqual(values["WOFO Login"][0], f'"{unrelated}"')
        self.assertEqual(values["Other app"][0], f'"{self.source}"')

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell syntax parser")
    def test_generated_powershell_scripts_parse_without_execution(self):
        uninstall = lifecycle._uninstall_script(
            self.paths.executable, self.paths, registered=True, delete_data=True, process_id=123,
        )
        with patch.object(lifecycle, "_run_powershell") as create:
            lifecycle._create_shortcuts(self.paths)
        scripts = (uninstall, create.call_args.args[0])
        for index, script in enumerate(scripts):
            path = self.root / f"parse-only-{index}.ps1"
            path.write_text(script, encoding="utf-8-sig")
            parser = (
                "$tokens=$null; $parseErrors=$null; "
                f"[System.Management.Automation.Language.Parser]::ParseFile({lifecycle._quote_ps(path)}, [ref]$tokens, [ref]$parseErrors) | Out-Null; "
                "if ($parseErrors.Count) { $parseErrors | ForEach-Object { Write-Output $_.Message }; exit 1 }"
            )
            try:
                result = subprocess.run(
                    lifecycle._encoded_command(parser), capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=20,
                )
            except PermissionError:
                self.skipTest("Sandbox disallows Python launching Windows PowerShell; parse via host shell")
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))


if __name__ == "__main__":
    unittest.main()
