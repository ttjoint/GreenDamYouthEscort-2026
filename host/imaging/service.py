"""The local image-scoring service the browser extension talks to.

``host/extension/background.js`` POSTs image bytes to
``http://127.0.0.1:17871/score`` and hides an element when the reply's score
crosses the threshold. Until this module existed, **nothing answered on that
port**: every image the extension tried to judge came back ``null`` and stayed
visible. The image path read as finished because the rules were tested, the
model was tested, and the gap between them - an HTTP endpoint - was nobody's
test.

Why a service rather than a model inside the extension
-----------------------------------------------------
An ONNX runtime plus a 21 MB model inside a browser extension is not something a
store would accept, and the host already needs the same model for the proxy's
image path. One model, one place. The extension therefore sends *bytes* to
loopback and gets a number back.

Why loopback and not a named pipe or a window message
-----------------------------------------------------
The extension is a browser sandbox with no IPC surface of its own, so HTTP is the
only transport both sides already speak. It is bound to ``127.0.0.1`` for the
same reason the console is: a scoring endpoint reachable from the LAN is an
oracle that tells anyone on the network whether a picture is objectionable.

Three boundaries, each with a test
----------------------------------
**Loopback only.** :class:`ScoringConfig` refuses any other bind address. This is
the same rule as ``host.ui.server``, and it matters more here: the reply is a
judgement about a picture, so it is a way to ask questions about content.

**Only extension origins may read the reply.** A page on the open internet can
make a browser POST to ``127.0.0.1``. Requests from ``chrome-extension://`` and
``moz-extension://`` are allowed, requests with no ``Origin`` are allowed (a local
tool, a test), and anything else is refused. Without this, any site could use the
parent's classifier as a free NSFW oracle - and could measure the wordlists'
sibling, the image threshold, by probing.

**The body is bounded before it is decoded.** A ``Content-Length`` over
:data:`MAX_IMAGE_BYTES` is refused, and the decoded frame is refused above
:data:`MAX_PIXELS` *before* ``Image.convert`` runs. A decompression bomb is cheap
to send and expensive to decode; the size check happens on the header, not on the
pixels.

What the service never does
---------------------------
It stores nothing, logs no bytes, and answers ``assessed: false`` - not a zero
score - when there is no model or the frame cannot be judged. That distinction is
the whole project's rule: "not looked at" must never read as "clean", and the
extension already honours it by leaving the image visible on ``null``.
"""
from __future__ import annotations

import io
import json
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import UnusableFrame

__all__ = [
    "DEFAULT_PORT",
    "MAX_IMAGE_BYTES",
    "MAX_PIXELS",
    "ScoringConfig",
    "ScoringServer",
    "decode_frame",
    "extension_origin",
    "serve_in_thread",
]

#: The port ``background.js`` and ``options.js`` already point at. Changing it
#: here without changing the extension turns scoring into a silent no-op, which
#: is the failure this module was written to end.
DEFAULT_PORT = 17871

#: Largest request body accepted. A 4K JPEG is well under 1 MB; 8 MB is generous
#: for a PNG screenshot and small enough that a few concurrent requests cannot
#: exhaust memory.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: Largest frame accepted, in pixels. 4096x4096 is above any display and keeps a
#: decompression bomb from being decoded at all: the check runs on the header.
MAX_PIXELS = 4096 * 4096

#: Scheme prefixes that identify a browser extension origin. A web page's origin
#: is ``http(s)://...`` and is refused.
EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://")


@dataclass
class ScoringConfig:
    """What the service needs. All fields have safe defaults.

    ``backend`` is an :class:`green_dam.imagepath.ImageBackend` - usually a
    :class:`~host.imaging.MarqoOnnxBackend`. ``None`` is a supported state and
    means "no model installed": the service still runs, and answers
    ``assessed: false`` so the extension leaves images alone rather than hiding
    them on a number nobody produced.
    """

    backend: object = None
    #: Not configurable to anything but loopback, by the same rule as the
    #: console: a classifier reachable from the LAN answers questions about
    #: content for anyone on it.
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    max_bytes: int = MAX_IMAGE_BYTES

    def __post_init__(self) -> None:
        if self.host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError(
                f"the scoring service binds to loopback only, not {self.host!r}: "
                f"a classifier reachable from the network is an oracle for "
                f"anyone on it")


def extension_origin(origin: str | None) -> bool:
    """True when ``origin`` may read the reply.

    No ``Origin`` at all means the caller is not a browser page - ``curl``, a
    test, the host itself - and is allowed. A browser always sends one on a
    cross-origin request, so the only way to arrive without it is to not be a
    page, which is the case this exception exists for.
    """
    if not origin:
        return True
    lowered = origin.strip().lower()
    return lowered.startswith(EXTENSION_SCHEMES)


def decode_frame(data: bytes) -> tuple[bytes, int, int]:
    """Decode image bytes into a BGR frame the backend can classify.

    Returns ``(buffer, width, height)`` with ``buffer`` holding
    ``width * height * 3`` bytes in BGR order - the order
    :meth:`host.imaging.MarqoOnnxBackend.score_image` expects, because the model
    was trained on OpenCV-style frames.

    Raises :class:`UnusableFrame` for anything that is not a decodable image or
    is too large. Pillow is imported here rather than at module level so that
    importing this module on a machine without it still succeeds and the
    capability probe can report the absence.
    """
    if not data:
        raise UnusableFrame("empty body")

    try:
        from PIL import Image                        # noqa: PLC0415 - lazy
    except ImportError as exc:                       # pragma: no cover - install
        raise UnusableFrame(
            "Pillow is not installed. Install the 'image' extra "
            "(pip install green-dam-2026[image]) to score images.") from exc

    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
    except Exception as exc:                         # noqa: BLE001 - any decode failure
        raise UnusableFrame(f"not a decodable image: {type(exc).__name__}") from exc

    if width <= 0 or height <= 0:
        raise UnusableFrame(f"bad dimensions {width}x{height}")
    if width * height > MAX_PIXELS:
        # Checked on the header, before ``convert`` allocates the frame.
        raise UnusableFrame(
            f"{width}x{height} exceeds the {MAX_PIXELS}-pixel limit")

    try:
        rgb = image.convert("RGB")
    except Exception as exc:                         # noqa: BLE001
        raise UnusableFrame(f"could not convert to RGB: {type(exc).__name__}") from exc

    import numpy as np                               # noqa: PLC0415 - lazy

    pixels = np.asarray(rgb, dtype=np.uint8)
    # RGB -> BGR by reversing the channel axis; contiguous so the backend does
    # not copy it again per inference.
    bgr = np.ascontiguousarray(pixels[:, :, ::-1])
    return bgr.tobytes(), int(bgr.shape[1]), int(bgr.shape[0])


class _Handler(BaseHTTPRequestHandler):
    """One request. ``POST /score`` is the only route that does anything."""

    server_version = "GreenDamScorer/1.0"
    #: Do not advertise the Python version.
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:  # noqa: ANN001
        """Silence the default stderr logger.

        The default writes a line per request, which for this service would
        record how often a child's browser asked about a picture. The service
        keeps no such record, and a log nobody intended is still a log.
        """
        return

    # -- responses --------------------------------------------------------

    def _json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        if origin:
            # Echo only an origin that was allowed; a refused one never reaches
            # this point, so a page cannot learn it was rejected by a header.
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _cors_preflight(self) -> None:
        """Answer a preflight, but only for an extension origin.

        ``image/png`` is not a CORS-safelisted content type, so a browser sends
        ``OPTIONS`` first. Refusing the preflight from a web page is what stops
        an arbitrary site from using the classifier - the POST itself would still
        arrive, but its reply could not be read, and this service's reply is the
        only thing worth stealing.
        """
        origin = self.headers.get("Origin")
        if not extension_origin(origin):
            self.send_response(HTTPStatus.FORBIDDEN)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- routes -----------------------------------------------------------

    def do_OPTIONS(self) -> None:                    # noqa: N802 - http.server API
        self._cors_preflight()

    def do_GET(self) -> None:                        # noqa: N802
        """``/health`` says the service is up and whether a model is loaded.

        Unauthenticated on purpose, like the console's ``/api/health``: it says
        nothing about any picture, and the extension's options page needs to
        tell a parent "the service is not running" versus "the model is missing".
        """
        path = self.path.split("?", 1)[0]
        if path == "/health":
            backend = self.server.config.backend
            self._json({
                "ok": True,
                "assessing": backend is not None,
                "backend": getattr(backend, "name", "") if backend else "",
            })
            return
        self._json({"error": "no_such_route"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:                       # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/score":
            self._json({"error": "no_such_route"}, HTTPStatus.NOT_FOUND)
            return

        if not extension_origin(self.headers.get("Origin")):
            self._json({"error": "forbidden_origin"}, HTTPStatus.FORBIDDEN)
            return

        declared = self.headers.get("Content-Length")
        if declared is None or not declared.isdigit():
            self._json({"error": "length_required"},
                       HTTPStatus.LENGTH_REQUIRED)
            return
        length = int(declared)
        if length > self.server.config.max_bytes:
            self._json({"error": "too_large"},
                       HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return

        data = self.rfile.read(length) if length else b""
        if len(data) != length:
            self._json({"error": "truncated_body"}, HTTPStatus.BAD_REQUEST)
            return

        self._json(self._score(data))

    # -- scoring ----------------------------------------------------------

    def _score(self, data: bytes) -> dict[str, object]:
        """Decode, classify, and answer in the shape ``background.js`` reads.

        ``assessed: false`` is the honest answer when there is no model or the
        frame cannot be judged. The extension turns that into ``null`` and
        leaves the image visible; returning a score of 0.0 here would instead
        report "clean", which nobody measured.
        """
        backend = self.server.config.backend
        if backend is None:
            return {"assessed": False, "score": None, "reason": "no_model"}

        try:
            buffer, width, height = decode_frame(data)
        except UnusableFrame as exc:
            return {"assessed": False, "score": None,
                    "reason": "unusable_frame", "detail": str(exc)[:80]}

        verdict = backend.classify(buffer, width, height)
        return {
            "assessed": bool(verdict.assessed),
            "score": None if verdict.nsfw is None else round(float(verdict.nsfw), 4),
            "flagged": bool(verdict.flagged),
            "reason": verdict.reason,
            "backend": verdict.backend,
        }


class ScoringServer(ThreadingHTTPServer):
    """Threaded loopback listener carrying its configuration."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: ScoringConfig) -> None:
        self.config = config
        super().__init__((config.host, config.port), _Handler)
        config.port = self.server_address[1]

    def handle_error(self, request, client_address) -> None:  # noqa: ANN001
        """Swallow per-connection errors.

        A scoring service that dies because one client sent nonsense would leave
        the extension with no classifier until the next restart - and the symptom
        would be "images stopped being checked", which is invisible.
        """

    @property
    def url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return f"http://{host}:{port}/"


def serve_in_thread(config: ScoringConfig) -> tuple[ScoringServer, threading.Thread]:
    """Start the service on a daemon thread and return it with the thread.

    Daemon so the service never outlives the host process: a listener nobody
    asked to keep is a listener nobody remembers to close.
    """
    server = ScoringServer(config)
    thread = threading.Thread(target=server.serve_forever, name="scoring",
                              daemon=True)
    thread.start()
    return server, thread
