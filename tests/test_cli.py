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


class ScreenTest(unittest.TestCase):
    def test_clean_frame(self):
        code, out, _ = run(["screen", "--width", "640", "--height", "480"])
        self.assertEqual(code, 0)
        self.assertIn("suspect        : False", out)

    def test_thin_frame_fires(self):
        code, out, _ = run(["screen", "--width", "1000", "--height", "32",
                            "--red", "255"])
        self.assertEqual(code, 0)
        self.assertIn("suspect        : True", out)
        self.assertIn("aspect", out)

    def test_json_shape(self):
        code, out, _ = run(["screen", "--width", "64", "--height", "64",
                            "--red", "10", "--json"])
        payload = json.loads(out)
        self.assertIn("suspect", payload)
        self.assertIn("checks_run", payload)
        self.assertIn("aspect_ratio", payload["metrics"])

    def test_red_out_of_range(self):
        code, _, err = run(["screen", "--width", "64", "--height", "64",
                            "--red", "999"])
        self.assertEqual(code, 2)
        self.assertIn("0..255", err)

    def test_missing_dimensions_errors(self):
        code, _, _ = run(["screen", "--width", "64"])
        self.assertNotEqual(code, 0)


class ProcessPathTest(unittest.TestCase):
    def test_process_option_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["check", "--config", td,
                                "--process-name", "notepad.exe"])
            self.assertEqual(code, 0)
            self.assertIn("action : None", out)

    def test_process_option_in_json(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = run(["check", "--config", td, "--json",
                                "--process-name", "notepad.exe"])
            payload = json.loads(out)
            self.assertIn("events", payload)
            self.assertEqual(len(payload["events"]), 1)


class ReplayTest(unittest.TestCase):
    def _write(self, td, payload):
        path = Path(td) / "flows.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_replays_and_reports_stage(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write(td, [
                {"host": "example.com", "port": 80, "protocol": "http"},
            ])
            code, out, _ = run(["replay", str(path), "--config", td])
            self.assertEqual(code, 0)
            self.assertIn("stage", out)

    def test_pinned_certificate_shows_limit(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write(td, [
                {"host": "a.example", "port": 443, "protocol": "https",
                 "pinned": True},
            ])
            code, out, _ = run(["replay", str(path), "--config", td,
                                "--ca-installed"])
            self.assertEqual(code, 0)
            self.assertIn("certificate_pinned", out)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write(td, [
                {"host": "a.example", "port": 80},
                {"host": "b.example", "port": 443, "protocol": "quic"},
            ])
            code, out, _ = run(["replay", str(path), "--config", td, "--json"])
            rows = json.loads(out)
            self.assertEqual(len(rows), 2)
            self.assertIn("quic_not_proxied", rows[1]["limits"])

    def test_invalid_flow_file_errors(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text('[{"host": "a"}]', encoding="utf-8")
            code, _, err = run(["replay", str(path), "--config", td])
            self.assertEqual(code, 2)
            self.assertIn("invalid flow file", err)

    def test_hosts_not_echoed(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._write(td, [
                {"host": "secret.example", "port": 80},
            ])
            code, out, _ = run(["replay", str(path), "--config", td, "--json"])
            self.assertEqual(code, 0)
            self.assertNotIn("secret.example", out)


class AbTest(unittest.TestCase):
    def _corpus(self, td, lines):
        path = Path(td) / "corpus.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_reports_summary(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._corpus(td, ["hello world", "clean text here"])
            code, out, _ = run(["ab", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("mean agreement", out)

    def test_json_shape(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._corpus(td, ["hello world"])
            code, out, _ = run(["ab", str(path), "--json"])
            payload = json.loads(out)
            for key in ("texts", "mean_agreement", "identical", "disagreed"):
                self.assertIn(key, payload)

    def test_empty_corpus_errors(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "empty.txt"
            path.write_text("", encoding="utf-8")
            code, _, err = run(["ab", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("empty", err)

    def test_missing_corpus_errors(self):
        code, _, err = run(["ab", "nope.txt"])
        self.assertEqual(code, 2)

    def test_does_not_echo_corpus_text(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._corpus(td, ["private-marker-xyz"])
            code, out, _ = run(["ab", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertNotIn("private-marker-xyz", out)


class PasswordCliTest(unittest.TestCase):
    def test_generate(self):
        code, out, _ = run(["password", "generate", "--length", "16"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip()), 16)

    def test_set_then_verify(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "pw")
            code, _, _ = run(["password", "set", "--path", path,
                              "--password", "abc-def-123"])
            self.assertEqual(code, 0)
            code, out, _ = run(["password", "verify", "--path", path,
                                "--password", "abc-def-123"])
            self.assertEqual(code, 0)
            self.assertIn("ok", out)

    def test_wrong_password_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "pw")
            run(["password", "set", "--path", path, "--password", "right"])
            code, out, _ = run(["password", "verify", "--path", path,
                                "--password", "wrong"])
            self.assertEqual(code, 1)
            self.assertIn("wrong", out)

    def test_show_reports_parameters(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "pw")
            run(["password", "set", "--path", path, "--password", "x"])
            code, out, _ = run(["password", "show", "--path", path, "--json"])
            payload = json.loads(out)
            self.assertEqual(payload["algorithm"], "pbkdf2_sha256")
            self.assertIn("iterations", payload)

    def test_show_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            code, _, err = run(["password", "show",
                                "--path", str(Path(td) / "absent")])
            self.assertEqual(code, 2)

    def test_never_echoes_the_password(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "pw")
            secret_value = "do-not-echo-me"
            code, out, _ = run(["password", "set", "--path", path,
                                "--password", secret_value])
            self.assertEqual(code, 0)
            self.assertNotIn(secret_value, out)
            code, out, _ = run(["password", "show", "--path", path, "--json"])
            self.assertNotIn(secret_value, out)


class UninstallCliTest(unittest.TestCase):
    def _journal(self, td):
        from green_dam import journal
        path = Path(td) / "journal.json"
        m = journal.Manifest(version="0.1.0")
        m.record(journal.ChangeKind.FIREWALL_RULE, "rule-1", "remove-rule")
        m.record(journal.ChangeKind.CERTIFICATE, "thumb-1", "revoke-cert")
        m.record(journal.ChangeKind.FILE, "C:/GD/x.exe", "delete-file")
        m.save(path)
        return path

    def test_plan_lists_reverse_order(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._journal(td)
            code, out, _ = run(["uninstall", "plan", "--manifest", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("would perform 3 step", out)
            # Reverse of install: file, cert, rule.
            self.assertLess(out.index("x.exe"), out.index("thumb-1"))
            self.assertLess(out.index("thumb-1"), out.index("rule-1"))

    def test_plan_says_no_reboot(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._journal(td)
            code, out, _ = run(["uninstall", "plan", "--manifest", str(path)])
            self.assertIn("no reboot", out.lower())

    def test_json_summary_is_counts_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._journal(td)
            code, out, _ = run(["uninstall", "plan", "--manifest", str(path),
                                "--json"])
            payload = json.loads(out)
            self.assertEqual(payload["steps"], 3)
            self.assertTrue(payload["complete"])
            self.assertNotIn("x.exe", out)

    def test_verify_ok(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._journal(td)
            code, out, _ = run(["uninstall", "verify", "--manifest", str(path)])
            self.assertEqual(code, 0)
            self.assertIn("all reversible", out)

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            code, _, err = run(["uninstall", "plan", "--manifest",
                                str(Path(td) / "absent.json")])
            self.assertEqual(code, 2)

    def test_rollback_requires_password(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._journal(td)
            code, _, err = run(["uninstall", "rollback", "--manifest", str(path),
                                "--password", "x",
                                "--password-file", str(Path(td) / "absent.pw")])
            self.assertEqual(code, 2)
            self.assertIn("no password record", err)

    def test_rollback_with_wrong_password_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._journal(td)
            pw = Path(td) / "pw"
            run(["password", "set", "--path", str(pw), "--password", "right"])
            code, _, err = run(["uninstall", "rollback", "--manifest", str(path),
                                "--password", "wrong",
                                "--password-file", str(pw)])
            self.assertEqual(code, 1)
            self.assertIn("wrong password", err)

    def test_rollback_with_correct_password_marks(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._journal(td)
            pw = Path(td) / "pw"
            run(["password", "set", "--path", str(pw), "--password", "right"])
            code, out, _ = run(["uninstall", "rollback", "--manifest", str(path),
                                "--password", "right",
                                "--password-file", str(pw)])
            self.assertEqual(code, 0)
            from green_dam import journal
            self.assertTrue(journal.Manifest.load(path).rolled_back)


class ChunkCliTest(unittest.TestCase):
    def test_shows_structure(self):
        code, out, _ = run(["chunk", "--text", "张三打断了李四的腿"])
        self.assertEqual(code, 0)
        self.assertIn("structured", out)
        self.assertIn("A+E+B", out)

    def test_hides_block_text_by_default(self):
        code, out, _ = run(["chunk", "--text", "张三打断了李四的腿"])
        self.assertEqual(code, 0)
        for fragment in ("张三", "李四", "打断"):
            self.assertNotIn(fragment, out)

    def test_show_text_opt_in(self):
        code, out, _ = run(["chunk", "--text", "张三打断了李四的腿",
                            "--show-text"])
        self.assertIn("张三", out)

    def test_json(self):
        code, out, _ = run(["chunk", "--text", "如果下雨，比赛就取消", "--json"])
        payload = json.loads(out)
        self.assertEqual(payload["summary"]["shape"], "structured")
        self.assertEqual(payload["roles"], ["Cn", "A", "E"])

    def test_json_has_no_text(self):
        code, out, _ = run(["chunk", "--text", "张三打断了李四的腿", "--json"])
        for fragment in ("张三", "李四"):
            self.assertNotIn(fragment, out)

    def test_no_predicate(self):
        code, out, _ = run(["chunk", "--text", "甲乙"])
        self.assertIn("no_predicate", out)


class SimilarCliTest(unittest.TestCase):
    def test_scores_a_reworded_pair(self):
        code, out, _ = run(["similar", "--left", "他认真地看书",
                            "--right", "他仔细地看书"])
        self.assertEqual(code, 0)
        self.assertIn("scored     : True", out)
        self.assertIn("similar    : True", out)

    def test_refuses_unstructured_pair(self):
        code, out, _ = run(["similar", "--left", "张三打断了李四的腿",
                            "--right", "王五擦干净了桌子"])
        self.assertEqual(code, 0)
        self.assertIn("scored     : False", out)
        self.assertIn("degraded_chunking", out)

    def test_json(self):
        code, out, _ = run(["similar", "--left", "他认真地看书",
                            "--right", "他仔细地看书", "--json"])
        payload = json.loads(out)
        self.assertTrue(payload["scored"])
        self.assertIn("metrics", payload)

    def test_does_not_echo_sentences(self):
        code, out, _ = run(["similar", "--left", "私密甲句子",
                            "--right", "私密乙句子", "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn("私密", out)

    def test_missing_sentence_errors(self):
        code, _, err = run(["similar", "--left", "只有一个"])
        self.assertEqual(code, 2)

    def test_threshold_override(self):
        code, out, _ = run(["similar", "--left", "张三打断了李四的腿",
                            "--right", "李四的腿被张三打断",
                            "--threshold", "0.1"])
        self.assertIn("similar    : True", out)


class TextureCliTest(unittest.TestCase):
    def test_grades_a_stddev(self):
        code, out, _ = run(["texture", "--stddev", "200"])
        self.assertEqual(code, 0)
        self.assertIn("grade          : 5", out)

    def test_zero_weight_grade_reports_not_passing(self):
        code, out, _ = run(["texture", "--stddev", "80"])
        self.assertIn("grade          : 3", out)
        self.assertIn("passes texture : False", out)

    def test_below_threshold(self):
        code, out, _ = run(["texture", "--stddev", "1.0"])
        self.assertIn("above 1.4      : False", out)

    def test_integer_aspect_gate(self):
        code, out, _ = run(["texture", "--stddev", "200",
                            "--width", "900", "--height", "100"])
        self.assertIn("aspect ok      : False", out)

    def test_json(self):
        code, out, _ = run(["texture", "--stddev", "200", "--json"])
        payload = json.loads(out)
        self.assertEqual(payload["grade"], 5)
        self.assertTrue(payload["passes_texture"])
        self.assertAlmostEqual(payload["weight"], 0.749116911)
