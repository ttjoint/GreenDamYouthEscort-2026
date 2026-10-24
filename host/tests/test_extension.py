"""The browser extension: its decision rules, and the artefact it ships as.

The JavaScript cannot be unit-tested here, so this file tests the two things that
carry the risk:

*the decisions*  :func:`host.extension.decide_text` and :func:`decide_image` are
                 the specification the content script implements. If they are
                 right and the JS matches them, the extension behaves.

*the artefact*   the manifest and ``config.json`` that
                 :func:`host.extension.build_extension` writes. A manifest that
                 both browsers reject is a feature that silently does nothing,
                 and the failure appears only after installation.

Where a rule is a product decision rather than a mechanical one - hiding an
element instead of navigating away, never touching the page's chrome - there is a
test naming that decision, so a future simplification has to argue with the test.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from host import extension as ext                       # noqa: E402


class TextDecisionTest(unittest.TestCase):
    """Mirrors ``matchText`` in content.js."""

    def setUp(self):
        self.rules = ext.default_rules(("赌博", "色情"))

    def test_matching_term_is_returned(self):
        self.assertEqual(ext.decide_text("这里谈论赌博", self.rules), "赌博")

    def test_term_is_returned_not_a_boolean(self):
        """The caller logs which rule fired; a boolean loses that."""
        self.assertIsInstance(ext.decide_text("赌博", self.rules), str)

    def test_clean_text_returns_none(self):
        self.assertIsNone(ext.decide_text("普通新闻内容", self.rules))

    def test_case_insensitive_by_default(self):
        rules = ext.default_rules(("Badword",))
        self.assertEqual(ext.decide_text("a BADWORD here", rules), "Badword")

    def test_case_sensitive_when_asked(self):
        rules = ext.Rules(terms=("Badword",), case_sensitive=True)
        self.assertIsNone(ext.decide_text("a badword here", rules))
        self.assertEqual(ext.decide_text("a Badword here", rules), "Badword")

    def test_single_character_text_is_not_judged(self):
        """One character matches too much to mean anything."""
        self.assertIsNone(ext.decide_text("赌", ext.default_rules(("赌",))))

    def test_empty_text_is_not_judged(self):
        self.assertIsNone(ext.decide_text("", self.rules))
        self.assertIsNone(ext.decide_text(None, self.rules))

    def test_oversized_text_is_not_judged(self):
        """A megabyte of text is a document, not a paragraph, and scanning it on
        every DOM mutation would stall the page."""
        rules = ext.Rules(terms=("赌博",), max_text_length=10)
        self.assertIsNone(ext.decide_text("赌博" + "x" * 100, rules))

    def test_no_terms_means_nothing_is_hidden(self):
        """The default state, and the honest one."""
        self.assertIsNone(ext.decide_text("赌博", ext.default_rules()))

    def test_text_filtering_can_be_switched_off(self):
        rules = ext.Rules(terms=("赌博",), hide_text=False)
        self.assertIsNone(ext.decide_text("赌博", rules))

    def test_no_match_returns_none_not_empty_string(self):
        self.assertIsNone(ext.decide_text("abc", self.rules))


class ImageDecisionTest(unittest.TestCase):
    """Mirrors ``classifyImageUrl`` in content.js."""

    def setUp(self):
        self.rules = ext.default_rules(("x",))

    def test_http_image_is_scored(self):
        self.assertEqual(
            ext.decide_image("https://example.com/a.jpg", self.rules), "score")

    def test_protocol_relative_url_is_scored(self):
        self.assertEqual(ext.decide_image("//cdn.example/a.png", self.rules),
                         "score")

    def test_extensionless_url_is_scored(self):
        """A CDN path has no extension and is still an image.

        Defaulting to "skip" here would miss most modern image URLs; a wasted
        90 ms is cheaper than a missed picture.
        """
        self.assertEqual(
            ext.decide_image("https://cdn.example/image/12345", self.rules),
            "score")

    def test_query_string_does_not_defeat_the_extension_test(self):
        self.assertEqual(
            ext.decide_image("https://e.com/a.jpg?w=200&h=300", self.rules),
            "score")

    def test_data_uri_is_skipped(self):
        """Inline icons are data URIs; a large one would duplicate bytes the page
        already has."""
        self.assertEqual(ext.decide_image("data:image/png;base64,AAAA",
                                          self.rules), "skip")

    def test_browser_internal_urls_are_ignored(self):
        for url in ("chrome://settings", "about:blank",
                    "moz-extension://abc/x"):
            self.assertEqual(ext.decide_image(url, self.rules), "ignore",
                             msg=url)

    def test_empty_url_is_ignored(self):
        self.assertEqual(ext.decide_image("", self.rules), "ignore")

    def test_image_filtering_off_means_skip(self):
        rules = ext.Rules(terms=("x",), hide_images=False)
        self.assertEqual(ext.decide_image("https://e.com/a.jpg", rules), "skip")


class ChromeTest(unittest.TestCase):
    """The page's own chrome must never be hidden.

    Hiding navigation or a comment box makes the site look broken, which produces
    a support call rather than a protected child.
    """

    def test_navigation_is_skipped(self):
        for tag in ("nav", "header", "footer"):
            self.assertIn(tag, ext.SKIP_TAGS, msg=tag)

    def test_form_controls_are_skipped(self):
        for tag in ("form", "button", "input", "select", "textarea"):
            self.assertIn(tag, ext.SKIP_TAGS, msg=tag)

    def test_scripts_and_styles_are_skipped(self):
        for tag in ("script", "style", "noscript"):
            self.assertIn(tag, ext.SKIP_TAGS, msg=tag)

    def test_media_elements_are_skipped(self):
        """Judged by their own path, not by their text."""
        for tag in ("video", "audio", "canvas", "svg", "iframe"):
            self.assertIn(tag, ext.SKIP_TAGS, msg=tag)

    def test_containers_are_judged(self):
        for tag in ("p", "li", "article", "blockquote", "h1"):
            self.assertIn(tag, ext.TEXT_TAGS, msg=tag)

    def test_no_tag_is_in_both_lists(self):
        """A tag in both lists is a contradiction the JS would resolve silently."""
        overlap = set(ext.TEXT_TAGS) & set(ext.SKIP_TAGS)
        self.assertEqual(overlap, set())


class ManifestTest(unittest.TestCase):
    """Both browsers read one manifest; the optional keys must not collide."""

    def setUp(self):
        self.manifest = ext.manifest()

    def test_manifest_version_is_three(self):
        self.assertEqual(self.manifest["manifest_version"], 3)

    def test_both_background_keys_are_emitted(self):
        """Chromium reads service_worker, Firefox reads scripts, and each
        ignores the other's key. One manifest, both browsers."""
        background = self.manifest["background"]
        self.assertIn("service_worker", background)
        self.assertIn("scripts", background)

    def test_firefox_id_is_present(self):
        """Firefox requires an explicit id for a self-hosted policy install."""
        gecko = self.manifest["browser_specific_settings"]["gecko"]
        self.assertIn("id", gecko)
        self.assertIn("@", gecko["id"])

    def test_host_permissions_are_web_only(self):
        """The extension reads visited pages and nothing else."""
        for permission in self.manifest["host_permissions"]:
            self.assertTrue(
                permission.startswith(("http://", "https://")),
                msg=f"unexpected host permission {permission}")

    def test_no_dangerous_permissions(self):
        """A parental control extension must not ask for what malware asks for."""
        dangerous = {"tabs", "history", "downloads", "management",
                     "proxy", "nativeMessaging", "debugger", "webRequest",
                     "cookies", "geolocation", "clipboardRead"}
        requested = set(self.manifest.get("permissions") or [])
        self.assertEqual(requested & dangerous, set())

    def test_content_script_runs_in_all_frames(self):
        """A bad post inside an iframe is still a bad post."""
        script = self.manifest["content_scripts"][0]
        self.assertTrue(script["all_frames"])

    def test_content_script_does_not_run_at_document_start(self):
        """Judging before the DOM exists finds nothing."""
        script = self.manifest["content_scripts"][0]
        self.assertEqual(script["run_at"], "document_idle")

    def test_id_can_be_overridden(self):
        custom = ext.manifest(extension_id="custom@example")
        self.assertEqual(
            custom["browser_specific_settings"]["gecko"]["id"],
            "custom@example")


class BuildTest(unittest.TestCase):
    """The artefact written to disk is what a browser loads."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "ext"

    def tearDown(self):
        self._tmp.cleanup()

    def _build(self, rules=None):
        return ext.build_extension(self.dir, rules or ext.default_rules(("赌博",)))

    def test_all_assets_are_written(self):
        self._build()
        for name in ("manifest.json", "config.json", "content.js",
                     "background.js", "options.html", "options.js"):
            self.assertTrue((self.dir / name).is_file(), msg=name)

    def test_manifest_is_valid_json(self):
        self._build()
        payload = json.loads((self.dir / "manifest.json").read_text("utf-8"))
        self.assertEqual(payload["manifest_version"], 3)

    def test_config_is_valid_json(self):
        self._build()
        payload = json.loads((self.dir / "config.json").read_text("utf-8"))
        self.assertIn("terms", payload)

    def test_config_carries_the_terms(self):
        """The extension must not fetch lists at runtime: the product has no
        network dependency."""
        self._build(ext.default_rules(("赌博", "色情")))
        payload = json.loads((self.dir / "config.json").read_text("utf-8"))
        self.assertEqual(payload["terms"], ["赌博", "色情"])

    def test_config_carries_the_tag_lists(self):
        """The JS reads these from config, so Python owns the decision about what
        is judged and what is chrome."""
        self._build()
        payload = json.loads((self.dir / "config.json").read_text("utf-8"))
        self.assertEqual(payload["textTags"], list(ext.TEXT_TAGS))
        self.assertEqual(payload["skipTags"], list(ext.SKIP_TAGS))

    def test_summary_reports_counts_not_terms(self):
        summary = self._build()
        blob = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("赌博", blob)
        self.assertEqual(summary["terms"], 1)

    def test_rebuild_overwrites_config(self):
        """A regenerated config must replace the old one, not merge with it."""
        self._build(ext.default_rules(("旧词",)))
        self._build(ext.default_rules(("新词",)))
        payload = json.loads((self.dir / "config.json").read_text("utf-8"))
        self.assertEqual(payload["terms"], ["新词"])


class JavaScriptSyntaxTest(unittest.TestCase):
    """Parse the JS with Node when it is available.

    A syntax error in a content script produces a browser console message nobody
    reads and a filter that does nothing. Catching it here costs one subprocess.
    """

    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is not installed")

    def _check(self, name: str):
        source = Path(ext.__file__).resolve().parent / name
        result = subprocess.run(["node", "--check", str(source)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0,
                         msg=f"{name} does not parse:\n{result.stderr}")

    def test_content_js_parses(self):
        self._check("content.js")

    def test_background_js_parses(self):
        self._check("background.js")

    def test_options_js_parses(self):
        self._check("options.js")


class NoRemoteCodeTest(unittest.TestCase):
    """Store policy and the product's own promise: no code from the network."""

    def test_js_files_do_not_load_remote_scripts(self):
        directory = Path(ext.__file__).resolve().parent
        offenders: list[str] = []
        for path in directory.glob("*.js"):
            text = path.read_text(encoding="utf-8")
            for marker in ("importScripts(", "document.write(",
                           "eval(", "new Function("):
                if marker in text:
                    offenders.append(f"{path.name}: {marker}")
        self.assertEqual(offenders, [],
                         msg=f"remote or dynamic code execution: {offenders}")

    def test_html_has_no_remote_assets(self):
        """An options page that loads a CDN script is a page that stops working
        offline and phones out on open."""
        html = (Path(ext.__file__).resolve().parent / "options.html").read_text(
            encoding="utf-8")
        self.assertNotIn("http://", html.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", html)

    def test_options_page_actually_probes_the_scoring_service(self):
        """The page must *check*, not just describe, whether scoring works.

        ``options.html`` has always carried a sentence saying images are not
        hidden when the service is down - and nothing ever verified that
        sentence against reality. A parent could read "图片不会被判定" as a
        caveat rather than as the current state, while the service had never
        been started.

        Asserted as three separate facts because a page that renders a status
        element but never fills it is indistinguishable, to the user, from a
        working page that says nothing.
        """
        directory = Path(ext.__file__).resolve().parent
        html = (directory / "options.html").read_text(encoding="utf-8")
        script = (directory / "options.js").read_text(encoding="utf-8")

        self.assertIn('id="service-status"', html,
                      msg="the options page has no element for the status")
        self.assertIn("/health", script,
                      msg="options.js never asks the service whether it is up")
        self.assertIn("probeService(", script,
                      msg="options.js defines no probe, or never calls it")
        self.assertIn("service-status", script,
                      msg="the probe writes to no element")

    def test_the_options_probe_distinguishes_down_from_model_less(self):
        """"not running" and "running without a model" need different fixes.

        Telling a parent to check their model when the service is simply not
        started sends them to the wrong problem, which is the class of mistake
        this project keeps finding in the original product's documentation.
        """
        script = (Path(ext.__file__).resolve().parent
                  / "options.js").read_text(encoding="utf-8")
        self.assertIn("未运行", script)
        self.assertIn("未加载模型", script)
        self.assertIn("host.imaging", script,
                      msg="the message should name the command that fixes it")


if __name__ == "__main__":
    unittest.main()


class JavaScriptBehaviourTest(unittest.TestCase):
    """Run ``content.js``'s real decision functions against a DOM stub.

    The Python tests above verify the *specification* and that the JS parses.
    Neither proves the JS implements the same rules. This class extracts the
    actual functions from ``content.js`` - not a copy - and runs them.

    Why not a real browser: as of Chrome 152 and Edge 156, ``--load-extension``
    is ignored, so a headless instance cannot be made to load an unpacked
    extension. Verified on this machine: the extension list stays empty and
    ``chrome.runtime`` is undefined in the page. Driving a real browser would
    therefore test nothing, so the stub is the honest option rather than a
    shortcut.
    """

    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is not installed")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name) / "ext"
        ext.build_extension(cls.dir, ext.default_rules(("赌博", "badword")))
        cls.harness = Path(__file__).resolve().parent / "js" / "run.js"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_case_passes(self):
        result = subprocess.run(
            ["node", str(self.harness),
             str(self.dir / "content.js"), str(self.dir / "config.json")],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        self.assertEqual(
            result.returncode, 0,
            msg=f"content.js behaved differently from the specification:\n"
                f"{result.stdout}\n{result.stderr}")
        self.assertIn("cases passed", result.stdout)

    def test_harness_reports_the_expected_number_of_cases(self):
        """A harness that silently stops finding cases would pass vacuously."""
        result = subprocess.run(
            ["node", str(self.harness),
             str(self.dir / "content.js"), str(self.dir / "config.json")],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        marker = "cases passed"
        line = [ln for ln in result.stdout.splitlines() if marker in ln][-1]
        passed, total = line.split()[0].split("/")
        self.assertEqual(passed, total)
        self.assertGreaterEqual(int(total), 10,
                                msg="the harness is not exercising enough cases")
