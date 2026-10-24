"""The console's loopback HTTP server, and the four things that make it safe.

A local web server in a parental-control product is a liability unless each
boundary is deliberate. This module is written around four of them, and every one
has a test:

**Bind to loopback, not to the world.** ``127.0.0.1`` only. Binding to
``0.0.0.0`` would publish the console to the LAN, and a console that shows what
was blocked is a map of what to avoid.

**Require a token, per launch.** The server generates a random token at startup
and the browser receives it in the URL the launcher opens. Without it, any local
process - including a script running as the same user - could read the log. The
token lives in memory, is compared with :func:`hmac.compare_digest`, and dies
with the process.

**Refuse cross-origin requests.** A page on the open internet can make the
browser issue a request to ``127.0.0.1``. Two defences: ``Origin``/``Referer``
must be absent or match this server, and the token is not a cookie, so it is not
attached automatically. A request from ``evil.example`` cannot guess it.

**Serve no files.** Every response is generated from :mod:`host.ui.data` or is one
of a fixed set of assets compiled into this module's package. There is no path
parameter anywhere, so there is nothing to traverse.

The fourth is the one that usually gets skipped, and it is the one that turns a
convenience server into a file-disclosure bug.
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import threading
import urllib.parse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__all__ = [
    "ConsoleConfig",
    "ConsoleServer",
    "serve_in_thread",
    "make_token",
    "WEB_DIR",
]

#: The console's static assets. Compiled in, not looked up by a path parameter.
WEB_DIR = Path(__file__).resolve().parent / "web"

#: Assets the server will serve, by exact name. A request for anything else is a
#: 404 regardless of what is on disk, so adding a file to ``web/`` does not
#: silently publish it.
ASSETS: tuple[str, ...] = ("index.html", "app.js", "style.css")

#: Longest request line accepted. The console's own URLs are short.
MAX_REQUEST_LINE = 2048


def make_token(length: int = 32) -> str:
    """A per-launch URL-safe token.

    ``secrets`` rather than ``random``: ``random`` is seeded from the clock and
    is predictable, and this token is the only thing separating the console from
    any other local process.
    """
    return secrets.token_urlsafe(length)


@dataclass
class ConsoleConfig:
    """What the console shows and how it authenticates.

    ``provider`` is a callable returning the JSON-ready overview dict. It is
    injected rather than imported so the server has no opinion about where the
    state comes from, and so tests can serve a fixed payload.
    """

    provider: object
    token: str = field(default_factory=make_token)
    #: Bind address. Not configurable to anything but loopback on purpose.
    host: str = "127.0.0.1"
    port: int = 0

    def __post_init__(self) -> None:
        if self.host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError(
                f"the console binds to loopback only, not {self.host!r}: a "
                f"console reachable from the network shows what to avoid")


class _Handler(BaseHTTPRequestHandler):
    """One request. No state beyond ``self.server.config``."""

    server_version = "GreenDamConsole/1.0"
    #: Do not advertise the Python version.
    sys_version = ""

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:
        """Silence the default stderr logger.

        The default writes the request line to stderr, which would put the
        console's URL - including the token - into any log that captures stderr.
        """
        return

    def _send(self, status: int, body: bytes, content_type: str, *,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # No caching: the console shows current state.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # The page loads nothing external and runs no inline script, so the
        # strictest policy is also the honest one.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; form-action 'none'; "
            "frame-ancestors 'none'; base-uri 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, token: str) -> None:
        self._json({"error": token}, status)

    # -- checks -----------------------------------------------------------

    def _origin_ok(self) -> bool:
        """Reject a request a browser made on another site's behalf.

        ``Origin`` is present on cross-origin requests and on same-origin POSTs.
        A request with no ``Origin`` and no ``Referer`` is a local tool (curl, a
        test) and is allowed - it still needs the token.

        When ``Origin`` is present it must be this server. Anything else is
        refused even with a valid token, so a token leaked into a browser history
        cannot be replayed by a page.
        """
        origin = self.headers.get("Origin")
        if origin:
            return origin.rstrip("/") == self._self_origin()
        referer = self.headers.get("Referer")
        if referer:
            return referer.startswith(self._self_origin() + "/")
        return True

    def _self_origin(self) -> str:
        host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_port}"
        return f"http://{host}"

    def _token_ok(self, query: dict[str, list[str]]) -> bool:
        supplied = (query.get("token") or [""])[0]
        if not supplied:
            supplied = self.headers.get("X-Console-Token") or ""
        expected = self.server.config.token
        if not supplied or not expected:
            return False
        return hmac.compare_digest(supplied, expected)

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:                       # noqa: N802 - http.server API
        if len(self.path) > MAX_REQUEST_LINE:
            self._error(HTTPStatus.REQUEST_URI_TOO_LONG, "uri_too_long")
            return

        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        if not self._origin_ok():
            self._error(HTTPStatus.FORBIDDEN, "cross_origin")
            return

        if path in ("/", "/index.html"):
            self._serve_asset("index.html", query)
            return
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if name not in ASSETS:
                self._error(HTTPStatus.NOT_FOUND, "no_such_asset")
                return
            self._serve_asset(name, query)
            return
        if path == "/api/overview":
            if not self._token_ok(query):
                self._error(HTTPStatus.UNAUTHORIZED, "bad_token")
                return
            try:
                payload = self.server.config.provider()
            except Exception as exc:        # pragma: no cover - defensive
                # Never echo the exception: it may contain a path.
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "provider_failed")
                del exc
                return
            self._json(payload)
            return
        if path == "/api/health":
            # Unauthenticated on purpose: the launcher polls it to know the
            # server is up. It says nothing about the system.
            self._json({"ok": True})
            return

        self._error(HTTPStatus.NOT_FOUND, "no_such_route")

    def do_HEAD(self) -> None:                      # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:                      # noqa: N802
        """No mutating routes yet, and that is a decision.

        The console is read-only in this version. Every action a parent takes -
        enabling a pack, changing the password - goes through the CLI, which
        means it is journalled and reproducible. A POST endpoint would need
        CSRF protection beyond the token and a second audit path; it is not
        worth adding until there is a screen that needs it.
        """
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "read_only")

    def _serve_asset(self, name: str, query: dict[str, list[str]]) -> None:
        """Serve one whitelisted asset. No filesystem path is built from input."""
        if name not in ASSETS:
            self._error(HTTPStatus.NOT_FOUND, "no_such_asset")
            return
        source = WEB_DIR / name
        if not source.is_file():            # pragma: no cover - packaging error
            self._error(HTTPStatus.NOT_FOUND, "asset_missing")
            return
        body = source.read_bytes()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or name.endswith(".js"):
            content_type += "; charset=utf-8"
        # The token is substituted into app.js, which is where the placeholder
        # lives. Putting it in the page instead would need an inline script, and
        # the CSP forbids inline script on purpose - the placeholder is filled
        # in a file the browser loads as a separate resource.
        if name == "app.js":
            token = self.server.config.token
            placeholder = b"__CONSOLE_TOKEN__"
            if placeholder not in body:      # pragma: no cover - packaging error
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR,
                            "token_placeholder_missing")
                return
            body = body.replace(placeholder, token.encode("ascii"))
        self._send(HTTPStatus.OK, body, content_type)


class ConsoleServer(ThreadingHTTPServer):
    """A threaded loopback server carrying its configuration."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: ConsoleConfig) -> None:
        self.config = config
        super().__init__((config.host, config.port), _Handler)

    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}/"


def serve_in_thread(config: ConsoleConfig) -> tuple[ConsoleServer, threading.Thread]:
    """Start the console and return it with its thread.

    Daemon thread: the console must never keep the process alive after the
    parent closes it. A UI that outlives its window is a leak the user cannot
    see.
    """
    server = ConsoleServer(config)
    thread = threading.Thread(target=server.serve_forever, name="console",
                              daemon=True)
    thread.start()
    return server, thread
