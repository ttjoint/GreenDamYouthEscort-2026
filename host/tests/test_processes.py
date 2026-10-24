"""H3 / B8: the process sweep, and the three refusals that bound it.

Two things are being tested here and they are different in kind:

*Behaviour* - the sweep asks the engine, reports what it would do, and does it
only when told to. Tested against a fake process source, so no real process is
ever touched and the test runs anywhere.

*Refusal* - the product never kills Windows, a security tool, or itself. Tested
against the guard directly, with the names that matter, because this is
requirement B8 and a false negative here is the failure the whole project exists
to avoid.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from host import protection  # noqa: E402
from host.engine import open_filter_from  # noqa: E402
from host.processes import ProcessInfo, sweep  # noqa: E402


def _engine(tmp: Path):
    """A Filter with a fresh install's defaults and an empty word list."""
    built, _wiring = open_filter_from(tmp / "config")
    return built


def _source(*pairs: tuple[int, str]):
    rows = tuple(ProcessInfo(pid=pid, name=name) for pid, name in pairs)
    return lambda: rows


class GuardTest(unittest.TestCase):
    """The refusal list itself, before any process is involved."""

    def test_critical_processes_are_refused(self):
        for name in ("csrss.exe", "lsass.exe", "winlogon.exe", "services.exe",
                     "smss.exe", "wininit.exe", "svchost.exe"):
            self.assertTrue(protection.is_protected(name), msg=name)

    def test_security_tools_are_refused(self):
        for name in ("MsMpEng.exe", "QQPCTray.exe", "QQPCMgr.exe",
                     "ZhuDongFangYu.exe", "avastui.exe", "ekrn.exe",
                     "SentinelAgent.exe", "CSFalconService.exe",
                     "360tray.exe", "360Safe.exe", "HipsTray.exe",
                     "hrsword.exe", "kxetray.exe", "avp.exe"):
            self.assertTrue(protection.is_protected(name), msg=name)

    def test_ordinary_programs_are_not_refused(self):
        """The guard must not refuse everything; a sweep that never acts is
        as broken as one that acts on the wrong thing."""
        for name in ("notepad.exe", "chrome.exe", "game.exe", "steam.exe",
                     "discord.exe", "python.exe"):
            self.assertFalse(protection.is_protected(name), msg=name)

    def test_own_process_tree_is_refused(self):
        self.assertTrue(protection.is_protected("anything.exe", pid=42,
                                                self_pids=(42,)))

    def test_own_process_refused_even_if_name_is_harmless(self):
        why = protection.why_protected("notepad.exe", pid=7, self_pids=(7,))
        self.assertIsNotNone(why)
        self.assertEqual(why[0], "self")

    def test_case_and_path_are_normalised(self):
        self.assertTrue(protection.is_protected(r"C:\Windows\SYSTEM32\CSRSS.EXE"))
        self.assertTrue(protection.is_protected("CSRSS.EXE"))
        self.assertTrue(protection.is_protected("csrss"))

    def test_categories_are_distinct(self):
        """The reason a process is refused decides the message the parent sees."""
        self.assertEqual(
            protection.why_protected("csrss.exe")[0], "critical")
        self.assertEqual(
            protection.why_protected("MsMpEng.exe")[0], "security")
        self.assertEqual(
            protection.why_protected("x.exe", pid=1, self_pids=(1,))[0], "self")

    def test_critical_is_checked_before_security(self):
        """``explorer`` is critical; a security-sounding name must not win."""
        self.assertEqual(
            protection.why_protected("explorer.exe")[0], "critical")

    def test_every_critical_name_is_lowercase(self):
        for name in protection.CRITICAL_PROCESSES:
            self.assertEqual(name, name.lower(), msg=name)

    def test_every_security_hint_is_lowercase(self):
        for hint in protection.SECURITY_HINTS:
            self.assertEqual(hint, hint.lower(), msg=hint)

    def test_refusal_is_loggable_and_comparable(self):
        a = protection.Refusal(1, "csrss.exe", "critical", "why")
        b = protection.Refusal(1, "csrss.exe", "critical", "different text")
        self.assertEqual(a, b, msg="equality must ignore the prose")
        self.assertEqual(a.to_dict()["category"], "critical")


class SweepTest(unittest.TestCase):
    def test_dry_run_terminates_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("notepad.exe")
            report = sweep(engine, source=_source((10, "notepad.exe")))
            self.assertFalse(report.applied)
            self.assertEqual(report.terminated, ())
            self.assertEqual(len(report.would_terminate), 1)

    def test_engine_decides_not_the_host(self):
        """An unwatched process must never appear in the report."""
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            report = sweep(engine, source=_source((10, "chrome.exe")))
            self.assertEqual(report.blocked, ())
            self.assertEqual(report.seen, 1)

    def test_protected_watched_process_is_refused_not_acted_on(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("msmpeng.exe")
            report = sweep(engine, source=_source((10, "MsMpEng.exe")),
                           apply=True)
            self.assertEqual(report.terminated, (),
                             msg="a security tool must never be terminated, "
                                 "even when the parent asked for it")
            self.assertEqual(len(report.refusals), 1)
            self.assertEqual(report.refusals[0].category, "security")

    def test_refusal_is_surfaced_as_needing_attention(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("csrss.exe")
            report = sweep(engine, source=_source((4, "csrss.exe")), apply=True)
            self.assertTrue(report.results[0].needs_attention,
                            msg="a parent who asked to block csrss should be "
                                "told the product will not, not left guessing")

    def test_gate_off_means_nothing_is_blocked(self):
        """Slot 2 off silences the whole chain; the sweep must respect it."""
        from green_dam import sysdat

        with tempfile.TemporaryDirectory() as td:
            values = [0] * sysdat.SLOT_COUNT
            values[sysdat.slot_index(2)] = 1      # slot 2: filtering disabled
            built, _wiring = open_filter_from(
                Path(td) / "config",
                legacy=sysdat.PolicyArray(values=tuple(values)))
            built.lists["B"].add("notepad.exe")
            report = sweep(built, source=_source((10, "notepad.exe")))
            self.assertEqual(report.blocked, ())

    def test_summary_never_carries_a_command_line(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("notepad.exe")
            report = sweep(engine, source=_source((10, "notepad.exe")))
            blob = repr(report.summary())
            self.assertIn("would_terminate", blob)
            self.assertNotIn("cmdline", blob.lower())

    def test_on_result_callback_sees_every_row(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("notepad.exe")
            engine.lists["B"].add("game.exe")
            seen = []
            sweep(engine, source=_source((1, "notepad.exe"), (2, "game.exe"),
                                         (3, "chrome.exe")),
                  on_result=seen.append)
            self.assertEqual(len(seen), 2)

    def test_pid_is_preserved_for_logging(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("notepad.exe")
            report = sweep(engine, source=_source((4321, "notepad.exe")))
            self.assertEqual(report.results[0].process.pid, 4321)


class SelfProtectionTest(unittest.TestCase):
    """A sweep must not terminate the process running it."""

    def test_own_pid_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            engine = _engine(Path(td))
            engine.lists["B"].add("python.exe")
            report = sweep(engine,
                           source=_source((999, "python.exe")),
                           apply=True, self_pids=(999,))
            self.assertEqual(report.terminated, ())
            self.assertEqual(report.refusals[0].category, "self")


if __name__ == "__main__":
    unittest.main()
