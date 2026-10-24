"""Capability probing and path layout: the two things every other host module
assumes.

Both are tested with the environment cleared, because a test that inherits the
real ``%ProgramData%`` would pass on this machine and nowhere else.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from host import capabilities, paths  # noqa: E402


class ProbeTest(unittest.TestCase):
    def test_probe_never_raises(self):
        """A probe that raised could not report what is missing."""
        result = capabilities.probe()
        self.assertIsInstance(result, capabilities.Capabilities)
        self.assertTrue(result.features)

    def test_every_feature_has_a_recognised_state(self):
        for feature in capabilities.probe().features:
            self.assertIn(feature.state,
                          {"present", "absent", "broken", "wrong_os"},
                          msg=f"{feature.name} has state {feature.state!r}")

    def test_states_are_mutually_exclusive(self):
        result = capabilities.probe()
        seen = 0
        for state in ("present", "absent", "broken", "wrong_os"):
            seen += len(result.by_state(state))
        self.assertEqual(seen, len(result.features))

    def test_summary_is_counts_and_names_only(self):
        blob = capabilities.probe().summary()
        self.assertEqual(set(blob), {"platform", "present", "absent",
                                     "broken", "wrong_os",
                                     "missing_required"})
        for value in blob.values():
            self.assertIsInstance(value, (list, str))

    def test_unknown_feature_is_not_usable(self):
        self.assertFalse(capabilities.probe().usable("no-such-feature"))
        self.assertIsNone(capabilities.probe().get("no-such-feature"))

    def test_missing_required_is_filtered_by_name(self):
        """M7's optional members must not be counted as required.

        Filtering by the module they serve would mark every optional M7
        feature as required, because pywin32 is required and also serves M7.
        """
        probe = capabilities.Capabilities(
            features=(
                capabilities.Feature("pywin32", "win32api", "M7", "present"),
                capabilities.Feature("pystray", "pystray", "M9", "absent"),
            ),
            platform="win32")
        self.assertEqual(probe.missing_required, ())

    def test_absent_required_is_reported(self):
        probe = capabilities.Capabilities(
            features=(
                capabilities.Feature("pywin32", "win32api", "M7", "absent"),
            ),
            platform="win32")
        self.assertEqual([f.name for f in probe.missing_required],
                         ["pywin32"])

    def test_broken_is_not_absent(self):
        """Installed-but-broken needs a different fix from not-installed."""
        with mock.patch.object(capabilities, "_try",
                               return_value=("broken", "installed, but needs 'x'")):
            result = capabilities.probe()
        self.assertTrue(result.by_state("broken"))
        self.assertFalse(result.usable("pywin32"))

    def test_non_windows_platform_reports_wrong_os(self):
        with mock.patch.object(capabilities, "_windows", return_value=False):
            result = capabilities.probe()
        states = {f.state for f in result.features}
        self.assertIn("wrong_os", states,
                      msg="a non-Windows machine must say it did not look, "
                          "not that the feature is absent")
        self.assertNotIn("absent", states)

    def test_windows_only_features_are_marked_wrong_os(self):
        with mock.patch.object(capabilities, "_windows", return_value=False):
            result = capabilities.probe()
        for name in ("pywin32", "psutil", "pywebview"):
            self.assertEqual(result.get(name).state, "wrong_os", msg=name)


class PathLayoutTest(unittest.TestCase):
    """B7 plus the layout contract from docs/dependency-audit.md §4."""

    def _clean_env(self):
        return mock.patch.dict(os.environ, {}, clear=False)

    def test_data_dir_is_under_program_data(self):
        with mock.patch.dict(os.environ,
                             {"ProgramData": r"D:\fake\ProgramData"}):
            self.assertEqual(paths.data_dir(),
                             Path(r"D:\fake\ProgramData") / paths.APP_NAME)

    def test_user_dir_is_under_local_app_data(self):
        with mock.patch.dict(os.environ,
                             {"LOCALAPPDATA": r"D:\fake\Local"}):
            self.assertEqual(paths.user_dir(),
                             Path(r"D:\fake\Local") / paths.APP_NAME)

    def test_config_and_log_are_inside_the_data_dir(self):
        with mock.patch.dict(os.environ,
                             {"ProgramData": r"D:\fake\ProgramData"}):
            root = paths.data_dir()
            self.assertEqual(paths.config_dir(), root / "config")
            self.assertEqual(paths.log_path(), root / "logs" / "events.jsonl")
            self.assertEqual(paths.journal_path(),
                             root / "journal" / "install.json")

    def test_layout_has_no_system_directory(self):
        for path in (paths.data_dir(), paths.user_dir(), paths.config_dir(),
                     paths.log_path(), paths.journal_path()):
            self.assertNotIn("system32", str(path).lower(), msg=str(path))

    def test_assert_writable_accepts_data_dir(self):
        with mock.patch.dict(os.environ,
                             {"ProgramData": r"D:\fake\ProgramData",
                              "SystemRoot": r"C:\Windows"}):
            self.assertEqual(paths.assert_writable(paths.config_dir()),
                             paths.config_dir().resolve())

    def test_assert_writable_rejects_system_directory(self):
        with mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}):
            with self.assertRaises(paths.SystemDirectoryError):
                paths.assert_writable(Path(r"C:\Windows\System32\evil.dat"))

    def test_assert_writable_rejects_windows_root_itself(self):
        with mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}):
            with self.assertRaises(paths.SystemDirectoryError):
                paths.assert_writable(Path(r"C:\Windows\evil.dat"))

    def test_assert_writable_uses_the_real_system_root(self):
        """The guard must read %SystemRoot%, not a hard-coded C:\\Windows.

        A hard-coded path would make the check vacuous on a machine whose
        Windows lives elsewhere - which is exactly the machine where a
        wrong write would be hardest to notice.
        """
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SystemRoot": td}):
                with self.assertRaises(paths.SystemDirectoryError):
                    paths.assert_writable(Path(td) / "System32" / "x.dat")
                # A sibling directory is fine.
                outside = Path(td).parent / "elsewhere"
                self.assertEqual(paths.assert_writable(outside),
                                 outside.resolve())

    def test_error_message_names_the_requirement(self):
        with mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}):
            with self.assertRaises(paths.SystemDirectoryError) as ctx:
                paths.assert_writable(Path(r"C:\Windows\System32\x"))
        self.assertIn("B7", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
