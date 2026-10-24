"""H2a: firewall rule construction.

Every test here runs without administrator rights, because none of them execute
anything. The command lines are built as argument vectors and asserted as data;
the single place that would call ``subprocess`` is not exercised by the suite.

That split is the point. "Does the rule name the right address" is a question
with a deterministic answer, and it should not require an elevated shell to ask.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from host import firewall  # noqa: E402


class AddressTest(unittest.TestCase):
    def test_ipv4_and_ipv6_are_addresses(self):
        for text in ("1.2.3.4", "2001:db8::1", "[::1]", "0.0.0.0"):
            self.assertTrue(firewall.is_address(text), msg=text)

    def test_host_names_are_not_addresses(self):
        """A rule containing a host name is silently ignored by Windows
        Firewall. Reporting success there would mean blocking nothing."""
        for text in ("evil.example", "localhost", "1.2.3.4.5", "1.2.3",
                     "", "1.2.3.4:80"):
            self.assertFalse(firewall.is_address(text), msg=text)


class RuleNameTest(unittest.TestCase):
    def test_name_carries_the_prefix(self):
        self.assertTrue(firewall.rule_name_for("1.2.3.4")
                        .startswith(firewall.RULE_PREFIX))

    def test_ipv6_colons_are_replaced(self):
        """A bare colon in a netsh argument is parsed as a key separator."""
        name = firewall.rule_name_for("2001:db8::1")
        self.assertNotIn(":", name)

    def test_name_is_stable(self):
        self.assertEqual(firewall.rule_name_for("1.2.3.4"),
                         firewall.rule_name_for("1.2.3.4"))

    def test_distinct_addresses_give_distinct_names(self):
        self.assertNotEqual(firewall.rule_name_for("1.2.3.4"),
                            firewall.rule_name_for("1.2.3.5"))


class BuildAddTest(unittest.TestCase):
    def test_host_names_are_rejected_not_raised(self):
        """One bad entry must not discard the good ones."""
        plan = firewall.build_add_rules(["1.2.3.4", "evil.example", "5.6.7.8"])
        self.assertEqual(len(plan.rules), 2)
        self.assertEqual(plan.rejected, ("evil.example",))

    def test_duplicates_collapse(self):
        """netsh refuses a duplicate rule name, which would fail the batch."""
        plan = firewall.build_add_rules(["1.2.3.4", "1.2.3.4", " 1.2.3.4 "])
        self.assertEqual(len(plan.rules), 1)

    def test_ipv6_is_normalised(self):
        plan = firewall.build_add_rules(["[2001:0db8::0001]"])
        self.assertEqual(plan.rules[0].remote_ip, "2001:db8::1")

    def test_empty_entries_are_skipped_silently(self):
        plan = firewall.build_add_rules(["", "  ", "1.2.3.4"])
        self.assertEqual(len(plan.rules), 1)
        self.assertEqual(plan.rejected, ())

    def test_empty_input_gives_empty_plan(self):
        plan = firewall.build_add_rules([])
        self.assertFalse(plan)
        self.assertEqual(plan.rules, ())

    def test_bad_direction_raises(self):
        with self.assertRaises(ValueError):
            firewall.build_add_rules(["1.2.3.4"], direction="sideways")

    def test_summary_has_no_command_line(self):
        plan = firewall.build_add_rules(["1.2.3.4"])
        self.assertNotIn("netsh", repr(plan.summary()))


class CommandTest(unittest.TestCase):
    """The vectors themselves: they must be argv, not shell strings."""

    def _rule(self, address: str = "1.2.3.4"):
        return firewall.build_add_rules([address]).rules[0]

    def test_command_is_an_argument_vector(self):
        cmd = firewall.build_add_rules_command(self._rule())
        self.assertIsInstance(cmd, tuple)
        for part in cmd:
            self.assertIsInstance(part, str)

    def test_command_blocks_outbound_by_default(self):
        cmd = firewall.build_add_rules_command(self._rule())
        self.assertIn("action=block", cmd)
        self.assertIn("dir=out", cmd)

    def test_command_names_the_rule_and_the_address(self):
        cmd = firewall.build_add_rules_command(self._rule("5.6.7.8"))
        self.assertIn("name=GreenDam2026-5.6.7.8", cmd)
        self.assertIn("remoteip=5.6.7.8", cmd)

    def test_command_carries_the_group(self):
        """The group is what makes uninstall possible without the journal."""
        cmd = firewall.build_add_rules_command(self._rule())
        self.assertIn(f"group={firewall.RULE_GROUP}", cmd)

    def test_no_shell_metacharacters_are_assembled(self):
        """An address can never become a second command.

        Addresses are validated as literal IPs before they get here, so this
        test documents the invariant rather than a gap: even if validation were
        bypassed, the vector is passed to ``subprocess`` without a shell.
        """
        cmd = firewall.build_add_rules_command(self._rule())
        for part in cmd:
            self.assertNotIn("&&", part)
            self.assertNotIn("|", part)
            self.assertNotIn(";", part)

    def test_remove_deletes_by_group_not_by_name(self):
        """A group delete works even when the journal is lost."""
        cmd = firewall.build_remove_rules()
        self.assertIn("delete", cmd)
        self.assertIn(f"group={firewall.RULE_GROUP}", cmd)
        self.assertFalse([p for p in cmd if p.startswith("name=")],
                         msg="removal must not depend on a rule name")

    def test_show_is_scoped_to_the_group(self):
        cmd = firewall.build_show_rules()
        self.assertIn(f"group={firewall.RULE_GROUP}", cmd)


class ReversibilityTest(unittest.TestCase):
    """M7: what install created, uninstall must be able to find."""

    def test_every_rule_is_findable_by_the_removal_command(self):
        plan = firewall.build_add_rules(["1.2.3.4", "2001:db8::1"])
        remove = firewall.build_remove_rules()
        group_arg = [p for p in remove if p.startswith("group=")]
        self.assertEqual(len(group_arg), 1)
        for rule in plan.rules:
            add = firewall.build_add_rules_command(rule)
            self.assertIn(group_arg[0], add,
                          msg=f"{rule.name} would survive uninstall: it is not "
                              f"in the group the removal command deletes")


if __name__ == "__main__":
    unittest.main()
