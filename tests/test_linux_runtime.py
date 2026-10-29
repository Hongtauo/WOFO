"""GTK lifecycle regressions without a display, desktop changes or real accounts."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config.settings import AutoStartManager
from ui.branding import APP_ID, APP_TITLE, ICON_PATH, ICON_PNG_PATH
from ui.main_window import MainWindow
from ui.tray import TrayIcon, _GtkTrayCleanup


class IdleLoop:
    def __init__(self):
        self.pending = []

    def idle_add(self, function, *args):
        self.pending.append((function, args))
        return len(self.pending)

    def drain(self, *unused, **kwargs):
        while self.pending:
            function, args = self.pending.pop(0)
            if function(*args):
                self.pending.append((function, args))


class Indicator:
    def __init__(self, connected):
        self.connected = connected
        self.handler = None

    def connect(self, signal, handler):
        assert signal == "connection-changed"
        self.handler = handler

    def get_property(self, name):
        assert name == "connected"
        return self.connected

    def change(self, connected):
        self.connected = connected
        self.handler(self, connected)


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


class LinuxTrayTests(unittest.TestCase):
    def setUp(self):
        self.loop = IdleLoop()
        self.connected = True
        self.created = []
        owner = self

        class FakeIcon:
            __module__ = "pystray._appindicator"

            def __init__(self, *args):
                self._appindicator = Indicator(owner.connected)
                self.visible = False
                self.stop = Mock()
                self.run = Mock(side_effect=AssertionError("GTK must share pywebview's loop"))
                self.run_detached = Mock(side_effect=lambda setup: setup(self))
                owner.created.append(self)

        self.icon_class = FakeIcon
        self.modules = {
            "gi": SimpleNamespace(require_version=Mock()),
            "gi.repository": SimpleNamespace(GLib=self.loop),
            "pystray": SimpleNamespace(Icon=FakeIcon, Menu=FakeMenu, MenuItem=Mock()),
        }
        for context in (
            patch.dict(sys.modules, self.modules),
            patch("ui.tray.platform.system", return_value="Linux"),
            patch.object(TrayIcon, "_image", return_value=object()),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.changes = []
        self.tray = TrayIcon(on_availability=self.changes.append)
        # Run queued GUI work when the background startup waits for readiness.
        self.wait_patch = patch.object(self.tray._ready, "wait", side_effect=self.loop.drain)
        self.wait_patch.start()
        self.addCleanup(self.wait_patch.stop)

    def test_linux_attaches_to_existing_gtk_loop(self):
        self.assertTrue(self.tray.start())
        icon = self.created[0]
        self.assertIsNone(self.tray._thread)
        icon.run_detached.assert_called_once()
        icon.run.assert_not_called()
        self.assertTrue(icon.visible)
        self.assertEqual(self.changes, [True])
        self.assertTrue(self.tray.start())
        self.assertEqual(len(self.created), 1)

    def test_no_host_keeps_window_accessible_and_tracks_host_restarts(self):
        self.connected = False
        self.assertFalse(self.tray.start())
        self.assertFalse(self.tray.available)
        icon = self.created[0]
        icon.stop.assert_not_called()
        icon._appindicator.change(True)
        self.assertTrue(self.tray.available)
        icon._appindicator.change(False)
        self.assertFalse(self.tray.available)
        self.assertEqual(self.changes, [True, False])

    def test_legacy_backend_is_not_reported_as_functional_tray(self):
        self.icon_class.__module__ = "pystray._gtk"
        with self.assertLogs("campus_login.tray", level="ERROR"):
            self.assertFalse(self.tray.start())
        self.assertEqual(self.created, [])
        self.assertFalse(self.tray.available)

    def test_stop_hides_indicator_on_gui_thread_and_ignores_late_host(self):
        self.assertTrue(self.tray.start())
        icon = self.created[0]
        self.tray.stop()
        self.assertFalse(self.tray.available)
        icon.stop.assert_not_called()
        self.loop.drain()
        self.assertFalse(icon.visible)
        icon.stop.assert_called_once()
        icon._appindicator.change(True)
        self.assertFalse(self.tray.available)
        self.assertEqual(self.changes, [True, False])

    def test_shutdown_before_queued_initialization_never_creates_icon(self):
        with patch.object(self.tray._ready, "wait", side_effect=lambda **kwargs: self.tray.stop()):
            self.assertFalse(self.tray.start())
        self.loop.drain()
        self.assertEqual(self.created, [])
        self.assertFalse(self.tray.start())


class GtkNotificationCleanupTests(unittest.TestCase):
    def make_icon(self, path, notifier):
        class FakeBackend:
            def __init__(self):
                self._notifier = notifier
                self._appindicator = object()

            def _remove_fs_icon(self):
                path.unlink()

            def _finalize(self):
                raise AssertionError("Upstream unconditionally closes notification ID 0")

        class GtkIcon(_GtkTrayCleanup, FakeBackend):
            pass

        return GtkIcon()

    def test_no_notification_never_contacts_dbus_and_cleans_icon_once(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".gtk-cleanup-") as directory:
            path = Path(directory) / "tray.png"
            path.write_bytes(b"temporary icon")
            notifier = SimpleNamespace(_nid=0, hide=Mock())
            icon = self.make_icon(path, notifier)
            icon._finalize()
            icon._finalize()
            notifier.hide.assert_not_called()
            self.assertFalse(path.exists())
            self.assertFalse(hasattr(icon, "_appindicator"))

    def test_existing_notification_is_closed_and_cleanup_is_idempotent(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".gtk-cleanup-") as directory:
            path = Path(directory) / "tray.png"
            path.write_bytes(b"temporary icon")
            notifier = SimpleNamespace(_nid=17, hide=Mock())
            icon = self.make_icon(path, notifier)
            icon._finalize()
            icon._finalize()
            notifier.hide.assert_called_once_with()
            self.assertFalse(path.exists())
            self.assertFalse(hasattr(icon, "_appindicator"))

    def test_partial_startup_without_notifier_still_cleans_native_resources(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".gtk-cleanup-") as directory:
            path = Path(directory) / "tray.png"
            path.write_bytes(b"temporary icon")
            icon = self.make_icon(path, None)
            icon._finalize()
            self.assertFalse(path.exists())
            self.assertFalse(hasattr(icon, "_appindicator"))

    def test_real_notification_errors_are_not_swallowed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".gtk-cleanup-") as directory:
            path = Path(directory) / "tray.png"
            path.write_bytes(b"temporary icon")
            notifier = SimpleNamespace(_nid=17, hide=Mock(side_effect=RuntimeError("unexpected failure")))
            icon = self.make_icon(path, notifier)
            with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
                icon._finalize()
            notifier.hide.assert_called_once_with()
            self.assertFalse(path.exists())
            self.assertFalse(hasattr(icon, "_appindicator"))


class MainWindowPlatformTests(unittest.TestCase):
    def make_window(self):
        app = MainWindow.__new__(MainWindow)
        app._quitting = app._cleaned = False
        app.api = Mock()
        app.window = Mock()
        app.tray = Mock(available=False)
        app._settings = SimpleNamespace(get=lambda: SimpleNamespace(start_minimized=True))
        return app

    def test_linux_uses_gtk_and_png_while_windows_retains_webview2_and_ico(self):
        for system, gui, icon in (("Linux", "gtk", ICON_PNG_PATH),
                                  ("Windows", "edgechromium", ICON_PATH)):
            with self.subTest(system=system):
                app = self.make_window()
                calls = []

                def start(callback, gui=None, debug=True, icon=None):
                    calls.append((callback, gui, debug, icon))

                app._webview = SimpleNamespace(start=start)
                app._cleanup = Mock()
                with patch("ui.main_window.platform.system", return_value=system):
                    app.run()
                self.assertEqual(calls, [(app._start_background, gui, False, str(icon))])
                app._cleanup.assert_called_once()

    def test_linux_sets_native_icon_title_and_matching_window_class(self):
        app = self.make_window()
        with patch("ui.main_window.platform.system", return_value="Linux"):
            app._apply_native_icon()
        app.window.native.set_icon_from_file.assert_called_once_with(str(ICON_PNG_PATH))
        app.window.native.set_wmclass.assert_called_once_with(APP_ID, APP_ID)
        app.window.native.set_title.assert_called_once_with(APP_TITLE)

    def test_linux_wayland_id_matches_desktop_filename(self):
        glib = SimpleNamespace(set_prgname=Mock(), set_application_name=Mock())
        modules = {"gi": SimpleNamespace(require_version=Mock()),
                   "gi.repository": SimpleNamespace(GLib=glib)}
        with patch.dict(sys.modules, modules), \
                patch("ui.main_window.platform.system", return_value="Linux"):
            MainWindow._set_application_id()
        glib.set_prgname.assert_called_once_with("wofo-login")
        glib.set_application_name.assert_called_once_with("WOFO Login")

    def test_missing_tray_prevents_start_minimized_and_close_hiding(self):
        app = self.make_window()
        app.tray.start.return_value = False
        app._start_background()
        app.api._set_tray_available.assert_called_once_with(False)
        app.api._start.assert_called_once()
        app.window.hide.assert_not_called()
        with self.assertRaises(RuntimeError):
            app._hide_window()
        self.assertTrue(app._on_closing())
        app.window.hide.assert_not_called()

    def test_losing_tray_restores_window_and_disables_hide_action(self):
        app = self.make_window()
        app._tray_availability_changed(False)
        app.api._set_tray_available.assert_called_once_with(False)
        app.window.show.assert_called_once()
        app.window.restore.assert_called_once()
        app._quitting = True
        app._tray_availability_changed(False)
        app.window.show.assert_called_once()


class LinuxAutoStartTests(unittest.TestCase):
    def test_desktop_entry_uses_current_name_without_changing_windows_key(self):
        manager = AutoStartManager()
        args = ["/opt/WOFO Login/WOFO Login"]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix=".linux-test-") as directory:
            with patch.object(Path, "home", return_value=Path(directory)), \
                    patch.object(manager, "_get_app_args", return_value=args):
                self.assertTrue(manager._enable_linux())
            content = (Path(directory) / ".config/autostart/campus_login.desktop").read_text(
                encoding="utf-8",
            )
        self.assertIn(f"Name={APP_TITLE}\n", content)
        self.assertIn('Exec="/opt/WOFO Login/WOFO Login"\n', content)
        self.assertIn(f"Icon={ICON_PNG_PATH}\n", content)
        self.assertIn("Terminal=false\n", content)
        self.assertEqual(manager._get_app_name(), "校园网自动登录")


if __name__ == "__main__":
    unittest.main()
