"""Opening the console in whatever window this machine can provide.

Three routes, tried in order, each strictly better than the next:

1. **WebView2 via ``pywebview``** - a real application window, no browser chrome,
   no address bar for a child to edit. Optional dependency.
2. **Edge in app mode** (``msedge --app=URL``) - a chromeless window from a
   browser that is present on every Windows 10/11 install. No dependency at all.
3. **The default browser** - always works, and is the only option if Edge has
   been removed. It comes with tabs and an address bar, which is why it is last.

Why not a native toolkit
------------------------
The project already ships an extension written in JavaScript, so a second UI
language buys nothing, and a web view renders the same HTML in all three routes.
The console is written once.

Why the fallback chain matters
------------------------------
``docs/architecture.md`` §M9 names three options because a hard dependency on
WebView2 would make the console fail on machines where it is absent - and the
console is how a parent finds out the filter is not working. A UI that cannot
start when something is missing is worse than a plain one that always starts.

The launcher never blocks: it starts the server, opens a window, and waits for
the process to be closed (or for the browser to be told to close). If no window
can be opened, it prints the URL and keeps serving, so the parent can paste it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .server import ConsoleConfig, ConsoleServer, serve_in_thread

__all__ = ["LaunchResult", "open_console", "find_edge", "EDGE_PATHS"]

#: Where Edge lives, 64-bit then 32-bit. Read from disk rather than from the
#: registry because ``--app`` needs the executable, not the App Paths entry.
EDGE_PATHS: tuple[str, ...] = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


@dataclass
class LaunchResult:
    """What happened, for logging and for tests."""

    url: str
    route: str
    #: The child process, when one was started. ``None`` for the browser route.
    process: object | None = None
    #: True when the console is serving but no window could be opened.
    headless: bool = False


def find_edge() -> str | None:
    """Path to ``msedge.exe``, or None. Never raises."""
    for candidate in EDGE_PATHS:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("msedge")


def _try_pywebview(url: str, title: str) -> bool:
    """Open a WebView2 window and block until it closes.

    Returns False if ``pywebview`` is missing or fails to start, so the caller
    can fall back. A failure here is not an error: it is the expected outcome on
    a machine without the optional dependency.
    """
    try:
        import webview                       # noqa: PLC0415 - optional
    except ImportError:
        return False

    try:
        webview.create_window(title, url, width=980, height=760)
        webview.start()
        return True
    except Exception:                        # pragma: no cover - depends on runtime
        return False


def _try_edge_app(edge: str, url: str) -> object | None:
    """Start Edge in app mode. Returns the process, or None on failure.

    ``--app`` gives a window with no address bar and no tabs, which is what makes
    the console feel like an application rather than a web page. A dedicated
    ``--user-data-dir`` is deliberately *not* used: sharing the profile means the
    window inherits the user's language and zoom, and creating a profile
    directory would leave something to clean up.
    """
    try:
        return subprocess.Popen(
            [edge, f"--app={url}", "--window-size=980,760"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def open_console(config: ConsoleConfig, *, title: str = "绿坝·花季护航 2026",
                 route: str = "auto", block: bool = True) -> tuple[LaunchResult,
                                                                  ConsoleServer]:
    """Start the console and open a window for it.

    ``route`` forces a specific path, which is what the tests use: ``"auto"``
    (the default), ``"webview"``, ``"edge"``, ``"browser"``, or ``"none"`` to
    serve without opening anything.

    ``block`` waits for the window to close. With ``route="browser"`` there is
    nothing to wait for, so the call returns immediately and ``headless`` is
    False - the browser owns the window.
    """
    server, _thread = serve_in_thread(config)
    url = server.url

    if route == "none":
        return LaunchResult(url=url, route="none", headless=True), server

    if route in ("auto", "webview"):
        if _try_pywebview(url, title):
            return LaunchResult(url=url, route="webview"), server
        if route == "webview":
            return LaunchResult(url=url, route="webview-failed",
                                headless=True), server

    if route in ("auto", "edge"):
        edge = find_edge()
        if edge:
            process = _try_edge_app(edge, url)
            if process is not None:
                if block:
                    try:
                        process.wait()
                    except KeyboardInterrupt:        # pragma: no cover
                        process.terminate()
                return LaunchResult(url=url, route="edge",
                                    process=process), server
        if route == "edge":
            return LaunchResult(url=url, route="edge-failed",
                                headless=True), server

    # Last resort. ``webbrowser.open`` returns False when it finds nothing.
    opened = webbrowser.open(url)
    if not opened:
        return LaunchResult(url=url, route="none", headless=True), server

    if block:
        # Nothing to wait on: the browser is a separate, possibly already-running
        # process. Keep serving until interrupted, because closing the server the
        # moment the call returns would break the page that just opened.
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:                # pragma: no cover
            pass
    return LaunchResult(url=url, route="browser"), server
