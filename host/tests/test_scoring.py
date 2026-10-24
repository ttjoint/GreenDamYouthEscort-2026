"""The scoring endpoint the browser extension actually calls.

Why this file exists: ``host/extension/background.js`` POSTs image bytes to
``127.0.0.1:17871/score``. Every other part of the image path was tested - the
rules (``test_extension.py``), the model (``test_imaging.py``), the engine's
verdict mapping (``test_imagepath.py``) - and the HTTP endpoint between them was
not, so it did not exist. The extension's image filtering was therefore a no-op
that read as finished.

The tests fall into three groups:

*the wire format*  what the extension sends and what it can read back. The
                  shape matters: ``background.js`` checks ``payload.assessed``
                  and ``typeof payload.score === "number"``, so a reply that
                  omits either leaves the image visible.

*the boundaries*   loopback only, extension origins only, bounded body. Each is
                  a way the service could be turned into something else - a LAN
                  oracle, a free NSFW classifier for any web page, a memory
                  exhaustion target.

*the honest answer*  no model and unjudgeable frames return ``assessed: false``,
                  never a zero. This is the project's central rule and the
                  extension's own comment says it relies on it.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from host.imaging import UnusableFrame                     # noqa: E402
from host.imaging import service                           # noqa: E402
from host.imaging.service import (                         # noqa: E402
    DEFAULT_PORT,
    MAX_IMAGE_BYTES,
    ScoringConfig,
    decode_frame,
    extension_origin,
    serve_in_thread,
)


def _png(width: int = 96, height: int = 96, *, noise: bool = True) -> bytes:
    """A real PNG, encoded by Pillow.

    A generated image rather than a fixture file: the point is that the service
    decodes what a browser would send, and a checked-in binary would prove only
    that it decodes that one file.
    """
    from PIL import Image

    if noise:
        import numpy as np

        rng = np.random.default_rng(7)
        pixels = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
        image = Image.fromarray(pixels, mode="RGB")
    else:
        image = Image.new("RGB", (width, height), (128, 128, 128))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeBackend:
    """An ``ImageBackend`` that returns a fixed verdict, without onnxruntime."""

    name = "fake-model"

    def __init__(self, score: float | None = 0.1, *, assessed: bool = True):
        self.score = score
        self.assessed = assessed
        self.calls = 0

    def classify(self, buffer: bytes, width: int, height: int):
        from host.engine import verdict_from_score

        self.calls += 1
        if not self.assessed:
            return verdict_from_score(None, backend=self.name)
        return verdict_from_score(self.score, backend=self.name)


class _ServerCase(unittest.TestCase):
    """A real server on a real ephemeral loopback port."""

    backend_factory = staticmethod(lambda: _FakeBackend())

    def setUp(self):
        self.backend = self.backend_factory()
        self.config = ScoringConfig(backend=self.backend, port=0)
        self.server, self.thread = serve_in_thread(self.config)
        self.base = self.server.url.rstrip("/")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, path: str, body: bytes, *, headers: dict | None = None):
        request = urllib.request.Request(
            self.base + path, data=body, method="POST", headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)

    def get(self, path: str):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")


class LoopbackTest(unittest.TestCase):
    """A classifier on the LAN answers questions about content for anyone."""

    def test_binding_to_a_public_address_is_refused(self):
        for host in ("0.0.0.0", "192.168.1.10", "", "::"):
            with self.assertRaises(ValueError, msg=host):
                ScoringConfig(backend=None, host=host)

    def test_loopback_addresses_are_accepted(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            ScoringConfig(backend=None, host=host)

    def test_the_default_port_matches_the_extension(self):
        """The extension hard-codes 17871 in three places.

        Asserted here rather than left to a comment: changing the default
        without changing ``background.js`` makes scoring a silent no-op, which
        is exactly the failure this module was written to end.
        """
        self.assertEqual(DEFAULT_PORT, 17871)
        assets = ROOT / "host" / "extension"
        for name in ("background.js", "options.js"):
            text = (assets / name).read_text(encoding="utf-8")
            self.assertIn(str(DEFAULT_PORT), text,
                          msg=f"{name} does not point at port {DEFAULT_PORT}; "
                              f"the extension and the service disagree")
        rules = (ROOT / "host" / "extension" / "__init__.py").read_text(
            encoding="utf-8")
        self.assertIn(f"127.0.0.1:{DEFAULT_PORT}", rules,
                      msg="Rules.service_url and DEFAULT_PORT disagree")


class OriginTest(unittest.TestCase):
    """Any web page can POST to loopback; only an extension may read the reply."""

    def test_extension_origins_are_allowed(self):
        for origin in ("chrome-extension://abcdef",
                       "moz-extension://0f1e2d",
                       "CHROME-EXTENSION://ABCDEF"):
            self.assertTrue(extension_origin(origin), msg=origin)

    def test_web_origins_are_refused(self):
        for origin in ("https://evil.example", "http://127.0.0.1:8000",
                       "null", "file://"):
            self.assertFalse(extension_origin(origin), msg=origin)

    def test_no_origin_is_allowed(self):
        """A local tool sends no Origin. It is not a page, so it cannot be
        another site's page, which is the case the check exists for."""
        self.assertTrue(extension_origin(None))
        self.assertTrue(extension_origin(""))


class ScoreEndpointTest(_ServerCase):
    """The shape ``background.js`` reads, and the honesty rules behind it."""

    def test_a_real_png_is_scored(self):
        status, body, _ = self.post(
            "/score", _png(),
            headers={"Content-Type": "image/png",
                     "Origin": "chrome-extension://abcdef"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["assessed"])
        self.assertIsInstance(payload["score"], float)
        self.assertEqual(payload["backend"], "fake-model")
        self.assertEqual(self.backend.calls, 1)

    def test_high_score_is_flagged(self):
        self.backend.score = 0.93
        status, body, _ = self.post(
            "/score", _png(), headers={"Content-Type": "image/png"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["flagged"])
        self.assertGreater(payload["score"], 0.5)

    def test_no_model_answers_not_assessed_not_zero(self):
        """The central rule: "not looked at" must never read as "clean".

        ``background.js`` treats a null score as "leave the image visible", and
        a 0.0 would instead assert the picture is fine - a claim nobody made.
        """
        self.backend.assessed = False
        status, body, _ = self.post(
            "/score", _png(), headers={"Content-Type": "image/png"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["assessed"])
        self.assertIsNone(payload["score"])

    def test_missing_model_is_reported_without_scoring(self):
        self.config.backend = None
        status, body, _ = self.post(
            "/score", _png(), headers={"Content-Type": "image/png"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["assessed"])
        self.assertIsNone(payload["score"])
        self.assertEqual(payload["reason"], "no_model")

    def test_undecodable_body_is_not_assessed(self):
        status, body, _ = self.post(
            "/score", b"this is not an image at all",
            headers={"Content-Type": "image/png"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["assessed"])
        self.assertIsNone(payload["score"])
        self.assertEqual(payload["reason"], "unusable_frame")
        self.assertEqual(self.backend.calls, 0,
                         msg="the backend ran on bytes that were not an image")

    def test_empty_body_is_not_assessed(self):
        status, body, _ = self.post("/score", b"",
                                    headers={"Content-Type": "image/png"})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["assessed"])

    def test_a_web_page_cannot_read_the_reply(self):
        status, body, _ = self.post(
            "/score", _png(),
            headers={"Content-Type": "image/png",
                     "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden_origin")
        self.assertEqual(self.backend.calls, 0,
                         msg="a refused origin still caused a classification")

    def test_unknown_route_is_404(self):
        status, body, _ = self.post("/classify", _png(),
                                    headers={"Content-Type": "image/png"})
        self.assertEqual(status, 404)

    def test_reply_is_not_cached(self):
        _, _, headers = self.post(
            "/score", _png(), headers={"Content-Type": "image/png"})
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_reply_never_contains_the_image_or_a_hash_of_it(self):
        """A score is a fact about a picture; the picture is not the product's.

        The reply is a fixed set of keys, so a future field carrying bytes would
        have to be added deliberately - which is the point of asserting the key
        set rather than the absence of a specific name.
        """
        _, body, _ = self.post("/score", _png(),
                               headers={"Content-Type": "image/png"})
        payload = json.loads(body)
        self.assertLessEqual(
            set(payload), {"assessed", "score", "flagged", "reason", "backend",
                           "detail"},
            msg=f"the reply gained a field: {sorted(payload)}")


class PreflightTest(_ServerCase):
    """``image/png`` is not CORS-safelisted, so a browser sends OPTIONS first."""

    def _options(self, origin: str | None):
        headers = {"Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "content-type"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + "/score", method="OPTIONS",
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers)

    def test_extension_preflight_is_allowed(self):
        status, headers = self._options("chrome-extension://abcdef")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"),
                         "chrome-extension://abcdef")
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))

    def test_web_page_preflight_is_refused(self):
        status, headers = self._options("https://evil.example")
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))


class SizeLimitTest(unittest.TestCase):
    """A decompression bomb is cheap to send and expensive to decode."""

    def test_declared_body_over_the_limit_is_refused(self):
        """The refusal must happen on the header, before the bytes arrive.

        Written against a raw socket on purpose: ``urllib`` insists on sending
        a body, and the server closes the connection the moment it refuses, so
        the client can see a reset instead of the 413 it is testing for. Sending
        *only* a head - and never the body - is both closer to an attack and
        the only way to prove the check did not wait for the payload.
        """
        import socket as _socket

        config = ScoringConfig(backend=None, port=0)
        server, _thread = serve_in_thread(config)
        host, port = server.server_address[0], server.server_address[1]
        try:
            with _socket.create_connection((host, port), timeout=10) as sock:
                sock.sendall(
                    b"POST /score HTTP/1.1\r\n"
                    b"Host: 127.0.0.1\r\n"
                    b"Content-Type: image/png\r\n"
                    b"Content-Length: " + str(MAX_IMAGE_BYTES + 1).encode() +
                    b"\r\n\r\n")
                reply = b""
                while b"\r\n\r\n" not in reply:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    reply += chunk
            self.assertIn(b"413", reply.split(b"\r\n", 1)[0],
                          msg=f"expected a 413 for an oversized body, got: "
                              f"{reply[:120]!r}")
        finally:
            server.shutdown()
            server.server_close()

    def test_huge_pixel_count_is_refused_before_conversion(self):
        """Checked from the header, before ``convert`` allocates the frame."""
        from PIL import Image

        buffer = io.BytesIO()
        # 5000x5000 = 25 M pixels, over MAX_PIXELS. Saved as a real PNG so the
        # header is what carries the dimensions.
        Image.new("RGB", (5000, 5000), (0, 0, 0)).save(buffer, format="PNG")
        with self.assertRaises(UnusableFrame) as ctx:
            decode_frame(buffer.getvalue())
        self.assertIn("pixel limit", str(ctx.exception))

    def test_missing_content_length_is_refused(self):
        config = ScoringConfig(backend=None, port=0)
        server, _thread = serve_in_thread(config)
        try:
            import http.client

            host, port = server.server_address[0], server.server_address[1]
            connection = http.client.HTTPConnection(host, port, timeout=10)
            # Send a request with no Content-Length at all.
            connection.putrequest("POST", "/score")
            connection.putheader("Content-Type", "image/png")
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 411)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()


class DecodeTest(unittest.TestCase):
    """``decode_frame`` produces the BGR layout the model was trained on."""

    def test_png_decodes_to_bgr(self):
        buffer, width, height = decode_frame(_png(64, 48))
        self.assertEqual((width, height), (64, 48))
        self.assertEqual(len(buffer), 64 * 48 * 3)

    def test_channel_order_is_bgr(self):
        """A red PNG must arrive with the red value in the last byte.

        The model expects OpenCV's BGR order; getting it backwards would leave
        every frame scored against the wrong channels, and the result would be
        plausible enough to go unnoticed.
        """
        from PIL import Image

        image = Image.new("RGB", (8, 8), (255, 0, 0))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        buffer, _w, _h = decode_frame(stream.getvalue())
        blue, green, red = buffer[0], buffer[1], buffer[2]
        self.assertEqual((red, green, blue), (255, 0, 0))

    def test_non_image_is_refused(self):
        with self.assertRaises(UnusableFrame):
            decode_frame(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20)

    def test_empty_is_refused(self):
        with self.assertRaises(UnusableFrame):
            decode_frame(b"")

    def test_transparent_png_is_flattened_to_rgb(self):
        from PIL import Image

        image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        buffer, width, height = decode_frame(stream.getvalue())
        self.assertEqual((width, height), (16, 16))
        self.assertEqual(len(buffer), 16 * 16 * 3)


class HealthTest(_ServerCase):
    """The options page needs "service down" told apart from "no model"."""

    def test_health_reports_a_loaded_model(self):
        status, payload = self.get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["assessing"])
        self.assertEqual(payload["backend"], "fake-model")

    def test_health_reports_no_model_honestly(self):
        self.config.backend = None
        status, payload = self.get("/health")
        self.assertEqual(status, 200)
        self.assertFalse(payload["assessing"])
        self.assertEqual(payload["backend"], "")


class BackendWiringTest(unittest.TestCase):
    """``Filter.image_backend`` must be reachable from the host's one entry point.

    The field existed from the start and **nothing in the product could ever set
    it**: ``host.engine.open_filter_from`` had no parameter for it, so a filter
    built by the host always answered ``no_model``. The engine's own tests passed
    because they construct ``Filter`` directly with a backend; the host's tests
    passed because they never asked for a verdict.

    This is the same shape of hole as the missing scoring endpoint - each side
    correct, the join untested - so it gets an explicit test on the join.
    """

    def test_no_model_path_leaves_the_backend_unset(self):
        import tempfile

        from host.engine import open_filter_from

        with tempfile.TemporaryDirectory() as td:
            filter_, _wiring = open_filter_from(Path(td), image_model=None)
        self.assertIsNone(filter_.image_backend,
                          msg="no model was named, so nothing may be assumed")

    def test_a_missing_model_path_is_not_an_error(self):
        """A deployment without the optional M4 package is supported.

        Raising here would make one optional extra able to stop the whole
        product from starting.
        """
        import tempfile

        from host.engine import open_filter_from

        with tempfile.TemporaryDirectory() as td:
            filter_, _wiring = open_filter_from(
                Path(td), image_model=Path(td) / "absent" / "model.onnx")
        self.assertIsNone(filter_.image_backend)

    def test_a_corrupt_model_is_not_an_error(self):
        """A file that exists but cannot be loaded must degrade, not raise."""
        import tempfile

        from host.engine import open_filter_from

        with tempfile.TemporaryDirectory() as td:
            model = Path(td) / "model.onnx"
            model.write_bytes(b"not an onnx graph")
            filter_, _wiring = open_filter_from(Path(td), image_model=model)
        self.assertIsNone(filter_.image_backend,
                          msg="a corrupt model should leave the backend unset, "
                              "not raise out of open_filter_from")

    def test_the_engine_accepts_an_injected_backend(self):
        """The engine's half of the join, asserted where the host relies on it."""
        from green_dam import config as config_mod, sysdat
        from green_dam.filter import Filter

        values = list(config_mod.default_for_fresh_install().values)
        values[sysdat.slot_index(13)] = 1        # turn the image path on
        legacy = sysdat.PolicyArray(values=tuple(values))

        backend = _FakeBackend(0.9)
        filter_ = Filter(legacy=legacy, image_backend=backend)
        buffer, width, height = decode_frame(_png())
        verdict = filter_.check_image_verdict(buffer, width=width,
                                              height=height)
        self.assertTrue(verdict.assessed)
        self.assertTrue(verdict.flagged)
        self.assertEqual(backend.calls, 1)

    def test_an_enabled_image_path_without_a_backend_says_no_model(self):
        """Not ``assessed: False`` with a zero score - ``no_model`` by name.

        The distinction is what the console and the extension both branch on,
        and it is the difference between "we looked and it was fine" and "we
        have nothing to look with".
        """
        from green_dam import config as config_mod, sysdat
        from green_dam.filter import Filter

        values = list(config_mod.default_for_fresh_install().values)
        values[sysdat.slot_index(13)] = 1
        legacy = sysdat.PolicyArray(values=tuple(values))

        filter_ = Filter(legacy=legacy, image_backend=None)
        buffer, width, height = decode_frame(_png())
        verdict = filter_.check_image_verdict(buffer, width=width,
                                             height=height)
        self.assertFalse(verdict.assessed)
        self.assertIsNone(verdict.nsfw)
        self.assertEqual(verdict.reason, "no_model")

    def test_a_usable_model_reaches_the_filter(self):
        """The join that was missing: a found backend must land on the Filter.

        Patched at ``open_image_backend`` so the test does not need a real 21 MB
        ONNX graph. Without this, deleting the wiring line from
        ``open_filter_from`` would leave every test above passing - they all
        check the *no model* path, which is the same whether the line exists or
        not. That is exactly how the hole survived the first time.
        """
        import tempfile
        from unittest import mock

        from host import engine

        backend = _FakeBackend(0.9)
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(engine, "open_image_backend",
                                   return_value=backend) as patched:
                filter_, _wiring = engine.open_filter_from(
                    Path(td), image_model=Path(td) / "whatever.onnx")
        patched.assert_called_once()
        self.assertIs(filter_.image_backend, backend,
                      msg="open_filter_from built a filter without the backend "
                          "it was given")

    def test_the_model_argument_is_what_gets_opened(self):
        """The path named by the caller is the path that is tried."""
        import tempfile
        from unittest import mock

        from host import engine

        with tempfile.TemporaryDirectory() as td:
            model = Path(td) / "m" / "model.onnx"
            with mock.patch.object(engine, "open_image_backend",
                                   return_value=None) as patched:
                engine.open_filter_from(Path(td), image_model=model)
        patched.assert_called_once_with(model)

    def test_the_default_policy_has_images_off(self):
        """A fresh install does not claim to be judging pictures.

        Slot 13 is 0 in the shipped bytes, and ``Gate`` refuses to read "not
        configured" as "enabled". Asserted here because a host that wired a
        model in would otherwise look like it was filtering when the engine
        still gates the path shut.
        """
        from green_dam import config as config_mod
        from green_dam.filter import Filter

        legacy = config_mod.default_for_fresh_install()
        self.assertFalse(Filter(legacy=legacy).image_filter_enabled)


if __name__ == "__main__":
    unittest.main()
