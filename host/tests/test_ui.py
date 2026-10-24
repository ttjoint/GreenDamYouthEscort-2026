"""The parent console: view models, the loopback server, and its four boundaries.

The console is the part of this product a parent actually sees, so it is also the
part where a mistake is most visible and most damaging. Two classes of test here:

*the data layer*  :mod:`host.ui.data` is pure functions, so what it is willing to
                  show can be asserted exactly. The tests that matter are the
                  negative ones: a log row with an unexpected field must not
                  reach the screen, and "nothing is being checked" must not be
                  rendered as "fine".

*the server*      :mod:`host.ui.server` is the only part that opens a socket. Its
                  four boundaries each have a test that fails loudly if the
                  boundary is removed: loopback-only binding, token auth,
                  cross-origin refusal, and no file serving.

The tests deliberately do not assert on HTML structure. The markup is meant to be
editable; the contract is the JSON the page receives.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from green_dam.eventlog import EventLog, LogRecord, Event    # noqa: E402
from green_dam.filter import Filter                          # noqa: E402
from green_dam.wordpacks import Pack, index_of               # noqa: E402
from host.ui import data                                     # noqa: E402
from host.ui.server import (                                 # noqa: E402
    ASSETS,
    ConsoleConfig,
    make_token,
    serve_in_thread,
)


class _FakeBrowser:
    """Stands in for ``host.browsers.Browser`` without touching the registry."""

    def __init__(self, name, family, configurable=True, version=""):
        self.display_name = name
        self.family = family
        self.configurable = configurable
        self.version = version
        self.policy_target = "HKLM\\SOFTWARE\\Policies\\Test" if configurable else ""


class StatusViewTest(unittest.TestCase):
    """``assessing`` is the field that keeps the console honest."""

    def test_bare_filter_is_not_assessing(self):
        """No lists, no model, no rules: the filter has looked at nothing."""
        view = data.status_view(Filter())
        self.assertFalse(view["assessing"])

    def test_bare_filter_names_every_gap(self):
        view = data.status_view(Filter())
        self.assertIn("no_word_lists", view["gaps"])
        self.assertIn("no_image_backend", view["gaps"])
        self.assertIn("no_network_rules", view["gaps"])

    def test_coverage_reports_each_layer(self):
        view = data.status_view(Filter())
        self.assertEqual(set(view["coverage"]), {"text", "image", "network"})
        self.assertFalse(any(view["coverage"].values()))

    def test_network_rules_flip_network_coverage(self):
        filter_ = Filter()
        filter_.lists["A"].add("example.test")
        view = data.status_view(filter_)
        self.assertTrue(view["coverage"]["network"])
        self.assertNotIn("no_network_rules", view["gaps"])

    def test_disabled_filter_is_reported_as_a_gap(self):
        """``Gate.filtering_disabled`` is a read-only property derived from the
        policy array, so the test has to build a filter whose policy says so
        rather than assigning the attribute."""
        from green_dam import sysdat

        # Slot 2 is the "filtering disabled" switch. PolicyArray is immutable,
        # so the array is built with that slot set rather than mutated.
        values = [0] * sysdat.SLOT_COUNT
        values[sysdat.slot_index(2)] = 1
        filter_ = Filter(legacy=sysdat.PolicyArray(values=tuple(values)))
        self.assertTrue(filter_.gate.filtering_disabled,
                        msg="the fixture did not produce a disabled gate")
        view = data.status_view(filter_)
        self.assertTrue(view["filtering_disabled"])
        self.assertIn("filtering_disabled", view["gaps"])

    def test_status_never_contains_list_entries(self):
        filter_ = Filter()
        filter_.lists["A"].add("secret-host.test")
        blob = json.dumps(data.status_view(filter_), ensure_ascii=False)
        self.assertNotIn("secret-host.test", blob,
                         msg="the status view leaked a list entry")

    def test_none_filter_does_not_crash(self):
        view = data.status_view(None)
        self.assertFalse(view["assessing"])
        self.assertIn("no_word_lists", view["gaps"])


class LogViewTest(unittest.TestCase):
    """The log view is the place a content leak would be most tempting."""

    def setUp(self):
        self._dir = Path(__file__).parent / "_tmp_log"
        self._dir.mkdir(exist_ok=True)
        self.path = self._dir / "events.jsonl"

    def tearDown(self):
        for path in self._dir.glob("*"):
            path.unlink()
        self._dir.rmdir()

    def _write(self, count: int = 3) -> None:
        with EventLog(self.path) as log:
            for index in range(count):
                log.append(LogRecord(
                    event=Event.BLOCK, source="network", stage="host",
                    reason="host_listed", when=f"2026-01-0{index + 1}T00:00:00Z"))

    def test_missing_log_is_empty_not_an_error(self):
        view = data.log_view(self.path)
        self.assertEqual(view["rows"], [])
        self.assertEqual(view["total"], 0)

    def test_unknown_path_is_reported(self):
        view = data.log_view(None)
        self.assertFalse(view["path_known"])

    def test_rows_are_returned_newest_first(self):
        self._write(3)
        view = data.log_view(self.path)
        seqs = [row["seq"] for row in view["rows"]]
        self.assertEqual(seqs, sorted(seqs, reverse=True))

    def test_limit_truncates_and_says_so(self):
        self._write(5)
        view = data.log_view(self.path, limit=2)
        self.assertEqual(len(view["rows"]), 2)
        self.assertTrue(view["truncated"])
        self.assertEqual(view["total"], 5)

    def test_rows_carry_no_content_field(self):
        """The log format has no body field; the view must not invent one."""
        self._write(1)
        row = data.log_view(self.path)["rows"][0]
        self.assertNotIn("text", row)
        self.assertNotIn("body", row)
        self.assertNotIn("url", row)

    def test_unexpected_fields_are_dropped(self):
        """Belt and braces: a future LogRecord field must not reach the screen.

        The allowlist in ``_safe_row`` is what makes this true. If someone adds a
        field carrying content, the console will not start showing it because
        nobody updated the view.
        """
        row = data._safe_row({
            "seq": 1, "event": "block", "reason": "host_listed",
            "matched_text": "不该出现的内容", "full_url": "http://x/y",
        })
        self.assertNotIn("matched_text", row)
        self.assertNotIn("full_url", row)
        self.assertEqual(row["reason"], "host_listed")

    def test_digest_is_the_only_content_derived_value(self):
        self._write(1)
        row = data.log_view(self.path)["rows"][0]
        derived = [key for key in row
                   if key not in ("seq", "when", "event", "source", "stage",
                                  "reason", "direction", "span_length")]
        # ``content_digest`` is the only content-derived key, and it is only
        # present when a record actually had a match to digest. These records do
        # not, so the list is empty - which is the stronger statement: nothing
        # content-derived reached the view at all.
        self.assertEqual(derived, [])


class PacksViewTest(unittest.TestCase):
    """Counts, categories and licences - never terms."""

    def _pack(self, **kwargs):
        """Pack has five required fields; tests should only name the interesting
        ones, so the boring three get defaults here."""
        base = {"id": "a", "name": "A", "category": "adult", "licence": "test",
                "source": "test"}
        base.update(kwargs)
        return Pack(**base)

    def _index(self, *packs):
        return index_of(list(packs))

    def test_counts_are_reported(self):
        index = self._index(
            self._pack(id="a", terms=("甲", "乙"), enabled=True),
            self._pack(id="b", name="B", terms=("丙",), enabled=False),
        )
        view = data.packs_view(index)
        self.assertEqual(view["total_terms"], 2)
        self.assertEqual(view["usable"], 1)

    def test_disabled_packs_stay_listed(self):
        """'Why is this not blocked' needs an answer too."""
        index = self._index(self._pack(terms=("甲",), enabled=False))
        view = data.packs_view(index)
        self.assertEqual(len(view["packs"]), 1)
        self.assertFalse(view["packs"][0]["enabled"])

    def test_terms_never_appear(self):
        index = self._index(self._pack(terms=("秘密词条",), enabled=True))
        blob = json.dumps(data.packs_view(index), ensure_ascii=False)
        self.assertNotIn("秘密词条", blob)

    def test_pack_error_is_reported(self):
        index = self._index(self._pack(terms=(), enabled=True,
                                       error="bad json"))
        view = data.packs_view(index)
        self.assertEqual(view["packs"][0]["error"], "bad json")


class BrowsersViewTest(unittest.TestCase):
    def test_uncovered_browsers_are_named(self):
        """A browser the filter cannot reach must be reported, not omitted."""
        view = data.browsers_view([
            _FakeBrowser("Chrome", "chromium"),
            _FakeBrowser("IE", "unknown", configurable=False),
        ])
        self.assertEqual(view["uncovered"], ["IE"])
        self.assertEqual(view["covered"], 1)

    def test_full_coverage_has_no_uncovered_list(self):
        view = data.browsers_view([_FakeBrowser("Chrome", "chromium")])
        self.assertEqual(view["uncovered"], [])

    def test_empty_install_does_not_crash(self):
        view = data.browsers_view([])
        self.assertEqual(view["browsers"], [])
        self.assertEqual(view["covered"], 0)


class ImageViewTest(unittest.TestCase):
    def test_no_backend_is_not_assessing(self):
        view = data.image_view(None)
        self.assertFalse(view["assessing"])
        self.assertIn("不等于", view["explanation"],
                      msg="the explanation must say that no model is not the "
                          "same as no problem")

    def test_backend_is_assessing(self):
        view = data.image_view("marqo-384:model")
        self.assertTrue(view["assessing"])
        self.assertIn("marqo-384", view["backend"])


class TokenTest(unittest.TestCase):
    def test_tokens_are_not_repeated(self):
        self.assertNotEqual(make_token(), make_token())

    def test_token_is_url_safe(self):
        token = make_token()
        self.assertTrue(all(c.isalnum() or c in "-_" for c in token), token)

    def test_token_is_long_enough_to_guess_proof(self):
        self.assertGreaterEqual(len(make_token()), 32)


class LoopbackTest(unittest.TestCase):
    def test_binding_to_a_public_address_is_refused(self):
        """A console reachable from the LAN shows a map of what to avoid."""
        for host in ("0.0.0.0", "192.168.1.10", ""):
            with self.assertRaises(ValueError, msg=host):
                ConsoleConfig(provider=lambda: {}, host=host)

    def test_loopback_addresses_are_accepted(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            ConsoleConfig(provider=lambda: {}, host=host)


class _ServerCase(unittest.TestCase):
    """Base: a real server on a real ephemeral port."""

    provider = staticmethod(lambda: {"ok": True})

    def setUp(self):
        self.config = ConsoleConfig(provider=self.provider)
        self.server, self.thread = serve_in_thread(self.config)
        self.base = self.server.url.rstrip("/")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str, *, headers: dict | None = None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read(), dict(error.headers)


class AuthTest(_ServerCase):
    def test_overview_requires_a_token(self):
        status, body, _ = self.get("/api/overview")
        self.assertEqual(status, 401)
        self.assertIn(b"bad_token", body)

    def test_wrong_token_is_rejected(self):
        status, _, _ = self.get("/api/overview?token=wrong")
        self.assertEqual(status, 401)

    def test_token_in_query_is_accepted(self):
        status, body, _ = self.get(f"/api/overview?token={self.config.token}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})

    def test_token_in_header_is_accepted(self):
        status, _, _ = self.get("/api/overview",
                                headers={"X-Console-Token": self.config.token})
        self.assertEqual(status, 200)

    def test_empty_token_is_rejected(self):
        status, _, _ = self.get("/api/overview?token=")
        self.assertEqual(status, 401)

    def test_health_needs_no_token(self):
        """The launcher polls it; it must not require a secret to say 'up'."""
        status, body, _ = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})

    def test_health_reveals_nothing_about_the_system(self):
        _, body, _ = self.get("/api/health")
        self.assertEqual(set(json.loads(body)), {"ok"})


class OriginTest(_ServerCase):
    def test_cross_origin_request_is_refused_even_with_a_token(self):
        """A page on the internet can reach 127.0.0.1; the token is not a cookie
        and will not be attached for it, but refusing on Origin closes the case
        where the token leaked into a browser history."""
        status, body, _ = self.get(
            f"/api/overview?token={self.config.token}",
            headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn(b"cross_origin", body)

    def test_same_origin_is_accepted(self):
        status, _, _ = self.get(
            f"/api/overview?token={self.config.token}",
            headers={"Origin": self.base})
        self.assertEqual(status, 200)

    def test_foreign_referer_is_refused(self):
        status, _, _ = self.get(
            f"/api/overview?token={self.config.token}",
            headers={"Referer": "https://evil.example/page"})
        self.assertEqual(status, 403)

    def test_absent_origin_is_allowed(self):
        """curl and the tests send no Origin; they still need the token."""
        status, _, _ = self.get(f"/api/overview?token={self.config.token}")
        self.assertEqual(status, 200)


class AssetTest(_ServerCase):
    def test_index_is_served(self):
        status, body, headers = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!DOCTYPE html>", body)
        self.assertIn("text/html", headers["Content-Type"])

    def test_each_whitelisted_asset_is_served(self):
        for name in ASSETS:
            status, body, _ = self.get(f"/static/{name}")
            self.assertEqual(status, 200, msg=name)
            self.assertGreater(len(body), 0, msg=name)

    def test_unknown_asset_is_404(self):
        status, body, _ = self.get("/static/../server.py")
        self.assertIn(status, (404, 400))
        self.assertNotIn(b"ConsoleConfig", body)

    def test_arbitrary_path_is_404(self):
        for path in ("/etc/passwd", "/../host/ui/server.py", "/api/secret"):
            status, body, _ = self.get(path)
            self.assertEqual(status, 404, msg=path)
            self.assertNotIn(b"import", body)

    def test_token_placeholder_is_substituted_in_app_js(self):
        status, body, _ = self.get("/static/app.js")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertNotIn("__CONSOLE_TOKEN__", text)
        self.assertIn(self.config.token, text)

    def test_index_does_not_contain_the_token(self):
        """The token belongs in the JS the browser loads, not in the markup.

        Putting it in the page would need an inline script, and the CSP forbids
        inline script on purpose.
        """
        _, body, _ = self.get("/")
        self.assertNotIn(self.config.token.encode(), body)


class SecurityHeaderTest(_ServerCase):
    def test_csp_forbids_inline_and_remote(self):
        _, _, headers = self.get("/")
        policy = headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)

    def test_framing_is_denied(self):
        _, _, headers = self.get("/")
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_no_sniffing(self):
        _, _, headers = self.get("/")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_no_referrer(self):
        _, _, headers = self.get("/")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_responses_are_not_cached(self):
        _, _, headers = self.get("/")
        self.assertIn("no-store", headers["Cache-Control"])

    def test_server_version_does_not_leak_python(self):
        _, _, headers = self.get("/")
        self.assertNotIn("Python", headers.get("Server", ""))


class ReadOnlyTest(_ServerCase):
    def test_post_is_refused(self):
        """The console is read-only: every action goes through the journalled CLI."""
        request = urllib.request.Request(
            self.base + "/api/overview", data=b"{}", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        self.assertEqual(status, 405)


class ProviderFailureTest(unittest.TestCase):
    def test_provider_exception_does_not_leak_a_path(self):
        def boom():
            raise RuntimeError("C:\\Users\\someone\\secret\\path")

        config = ConsoleConfig(provider=boom)
        server, _ = serve_in_thread(config)
        try:
            url = server.url.rstrip("/") + f"/api/overview?token={config.token}"
            code = None
            body = ""
            try:
                urllib.request.urlopen(url, timeout=5)
                self.fail("expected an error")
            except urllib.error.HTTPError as exc:
                code = exc.code
                body = exc.read().decode("utf-8")
            self.assertEqual(code, 500)
            self.assertNotIn("secret", body)
            self.assertNotIn("C:\\", body)
        finally:
            server.shutdown()
            server.server_close()


class WebAssetsTest(unittest.TestCase):
    """The page must work with no network, which the CSP also enforces."""

    def setUp(self):
        self.dir = Path(__file__).resolve().parents[2] / "host" / "ui" / "web"

    def test_every_declared_asset_exists(self):
        for name in ASSETS:
            self.assertTrue((self.dir / name).is_file(), msg=name)

    def test_html_loads_no_remote_resource(self):
        html = (self.dir / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_html_has_no_inline_script(self):
        """An inline script would need 'unsafe-inline', which the CSP refuses."""
        html = (self.dir / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)

    def _strip_comments(self, text: str) -> str:
        """Remove /* ... */ so a comment explaining a rule does not trip the
        assertion that enforces it."""
        import re

        return re.sub(r"/\*.*?\*/", "", text, flags=re.S)

    def test_css_imports_nothing(self):
        css = self._strip_comments(
            (self.dir / "style.css").read_text(encoding="utf-8"))
        self.assertNotIn("@import", css)
        self.assertNotIn("url(http", css)

    def test_js_does_not_build_html_from_data(self):
        """The console renders data with textContent, never innerHTML.

        This is the difference between a console that displays a log record and
        one that executes it.
        """
        js = self._strip_comments(
            (self.dir / "app.js").read_text(encoding="utf-8"))
        for marker in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                       "document.write", "eval("):
            self.assertNotIn(marker, js, msg=marker)

    def test_js_never_renders_terms_or_content(self):
        js = self._strip_comments(
            (self.dir / "app.js").read_text(encoding="utf-8"))
        self.assertNotIn("pack.terms.join", js)
        self.assertNotIn("row.text", js)


if __name__ == "__main__":
    unittest.main()
