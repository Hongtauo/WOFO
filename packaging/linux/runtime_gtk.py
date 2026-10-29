"""Select the packaged Jammy WebKit ABI before pywebview chooses a backend."""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.0")
gi.require_version("Soup", "2.4")
