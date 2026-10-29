WOFO Login · 佛山大学校园网断线守护
Ubuntu 22.04 desktop / amd64

Recommended installation:
  sudo apt install ./WOFO-Login_1.0.1_ubuntu22.04_amd64.deb
Then open WOFO Login from the application menu, or run wofo-login-gui.

Portable archive:
  Install the native runtime libraries listed below, extract the complete
  archive, and run ./run.sh. Keep app/ beside run.sh.
  sudo apt install libgtk-3-0 libwebkit2gtk-4.0-37 \
    gir1.2-gtk-3.0 gir1.2-webkit2-4.0 gir1.2-ayatanaappindicator3-0.1 \
    gir1.2-freedesktop \
    libgirepository-1.0-1 libcairo2 libffi8 libsqlite3-0 libssl3 \
    libbz2-1.0 liblzma5 libexpat1 zlib1g fonts-noto-cjk
Python is bundled. No Python installation or pip command is needed to run it.
The complete dependencies for this build are in runtime-dependencies.txt.

An X11 or Wayland desktop session is required. For the tray icon, enable the
Ubuntu AppIndicators extension when using GNOME. The window can still be used
when a desktop environment does not show the tray icon.

Uninstall the .deb:
  sudo apt remove wofo-login-gui
For the portable build, exit the application and delete its extracted folder.
Both methods preserve ~/.config/campus_login/, including accounts and settings.
