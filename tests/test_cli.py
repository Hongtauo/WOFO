"""安装、卸载命令行只分派对应入口，不启动校园网认证或图形主窗口。"""

import contextlib
import io
import unittest
from unittest.mock import patch

import main
from ui.branding import APP_NAME, APP_VERSION


class CommandLineTests(unittest.TestCase):
    def test_install_dispatches_without_constructing_window(self):
        with patch("app_lifecycle.run_install", return_value=0) as install, \
                patch("ui.main_window.MainWindow") as window:
            self.assertEqual(main.main(["--install"]), 0)
        install.assert_called_once_with()
        window.assert_not_called()

    def test_uninstall_preserves_canceled_result_and_skips_window(self):
        with patch("app_lifecycle.run_uninstall", return_value=2) as uninstall, \
                patch("ui.main_window.MainWindow") as window:
            self.assertEqual(main.main(["--uninstall"]), 2)
        uninstall.assert_called_once_with()
        window.assert_not_called()

    def test_version_does_not_launch_app_or_lifecycle(self):
        stream = io.StringIO()
        with patch("ui.main_window.MainWindow") as window, \
                patch("app_lifecycle.run_install") as install, \
                patch("app_lifecycle.run_uninstall") as uninstall, \
                contextlib.redirect_stdout(stream), self.assertRaises(SystemExit) as result:
            main.main(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn(f"{APP_NAME} {APP_VERSION}", stream.getvalue())
        window.assert_not_called()
        install.assert_not_called()
        uninstall.assert_not_called()


if __name__ == "__main__":
    unittest.main()
