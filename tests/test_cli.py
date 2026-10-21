"""Tests for the diagnostic CLI.

The CLI must never print list entries or wordlist terms. Several tests assert
that by capturing stdout and checking for fixture strings.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import cli, config  # noqa: E402


def run(argv):
    """Run the CLI and return (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:          # argparse errors
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class StatusTest(unittest.TestCase):
    def test_runs_on_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["status", "--config", td])
            self.assertEqual(code, 0)
            self.assertIn("lists:", out)
            self.assertIn("unbounded", out)

    def test_json_shape(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["status", "--config", td, "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(out)
            self.assertEqual(set(payload["lists"]), {"A", "B", "C", "D"})
            self.assertIn("filtering_disabled", payload)

    def test_reads_real_config(self):
        sysdir = ROOT.parent / "LH-installed-tree" / "Windows" / "System32"
        if not (sysdir / "xconfigs.dat").exists():
            self.skipTest("legacy tree not present")
        code, out, _ = run(["status", "--config", str(sysdir)])
        self.assertEqual(code, 0)
        self.assertIn("filtering off   : False", out)


class PolicyTest(unittest.TestCase):
    def test_defaults_when_no_file(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["policy", "--config", td])
            self.assertEqual(code, 0)
            self.assertIn("shipped defaults", out)

    def test_lists_policies_by_number_and_name(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["policy", "--config", td])
            self.assertIn("image_filter_level", out)
            self.assertIn("allow_auto_screenshot", out)

    def test_json(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["policy", "--config", td, "--json"])
            payload = json.loads(out)
            names = {row["name"] for row in payload["nonzero"]}
            self.assertIn("image_filter_level", names)
            self.assertTrue(all(row["policy"] <= 25
                                for row in payload["nonzero"]))


class CheckTest(unittest.TestCase):
    def test_clean_request(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["check", "--config", td,
                                "--host", "ok.example", "--port", "80"])
            self.assertEqual(code, 0)
            self.assertIn("action : None", out)

    def test_text_without_wordlists(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["check", "--config", td, "--text", "hello"])
            self.assertEqual(code, 0)
            self.assertIn("no_wordlists", out)

    def test_json_has_events(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["check", "--config", td, "--json",
                                "--host", "ok.example", "--port", "80"])
            payload = json.loads(out)
            self.assertIn("events", payload)
            self.assertEqual(len(payload["events"]), 1)

    def test_text_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "t.txt"
            path.write_text("hello", encoding="utf-8")
            code, out, _ = run(["check", "--config", td, "--text-file", str(path)])
            self.assertEqual(code, 0)

    def test_does_not_echo_input(self):
        with tempfile.TemporaryDirectory() as td:
            secret = "private-marker-xyz"
            code, out, _ = run(["check", "--config", td, "--host",
                                secret, "--port", "80", "--json"])
            self.assertEqual(code, 0)
            self.assertNotIn(secret, out)


class ValidateTest(unittest.TestCase):
    def test_valid_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            config.save(config.default_for_fresh_install(), path)
            code, out, _ = run(["validate", str(path), "--strict"])
            self.assertEqual(code, 0)
            self.assertIn("ok:", out)

    def test_truncated_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "p.dat"
            path.write_bytes(b"\x00" * 10)
            code, _, err = run(["validate", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("invalid", err)

    def test_missing_file(self):
        code, _, err = run(["validate", "nope.dat"])
        self.assertEqual(code, 2)


class SaveDefaultsTest(unittest.TestCase):
    def test_writes_268_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "xconfigs.dat"
            code, out, _ = run(["save-defaults", str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(path.stat().st_size, 268)
            self.assertIn("268", out)


class LogTest(unittest.TestCase):
    def _write_log(self, path: Path):
        from green_dam.eventlog import Event, EventLog, LogRecord
        log = EventLog(path=path)
        log.append(LogRecord(event=Event.BLOCK, source="network",
                             stage="host", reason="host_listed", when="T"))
        log.close()

    def test_reads_back(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "e.jsonl"
            self._write_log(path)
            code, out, _ = run(["log", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("host_listed", out)

    def test_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "e.jsonl"
            self._write_log(path)
            code, out, _ = run(["log", str(path), "--json"])
            rows = json.loads(out)
            self.assertEqual(rows[0]["event"], "block")

    def test_missing_log(self):
        code, _, err = run(["log", "nope.jsonl"])
        self.assertEqual(code, 2)


class NoContentTest(unittest.TestCase):
    """No subcommand may print a list entry or a matched term."""

    def test_status_and_policy_are_counts_only(self):
        with tempfile.TemporaryDirectory() as td:
            for argv in (["status", "--config", td, "--json"],
                         ["policy", "--config", td, "--json"]):
                code, out, _ = run(argv)
                self.assertEqual(code, 0)
                payload = json.loads(out)          # must be valid JSON
                # The payload may only contain switches, counts and names -
                # no entries, no rules, no URLs.
                self.assertNotIn("http://", out)
                self.assertNotIn("{甲", out)          # a rule-shaped string
                self.assertNotIn("domain", out.lower())
                del payload


class ParserTest(unittest.TestCase):
    def test_no_subcommand_errors(self):
        code, _, err = run([])
        self.assertNotEqual(code, 0)

    def test_bad_direction_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            code, _, _ = run(["check", "--config", td, "--direction", "sideways"])
            self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
