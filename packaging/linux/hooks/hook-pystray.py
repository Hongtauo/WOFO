"""Collect just the selected GTK tray backend without importing a display."""

hiddenimports = [
    "pystray._appindicator",
    "pystray._util.gtk",
    "pystray._util.notify_dbus",
    "gi.repository.AyatanaAppIndicator3",
    "gi.repository.DBus",
    "gi.repository.Gio",
    "gi.repository.GLib",
]
