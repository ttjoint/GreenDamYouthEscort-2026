"""H7 / A7: the deployment manifest and the packer.

Two layers, tested separately:

``host.packaging``  the *plan*: which files, where, and why. Pure data, so every
                    question about a deployment is answerable without copying
                    anything or owning an embeddable CPython.
``tools.package``   the *copy*: interpreter layout, pywin32's unusual layout,
                    and the refusals that keep a bad invocation from destroying
                    a good artefact.

The size assertions are deliberately loose bounds, not exact figures. An exact
byte count would fail on every CPython patch release; the property worth pinning
is the order of magnitude, because the bug this guards against made the output
5.9 GB instead of 20 MB.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from host import packaging                                # noqa: E402
from tools import package as packer                       # noqa: E402


def _repo(tmp: Path) -> Path:
    """A miniature checkout with the shape ``build_manifest`` expects."""
    (tmp / "src" / "green_dam").mkdir(parents=True)
    (tmp / "src" / "green_dam" / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "src" / "green_dam" / "filter.py").write_text("x = 1\n",
                                                         encoding="utf-8")
    (tmp / "src" / "green_dam" / "__pycache__").mkdir()
    (tmp / "src" / "green_dam" / "__pycache__" / "junk.pyc").write_bytes(b"x")

    (tmp / "host").mkdir()
    (tmp / "host" / "__init__.py").write_text("", encoding="utf-8")
    (tmp / "host" / "engine.py").write_text("y = 2\n", encoding="utf-8")
    (tmp / "host" / "tests").mkdir()
    (tmp / "host" / "tests" / "test_x.py").write_text("", encoding="utf-8")

    (tmp / "data" / "legacy-imported").mkdir(parents=True)
    (tmp / "data" / "legacy-imported" / "falun-terms.json").write_text(
        "{}", encoding="utf-8")
    (tmp / "data" / "verb-table.json").write_text("{}", encoding="utf-8")
    # A data file the product never reads; must not be shipped.
    (tmp / "data" / "external-wordlists.json").write_text("{}",
                                                          encoding="utf-8")

    (tmp / "docs").mkdir()
    (tmp / "docs" / "dependency-audit.md").write_text("# audit\n",
                                                       encoding="utf-8")
    return tmp


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_engine_and_host_are_included(self):
        manifest = packaging.build_manifest(self.root)
        destinations = {e.destination for e in manifest.entries}
        self.assertIn("engine/green_dam/filter.py", destinations)
        self.assertIn("host/engine.py", destinations)

    def test_tests_are_never_shipped(self):
        """The deployment cannot run them and they outnumber the code."""
        manifest = packaging.build_manifest(self.root)
        for entry in manifest.entries:
            self.assertNotIn("/tests/", f"/{entry.destination}")
            self.assertNotIn("test_", entry.destination)

    def test_pycache_is_never_shipped(self):
        manifest = packaging.build_manifest(self.root)
        for entry in manifest.entries:
            self.assertNotIn("__pycache__", entry.destination)
            self.assertNotIn(".pyc", entry.destination)

    def test_unused_data_is_not_shipped(self):
        """Only the data files the product reads, not all of data/."""
        manifest = packaging.build_manifest(self.root)
        destinations = {e.destination for e in manifest.entries}
        self.assertIn("data/verb-table.json", destinations)
        self.assertNotIn("data/external-wordlists.json", destinations)

    def test_every_entry_has_a_reason(self):
        """An unexplained file is one nobody can later decide to remove."""
        for entry in packaging.build_manifest(self.root).entries:
            self.assertTrue(entry.reason.strip(), msg=entry.destination)

    def test_exclusions_are_recorded_not_silent(self):
        manifest = packaging.build_manifest(self.root)
        excluded = {path for path, _why in manifest.excluded}
        self.assertTrue(any("__pycache__" in path for path in excluded))
        self.assertTrue(any("/tests/" in f"/{path}" for path in excluded))

    def test_missing_engine_is_reported(self):
        """Absence of the package *directory*, which is what the host needs."""
        import shutil
        shutil.rmtree(self.root / "src" / "green_dam")
        manifest = packaging.build_manifest(self.root)
        self.assertTrue(manifest.missing)
        self.assertIn("src/green_dam", [path for path, _why in manifest.missing])

    def test_summary_counts_by_top_level(self):
        summary = packaging.build_manifest(self.root).summary()
        self.assertIn("engine", summary["by_top"])
        self.assertIn("host", summary["by_top"])
        self.assertGreater(summary["files"], 0)


class DestinationSafetyTest(unittest.TestCase):
    """A manifest must never be able to write outside the deployment root."""

    def test_parent_traversal_is_rejected(self):
        with self.assertRaises(packaging.UnsafeDestination):
            packaging._safe_destination("engine/../../etc/passwd")

    def test_absolute_path_is_rejected(self):
        with self.assertRaises(packaging.UnsafeDestination):
            packaging._safe_destination("C:/Windows/system32/x.dll")

    def test_posix_absolute_path_is_rejected(self):
        with self.assertRaises(packaging.UnsafeDestination):
            packaging._safe_destination("/etc/passwd")

    def test_normal_relative_path_is_accepted(self):
        self.assertEqual(packaging._safe_destination("engine/green_dam/x.py"),
                         "engine/green_dam/x.py")

    def test_backslashes_are_normalised(self):
        self.assertEqual(packaging._safe_destination("engine\\green_dam\\x.py"),
                         "engine/green_dam/x.py")


class VerifyTest(unittest.TestCase):
    def test_missing_file_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = _repo(Path(td))
            manifest = packaging.build_manifest(root)
            out = Path(td) / "out"
            out.mkdir()
            problems = packaging.verify_manifest(out, manifest)
            self.assertTrue(problems)
            self.assertTrue(all(p.startswith("missing:") for p in problems))

    def test_size_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            root = _repo(Path(td))
            manifest = packaging.build_manifest(root)
            out = Path(td) / "out"
            for entry in manifest.entries:
                target = out / entry.destination
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"truncated")
            problems = packaging.verify_manifest(out, manifest)
            self.assertTrue(any("size mismatch" in p for p in problems))


class PackageInvocationTest(unittest.TestCase):
    """The refusals, which are what keep a bad command from destroying output."""

    def test_refuses_non_empty_output_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir()
            (out / "keep.txt").write_text("do not lose me", encoding="utf-8")
            code = packer.main(["--out", str(out)])
            self.assertEqual(code, 2)
            self.assertTrue((out / "keep.txt").is_file(),
                            msg="the packer destroyed existing output")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            code = packer.main(["--out", str(out), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertFalse(out.exists())

    def test_missing_interpreter_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            code = packer.main(["--out", str(out), "--python",
                                str(Path(td) / "no-such-python")])
            self.assertEqual(code, 2)

    def test_directory_is_accepted_as_interpreter(self):
        """Callers pass the installation directory; that must not be an error.

        Handing a directory to ``CreateProcess`` raises ``PermissionError``,
        which an earlier version reported as "the interpreter may be broken" -
        blaming the user for doing the natural thing.
        """
        self.assertIsNotNone(packer._python_exe(Path(sys.executable).parent))
        self.assertIsNotNone(packer._python_exe(Path(sys.executable)))

    def test_bogus_directory_is_not_an_interpreter(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(packer._python_exe(Path(td)))

    def test_site_packages_returns_a_list(self):
        """More than one directory matters: user-site packages are real.

        On the machine this was written on, ``psutil`` lived in the per-user site
        directory while ``cryptography`` lived in the installation's. A
        single-directory lookup found one and reported the other as missing.
        """
        sites = packer._site_packages(Path(sys.executable).parent)
        self.assertIsInstance(sites, list)
        self.assertTrue(sites, msg="no site-packages found for this interpreter")


class Pywin32LayoutTest(unittest.TestCase):
    """pywin32 is not a normal package and needs its ``.pth`` reproduced."""

    def test_layout_constants_are_documented(self):
        self.assertIn("win32", packer.PYWIN32_DIRS)
        self.assertEqual(packer.PYWIN32_PTH, "pywin32.pth")

    def test_pywin32_is_not_in_the_plain_package_list(self):
        """Its importable names are submodules, so name-based copying fails."""
        for name in ("win32api", "win32serviceutil", "pythoncom"):
            self.assertNotIn(name, packer.RUNTIME_PACKAGES)

    def test_missing_pywin32_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            result = packer._copy_pywin32([Path(td)], Path(td) / "dest")
            self.assertIn("pywin32", result["missing"])


class RealCheckoutTest(unittest.TestCase):
    """The manifest for *this* repository must be complete."""

    def test_this_repository_produces_a_complete_manifest(self):
        manifest = packaging.build_manifest(ROOT)
        self.assertEqual(tuple(manifest.missing), (),
                         msg=f"the real checkout is missing files: "
                             f"{manifest.missing}")
        destinations = {e.destination for e in manifest.entries}
        # The two things the host looks for at startup.
        self.assertIn("engine/green_dam/__init__.py", destinations)
        self.assertIn("host/engine.py", destinations)

    def test_every_engine_module_is_shipped(self):
        """A module left out is a deployment that fails at runtime, not at build."""
        manifest = packaging.build_manifest(ROOT)
        shipped = {e.destination for e in manifest.entries}
        for path in sorted((ROOT / "src" / "green_dam").glob("*.py")):
            self.assertIn(f"engine/green_dam/{path.name}", shipped, msg=path.name)

    def test_every_host_module_is_shipped(self):
        """The host half, which the engine test above cannot see.

        ``test_every_engine_module_is_shipped`` only walks ``src/green_dam``.
        A host module missing from the manifest - ``imaging/service.py``, say -
        produces a deployment where the extension's image path silently does
        nothing, and nothing at build time says so.
        """
        manifest = packaging.build_manifest(ROOT)
        shipped = {e.destination for e in manifest.entries}
        for path in sorted((ROOT / "host").rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "/tests/" in f"/{relative}" or relative.endswith("/conftest.py"):
                continue
            self.assertIn(relative, shipped, msg=relative)

    def test_the_scoring_service_is_shipped(self):
        """Named explicitly because its absence is invisible.

        Without this module the extension's image filtering is a no-op that
        reports no error: ``background.js`` gets no reply, treats the image as
        unscored, and leaves it visible. Nothing else in the product would fail.
        """
        manifest = packaging.build_manifest(ROOT)
        shipped = {e.destination for e in manifest.entries}
        for name in ("host/imaging/service.py", "host/imaging/__main__.py"):
            self.assertIn(name, shipped)

    def test_manifest_size_is_the_order_of_a_small_download(self):
        """Engine + host + data + model, without an interpreter.

        The bug that prompted this test produced 5.9 GB by copying the whole
        site-packages of the build machine, so the point is catching a runaway
        copy, not enforcing a precise number.

        The budget moved from 20 MB to 45 MB when the image classifier became
        default content: the weights are 21.4 MB and they ship inside the
        deployment, because this product is offline by design and image
        filtering that needs a download is image filtering that does not work.
        An embeddable CPython adds ~15 MB on top of this number.
        """
        manifest = packaging.build_manifest(ROOT)
        megabytes = manifest.total_bytes / 1048576
        self.assertLess(megabytes, 45,
                        msg=f"engine+host+data+model is {megabytes:.1f} MB; the "
                            f"budget is 45 MB (21.4 MB of it is the model)")

    def test_the_model_is_shipped_and_its_licence_travels_with_it(self):
        """Default content ships, and Apache-2.0 requires the notice beside it.

        A deployment without the model enforces nothing for images and reports
        it only on the console. Shipping the weights without the licence is a
        licence violation, so the two facts are asserted together.
        """
        manifest = packaging.build_manifest(ROOT)
        shipped = {e.destination for e in manifest.entries}
        self.assertIn(f"data/{packaging.MODEL_DIR}/{packaging.MODEL_FILE}",
                      shipped, msg="the image classifier is not packaged")
        self.assertIn(f"data/{packaging.MODEL_DIR}/LICENSE", shipped,
                      msg="the model's licence is not packaged with it")

    def test_a_missing_model_is_a_build_error_not_a_warning(self):
        """A deployment that quietly lacks the model says nothing at all."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "green_dam").mkdir(parents=True)
            (root / "host").mkdir()
            (root / "data").mkdir()
            (root / "data" / "verb-table.json").write_text("{}", encoding="utf-8")
            manifest = packaging.build_manifest(root)
        reasons = " ".join(why for _path, why in manifest.missing)
        self.assertIn("image classifier", reasons,
                      msg=f"a model-less tree produced no complaint: "
                          f"{manifest.missing}")

    def test_no_test_file_reaches_the_deployment(self):
        manifest = packaging.build_manifest(ROOT)
        offenders = [e.destination for e in manifest.entries
                     if "/tests/" in f"/{e.destination}"
                     or Path(e.destination).name.startswith("test_")]
        self.assertEqual(offenders, [])


class ModelLocationTest(unittest.TestCase):
    """The model ships with the deployment; the code has to look there.

    A third join of the same shape as the scoring endpoint and the backend
    wiring: ``host/packaging.py`` put the weights in ``<deployment>/data/
    models/nsfw-384/`` while ``host/engine.py`` looked only in
    ``%ProgramData%``. A portable install would have shipped 21.4 MB it never
    opened, and reported "no model" while doing it.
    """

    def test_the_bundle_directory_is_searched_first(self):
        """Portable means self-contained: the model beside the code wins.

        Order matters. If ``%ProgramData%`` won, a portable install would behave
        differently on a machine that happened to have a machine-wide copy, and
        "portable" would stop being a property of the directory.
        """
        from host import engine

        paths = engine.model_search_path()
        self.assertGreaterEqual(len(paths), 2)
        self.assertEqual(paths[0], engine.bundled_root() / "data" / "models"
                         / "nsfw-384")
        self.assertEqual(paths[1],
                         engine.paths.data_dir() / "models" / "nsfw-384")

    def test_a_directory_without_the_weights_is_not_a_model(self):
        """README and LICENSE ship beside the weights; they are not weights.

        A deployment built with ``--no-optional`` still has the directory, so a
        check for the directory alone would report a model that cannot load.
        """
        import tempfile
        from unittest import mock

        from host import engine

        with tempfile.TemporaryDirectory() as td:
            model_dir = Path(td) / "models" / "nsfw-384"
            model_dir.mkdir(parents=True)
            (model_dir / "README.md").write_text("provenance", encoding="utf-8")
            (model_dir / "LICENSE").write_text("Apache-2.0", encoding="utf-8")
            with mock.patch.object(engine, "model_search_path",
                                   return_value=(model_dir,)):
                self.assertIsNone(engine.find_model(),
                                  msg="a directory holding only documentation "
                                      "was reported as a usable model")

    def test_the_shipped_checkout_has_a_findable_model(self):
        """This repository ships the weights, so find_model must find them.

        Skipped when the model is absent from the checkout - it is gitignored,
        so a fresh clone legitimately has none. The packaging test is what
        fails loudly in that case; this one documents the intent.
        """
        from host import engine

        bundled = engine.bundled_root() / "data" / "models" / "nsfw-384"
        if not (bundled / "model.onnx").is_file():
            raise unittest.SkipTest("the model is not present in this checkout")
        self.assertEqual(engine.find_model(), bundled)

    def test_the_console_and_the_scorer_agree_about_the_model(self):
        """Two places answer "is image filtering on"; they must not disagree.

        The console said "no model" while the scorer loaded one, which sends a
        parent looking in the wrong place. Both now call ``find_model``.
        """
        from host import engine
        from host.ui import __main__ as console

        found = engine.find_model()
        reported = console._image_backend()
        if found is None:
            self.assertIsNone(reported)
        else:
            self.assertEqual(reported, "marqo-384")


if __name__ == "__main__":
    unittest.main()
