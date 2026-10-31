"""Desktop app: runs the translator on this computer in its own window, with no web browser.

The window shows the same page as the browser version, served from a private address on this computer
(127.0.0.1). A small bridge lets the page use the computer: add events to the calendar app, save files,
open links, and remember the API key.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

APP_NAME = "Medical Paperwork Translator"
KEY_FILE = "anthropic_api_key.txt"
log = logging.getLogger(__name__)


def data_dir() -> Path:
    """Where the app keeps its settings, saved key, log, and calendar files."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_saved_key() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    path = data_dir() / KEY_FILE
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key


def save_key(key: str) -> None:
    path = data_dir() / KEY_FILE
    key = key.strip()
    if key:
        path.write_text(key, encoding="utf-8")
        try:
            path.chmod(0o600)  # only this user can read it
        except OSError:
            pass
        os.environ["ANTHROPIC_API_KEY"] = key
    else:
        path.unlink(missing_ok=True)
        os.environ.pop("ANTHROPIC_API_KEY", None)


def open_with_default_app(path: Path) -> None:
    """Open a file the way double-clicking it would, e.g. a .ics file opens the calendar app."""
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows only
    else:
        subprocess.run(["xdg-open", str(path)], check=True)


def safe_filename(name: str, default: str) -> str:
    name = re.sub(r"[^\w\- .]", "", name).strip(" .")[:80]
    return name or default


class DesktopBridge:
    """Functions the page can call as window.pywebview.api.<name>(...). Every one returns plain JSON data."""

    def __init__(self) -> None:
        self._window = None  # set once the window exists; underscore keeps it private from the page

    def add_to_calendar(self, ics_text: str, filename: str = "appointment.ics") -> dict:
        if not isinstance(ics_text, str) or not ics_text.startswith("BEGIN:VCALENDAR"):
            return {"ok": False, "error": "That isn't a calendar event."}
        folder = data_dir() / "calendar"
        folder.mkdir(exist_ok=True)
        name = safe_filename(filename, "appointment.ics")
        if not name.endswith(".ics"):
            name += ".ics"
        path = folder / name
        path.write_text(ics_text, encoding="utf-8", newline="")
        try:
            open_with_default_app(path)
        except Exception as exc:  # no calendar app set up
            log.warning("Couldn't open %s: %s", path, exc)
            return {"ok": False, "error": "Couldn't open your calendar app. You can save the file instead.", "path": str(path)}
        return {"ok": True, "path": str(path)}

    def save_text_file(self, text: str, filename: str = "my-paper-summary.txt") -> dict:
        import webview

        if self._window is None:
            return {"ok": False, "error": "The app window isn't ready yet."}
        result = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=safe_filename(filename, "summary.txt"))
        if not result:
            return {"ok": False, "cancelled": True}
        path = Path(result if isinstance(result, str) else result[0])
        path.write_text(text, encoding="utf-8")
        return {"ok": True, "path": str(path)}

    def open_url(self, url: str) -> dict:
        if not isinstance(url, str) or urllib.parse.urlparse(url).scheme not in ("https", "http", "mailto"):
            return {"ok": False}
        webbrowser.open(url)
        return {"ok": True}

    def api_key_status(self) -> dict:
        return {"configured": bool(os.environ.get("ANTHROPIC_API_KEY"))}

    def save_api_key(self, key: str) -> dict:
        key = (key or "").strip()
        if key and not key.startswith("sk-"):
            return {"ok": False, "error": "That doesn't look like an Anthropic API key. Keys start with sk-."}
        save_key(key)
        return {"ok": True, "configured": bool(key)}


def pick_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """The translator's web server, running in a background thread on a private port."""

    def __init__(self, port: int | None = None) -> None:
        from .app import app

        self.port = port or pick_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_config=None, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="translator-server", daemon=True)

    def start(self, timeout: float = 30) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive():
                raise RuntimeError("The translator stopped while starting. See log.txt in the app's folder.")
            if time.monotonic() > deadline:
                raise RuntimeError("The translator took too long to start.")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def check_server(url: str) -> list[str]:
    """Load the page and a sample paper. Returns a list of problems (empty means everything works)."""
    problems = []

    def fetch(path: str, data: bytes | None = None) -> bytes:
        with urllib.request.urlopen(url.rstrip("/") + path, data=data, timeout=20) as response:
            return response.read()

    try:
        if b"Medical Paperwork Translator" not in fetch("/"):
            problems.append("the page didn't load")
        for asset in ("/app.js", "/scanner.js", "/styles.css"):
            fetch(asset)
        config = json.loads(fetch("/api/config"))
        if len(config.get("samples", [])) != 3:
            problems.append("the sample papers are missing")
        result = json.loads(fetch("/api/translate", data=b"sample_id=bill"))
        if sum(c["source"] == "math_check" for c in result["concerns"]) != 3:
            problems.append("the bill check didn't find the sample's mistakes")
    except Exception as exc:
        problems.append(f"{type(exc).__name__}: {exc}")
    return problems


def setup_logging() -> Path:
    log_path = data_dir() / "log.txt"
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, mode="w", encoding="utf-8")]
    if sys.stderr is not None:  # windowed apps on Windows have no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers, force=True)
    return log_path


def run_window(server: Server, smoke_test: bool = False) -> int:
    """Show the app window. With smoke_test, open it, check the page drew, then close (used by the build)."""
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    bridge = DesktopBridge()
    window = webview.create_window(
        APP_NAME, server.url, js_api=bridge, width=1000, height=860, min_size=(360, 480), text_select=True
    )
    bridge._window = window
    outcome = {"code": 0}

    def smoke() -> None:
        try:
            if not window.events.loaded.wait(60):
                raise RuntimeError("the window never finished loading")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                ready = window.evaluate_js(
                    "document.querySelectorAll('.sample-button').length === 3 && Boolean(window.pywebview && window.pywebview.api)"
                )
                if ready:
                    log.info("Window smoke test passed: the page drew and the bridge is connected.")
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("the page or the bridge never became ready")
        except Exception as exc:
            log.error("Window smoke test failed: %s", exc)
            outcome["code"] = 1
        finally:
            window.destroy()

    webview.start(
        func=smoke if smoke_test else None,
        private_mode=False,  # keep display settings between launches
        storage_path=str(data_dir() / "webview"),
    )
    return outcome["code"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"Run the {APP_NAME} on this computer.")
    parser.add_argument("--self-test", action="store_true", help="Check the translator works, then quit.")
    parser.add_argument("--smoke-test-window", action="store_true", help="Open the window, check it, then quit.")
    # macOS can pass extra arguments such as -psn_0_12345 when an app is opened from Finder.
    args, _ = parser.parse_known_args(argv)

    log_path = setup_logging()
    load_saved_key()
    try:
        server = Server()
        server.start()
    except Exception:
        log.exception("Couldn't start the translator")
        sys.exit(1)
    log.info("Translator running at %s (log: %s)", server.url, log_path)

    if args.self_test:
        problems = check_server(server.url)
        try:
            import webview  # noqa: F401  # confirms the window library was packaged

            if sys.platform == "darwin":
                import webview.platforms.cocoa  # noqa: F401
            elif os.name == "nt":
                import webview.platforms.winforms  # noqa: F401
        except Exception as exc:
            problems.append(f"window library missing: {exc}")
        for problem in problems:
            log.error("Self-test problem: %s", problem)
        log.info("Self-test %s", "failed" if problems else "passed")
        server.stop()
        sys.exit(1 if problems else 0)

    code = run_window(server, smoke_test=args.smoke_test_window)
    server.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()
