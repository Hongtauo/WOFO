"""Start the unmodified Linux distributable in an isolated X11 test session.

    dbus-run-session -- xvfb-run -a -s '-screen 0 1440x1100x24' \
        .venv/bin/python tests/smoke_linux_package.py \
        dist/linux/wofo-login-gui/wofo-login-gui --output-dir dist/linux-package-smoke

The executable uses a temporary HOME/XDG tree and a loopback-only simulated HTTP
proxy. A real visible window must render before its X11 WM_DELETE_WINDOW close
request. No credentials or production-only test hooks are used. Requires xdotool,
GTK 3 system bindings, Pillow and an X11 display (Xvfb in CI).
"""

import argparse
import ctypes
import ctypes.util
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class ConnectivityProxy(BaseHTTPRequestHandler):
    """Simulate connectivity checks locally; never forward requests."""

    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def do_CONNECT(self):
        self.send_error(403)

    def log_message(self, *_):
        pass


def send_close_request(window_id):
    """Send WM_DELETE_WINDOW, without requiring an installed window manager.

    xdotool windowclose destroys an X window directly. The proper client message
    lets GTK run the application's closing and cleanup handlers instead.
    """
    class ClientData(ctypes.Union):
        _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10),
                    ("l", ctypes.c_long * 5)]

    class ClientMessage(ctypes.Structure):
        _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                    ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                    ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                    ("format", ctypes.c_int), ("data", ClientData)]

    class XEvent(ctypes.Union):
        _fields_ = [("xclient", ClientMessage), ("pad", ctypes.c_long * 24)]

    library = ctypes.util.find_library("X11")
    if not library:
        raise RuntimeError("libX11 is unavailable")
    xlib = ctypes.CDLL(library)
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    xlib.XInternAtom.restype = ctypes.c_ulong
    xlib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                               ctypes.c_long, ctypes.POINTER(XEvent)]
    xlib.XSendEvent.restype = ctypes.c_int
    xlib.XFlush.argtypes = [ctypes.c_void_p]
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = xlib.XOpenDisplay(None)
    if not display:
        raise RuntimeError("Cannot connect to the X11 display")
    try:
        event = XEvent()
        event.xclient.type = 33  # ClientMessage
        event.xclient.send_event = 1
        event.xclient.display = display
        event.xclient.window = window_id
        event.xclient.message_type = xlib.XInternAtom(display, b"WM_PROTOCOLS", 0)
        event.xclient.format = 32
        event.xclient.data.l[0] = xlib.XInternAtom(display, b"WM_DELETE_WINDOW", 0)
        event.xclient.data.l[1] = 0  # CurrentTime
        if not xlib.XSendEvent(display, window_id, 0, 0, ctypes.byref(event)):
            raise RuntimeError("X11 rejected the window close request")
        xlib.XFlush(display)
    finally:
        xlib.XCloseDisplay(display)


def metadata(directory):
    """Compare original user configuration without reading any account content."""
    if not directory.is_dir():
        return None
    return {str(path.relative_to(directory)): (path.stat().st_size, path.stat().st_mtime_ns)
            for path in directory.rglob("*") if path.is_file()}


def sanitize_log(text, isolated=None):
    """Retain actionable GI errors from this empty test account environment."""
    replacements = [(str(ROOT), "<workspace>"), (str(Path.home()), "<original-home>")]
    if isolated is not None:
        replacements.insert(0, (str(isolated), "<test-root>"))
    for path, replacement in replacements:
        text = text.replace(path, replacement)
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1<redacted>@", text)
    text = re.sub(r"(?i)\b(password|passwd|authorization|api[_-]?key|secret)"
                  r"(\s*[:=]\s*)([^\s,;&]+)", r"\1\2<redacted>", text)
    return text[-65536:]


def failure_diagnostics(output, captured_log, isolated=None):
    """Persist bounded diagnostics and emit the last real application traceback."""
    log_text = sanitize_log("".join(captured_log), isolated)
    (output / "application.log").write_text(log_text, encoding="utf-8")
    marker = "Traceback (most recent call last)"
    position = log_text.rfind(marker)
    diagnostic = log_text[position:] if position >= 0 else "\n".join(log_text.splitlines()[-30:])
    diagnostic = diagnostic[-10000:]
    if diagnostic:
        print("Packaged application failure diagnostics (isolated empty test data):", file=sys.stderr)
        print(diagnostic, file=sys.stderr)
    return log_text


def run_xdotool(*arguments, allow_missing=False):
    result = subprocess.run(["xdotool", *map(str, arguments)], capture_output=True,
                            text=True, timeout=5, check=False)
    if result.returncode and not (allow_missing and result.returncode == 1):
        raise RuntimeError(f"xdotool {arguments[0]} failed with code {result.returncode}")
    return result.stdout.strip()


def snapshot(path, geometry):
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    from PIL import Image

    Gdk.init([])
    root = Gdk.get_default_root_window()
    if root is None:
        raise RuntimeError("No X11 root window is available")
    pixels = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
    if pixels is None:
        raise RuntimeError("Cannot capture the X11 root window")
    pixels.savev(str(path), "png", [], [])
    with Image.open(path) as image:
        x, y, width, height = (geometry[name] for name in ("X", "Y", "WIDTH", "HEIGHT"))
        crop = image.crop((x, y, x + width, y + height)).convert("RGB").resize((160, 120))
        colors = crop.getcolors(160 * 120) or []
        # A mapped window with only its flat startup background is insufficient.
        largest_area = max((count for count, _ in colors), default=160 * 120)
        return len(colors) > 60 and largest_area < 160 * 120 * 0.95


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "linux-package-smoke")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("This check requires Linux and an isolated X11 display")
    executable = args.executable.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        parser.error("The executable must be an existing executable Linux program")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    from ui.branding import APP_TITLE

    original_config = Path.home() / ".config" / "campus_login"
    baseline = metadata(original_config)
    report = {"ok": False, "checks": []}
    process = None
    captured_log = []
    log_reader = None
    isolated = None
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ConnectivityProxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="wofo-package-smoke-") as directory:
            isolated = Path(directory)
            home = isolated / "home"
            env = os.environ.copy()
            locations = {"HOME": home, "XDG_CONFIG_HOME": home / ".config",
                         "XDG_DATA_HOME": home / ".local" / "share",
                         "XDG_STATE_HOME": home / ".local" / "state",
                         "XDG_CACHE_HOME": home / ".cache",
                         "XDG_RUNTIME_DIR": isolated / "runtime", "TMPDIR": isolated / "tmp"}
            for name, location in locations.items():
                location.mkdir(parents=True, mode=0o700, exist_ok=True)
                env[name] = str(location)
            proxy_url = f"http://127.0.0.1:{proxy.server_port}"
            for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
                env[name] = proxy_url
            for name in ("no_proxy", "NO_PROXY"):
                env[name] = "localhost,127.0.0.1,::1"
            env["GDK_BACKEND"] = "x11"
            # Do not allow a developer's PYTHONPATH to supply missing frozen code.
            env.pop("PYTHONPATH", None)
            process = subprocess.Popen([str(executable)], cwd=isolated, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)

            def drain_log():
                for line in iter(process.stdout.readline, b""):
                    # Bound memory and never print raw application log text.
                    captured_log.append(line.decode("utf-8", errors="replace"))
                    if len(captured_log) > 1000:
                        del captured_log[:500]

            log_reader = threading.Thread(target=drain_log, daemon=True)
            log_reader.start()
            pattern = "^" + re.escape(APP_TITLE) + "$"
            deadline = time.monotonic() + 45
            window_id = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError(f"Packaged application exited before showing a window ({process.returncode})")
                matches = run_xdotool("search", "--onlyvisible", "--name", pattern, allow_missing=True)
                if matches:
                    window_id = int(matches.splitlines()[0])
                    break
                threading.Event().wait(0.2)
            if window_id is None:
                raise AssertionError("Packaged application did not show its GTK window within 45 seconds")
            assert run_xdotool("getwindowname", window_id) == APP_TITLE
            geometry = dict(line.split("=", 1) for line in
                            run_xdotool("getwindowgeometry", "--shell", window_id).splitlines())
            geometry = {name: int(value) for name, value in geometry.items()}
            assert geometry["WIDTH"] >= 800 and geometry["HEIGHT"] >= 620
            report["checks"].extend(["production_binary_starts", "native_title", "visible_gtk_window"])
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                assert process.poll() is None, "Packaged application exited while rendering"
                if snapshot(output / "package-overview.png", geometry):
                    break
                threading.Event().wait(0.3)
            else:
                raise AssertionError("Packaged WebKit window remained blank")
            report["checks"].append("packaged_webkit_content_renders")

            send_close_request(window_id)
            try:
                exit_code = process.wait(timeout=15)
            except subprocess.TimeoutExpired as exc:
                raise AssertionError("Packaged application did not quit after WM_DELETE_WINDOW") from exc
            assert exit_code == 0, f"Packaged application exited with code {exit_code}"
            if log_reader:
                log_reader.join(timeout=2)
            log_text = "".join(captured_log)
            report["application_traceback"] = "Traceback (most recent call last)" in log_text
            assert not report["application_traceback"], "Packaged application emitted a traceback"
            report["checks"].append("normal_window_close_and_clean_exit")

            database = home / ".config" / "campus_login" / "accounts.db"
            assert database.is_file(), "The application did not create its isolated database"
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
            assert not (home / ".config" / "autostart" / "campus_login.desktop").exists()
            assert metadata(original_config) == baseline, "Original user configuration changed"
            report["checks"].extend(["temporary_home_and_empty_database", "original_user_config_unchanged"])
            report.update(ok=True, exit_code=exit_code,
                          viewport={"width": geometry["WIDTH"], "height": geometry["HEIGHT"]},
                          snapshot="package-overview.png")
    except BaseException as exc:
        traceback.print_exc()
        log_text = "".join(captured_log)
        report.update(ok=False, error=f"{type(exc).__name__}: {exc}",
                      application_traceback="Traceback (most recent call last)" in log_text,
                      application_exception_types=sorted(set(re.findall(
                          r"(?m)^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):", log_text))))
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if log_reader:
            log_reader.join(timeout=2)
        if not report["ok"]:
            log_text = failure_diagnostics(output, captured_log, isolated)
            report["application_log"] = "application.log"
            report["application_traceback"] = "Traceback (most recent call last)" in log_text
            report["application_exception_types"] = sorted(set(re.findall(
                r"(?m)^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):", log_text)))
        proxy.shutdown()
        proxy.server_close()

    serialized = json.dumps(report, ensure_ascii=True, indent=2)
    (output / "report.json").write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
