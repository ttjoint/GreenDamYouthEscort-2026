"""Multi-browser policy: detection, plan construction, and reversibility.

The tests are split by what they can prove without a privileged shell:

*Detection*  runs against the real registry read-only. On this machine Chrome,
             Edge and Firefox are installed; on a bare CI runner none are. The
             assertions are therefore about *shape* and about the mechanisms
             differing, not about which browsers exist.

*Plan*       pure data. Every question about what would be written - including
             the two that matter most, "does removal undo installation" and
             "does removal touch a key we do not own" - is answerable here.

*Apply*      writes to ``HKEY_CURRENT_USER`` only, because that needs no
             elevation. That is also the scope an unprivileged install would
             use, so the test exercises a real path rather than a mock.

The apply tests clean up after themselves and assert the cleanup worked: a test
that leaves a policy key behind would make the next run of the suite behave
differently, which is the same failure mode as an uninstall that does not undo.
"""
from __future__ import annotations

import sys
import unittest
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from host import browsers as br                        # noqa: E402
from host.policy import (                              # noqa: E402
    CHROMIUM_KEYS,
    Change,
    Plan,
    extension_settings,
    firefox_policy_document,
    force_list,
    plan_install,
    plan_remove,
)
from host.policy.apply import apply_plan, read_back    # noqa: E402

#: A syntactically valid Chromium extension ID: 32 lowercase a-p characters.
#: Not a real extension - the value is never installed by these tests.
EXT_ID = "abcdefghijklmnopabcdefghijklmnop"


def _fake_browser(family: str = "chromium", name: str = "Test Browser",
                  target: str = r"HKLM\SOFTWARE\Policies\TestVendor"):
    return br.Browser(family=br.Family(family), executable=Path("C:/t/b.exe"),
                      policy_target=target, display_name=name)


class DetectionTest(unittest.TestCase):
    """Detection must not guess paths and must not hide what it cannot cover."""

    def test_detect_returns_a_tuple_of_browsers(self):
        found = br.detect()
        self.assertIsInstance(found, tuple)
        for browser in found:
            self.assertIsInstance(browser, br.Browser)

    def test_no_duplicate_executables(self):
        paths = [b.executable for b in br.detect()]
        self.assertEqual(len(paths), len(set(paths)),
                         msg="a browser was reported twice; a plan would "
                             "contain duplicate changes")

    def test_every_detected_browser_is_classified(self):
        for browser in br.detect():
            self.assertIn(str(browser.family),
                          {"chromium", "firefox", "unknown"}, msg=str(browser))

    def test_configurable_browsers_have_a_target(self):
        for browser in br.detect():
            if browser.configurable:
                self.assertTrue(browser.policy_target, msg=str(browser.executable))

    def test_unknown_browsers_have_no_target(self):
        """Guessing a policy location for an unknown vendor would enforce
        nothing while reporting success."""
        for browser in br.detect():
            if browser.family == br.UNKNOWN:
                self.assertIsNone(browser.policy_target)
                self.assertTrue(browser.detail)

    def test_chromium_targets_include_a_hive(self):
        """The scope must be part of the target, not a separate field."""
        for browser in br.detect():
            if browser.family == br.CHROMIUM:
                self.assertTrue(browser.policy_target.startswith("HKLM\\"),
                                msg=browser.policy_target)

    def test_chromium_vendors_covers_every_product(self):
        vendors = br.chromium_vendors()
        self.assertIn(r"Google\Chrome", vendors)
        self.assertIn(r"Microsoft\Edge", vendors)
        self.assertIn(r"BraveSoftware\Brave-Browser", vendors)


class ScopeTest(unittest.TestCase):
    """HKLM versus HKCU, which is the difference between an elevated and an
    unprivileged install."""

    def test_with_scope_rewrites_the_hive(self):
        browser = _fake_browser(target=r"HKLM\SOFTWARE\Policies\X")
        self.assertEqual(browser.with_scope("HKCU").policy_target,
                         r"HKCU\SOFTWARE\Policies\X")

    def test_with_scope_is_reversible(self):
        browser = _fake_browser(target=r"HKLM\SOFTWARE\Policies\X")
        round_trip = browser.with_scope("HKCU").with_scope("HKLM")
        self.assertEqual(round_trip.policy_target, browser.policy_target)

    def test_with_scope_rejects_a_bad_hive(self):
        browser = _fake_browser()
        with self.assertRaises(ValueError):
            browser.with_scope("HKXX")

    def test_with_scope_leaves_firefox_alone(self):
        """Firefox policy is a file; rewriting its path into a registry path
        would be a bug that only appears on machines with Firefox."""
        firefox = _fake_browser(family="firefox", name="Firefox",
                                target=r"C:\FF\distribution\policies.json")
        self.assertEqual(firefox.with_scope("HKCU").policy_target,
                         firefox.policy_target)

    def test_unknown_browser_is_unchanged(self):
        unknown = _fake_browser(family="unknown", target=None)
        self.assertIsNone(unknown.with_scope("HKCU").policy_target)


class InstallPlanTest(unittest.TestCase):
    def test_chromium_gets_both_keys(self):
        plan = plan_install([_fake_browser()], EXT_ID,
                            update_url="https://x/u.xml")
        names = {c.name for c in plan.changes if c.kind == "registry"}
        self.assertEqual(names, {"ExtensionInstallForcelist", "ExtensionSettings"})

    def test_firefox_gets_a_file_change(self):
        firefox = _fake_browser(family="firefox", name="Firefox",
                                target=r"C:\FF\distribution\policies.json")
        plan = plan_install([firefox], EXT_ID,
                            firefox_install_url="https://x/e.xpi")
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.changes[0].kind, "file")

    def test_missing_update_url_warns(self):
        """A force-install entry with no update URL points at the Web Store,
        where this extension is not published. The plan must say so."""
        plan = plan_install([_fake_browser()], EXT_ID)
        self.assertTrue(plan.warnings)
        self.assertIn("Web Store", plan.warnings[0])

    def test_missing_firefox_install_url_warns(self):
        firefox = _fake_browser(family="firefox", name="Firefox",
                                target=r"C:\FF\policies.json")
        plan = plan_install([firefox], EXT_ID)
        self.assertTrue(plan.warnings)
        self.assertIn("install_url", plan.warnings[0])

    def test_unconfigurable_browser_is_reported_not_skipped(self):
        """A parent whose child uses an uncovered browser must be told."""
        plan = plan_install([_fake_browser(family="unknown", target=None)], EXT_ID)
        self.assertEqual(plan.changes, ())
        self.assertTrue(plan.uncovered)

    def test_plan_never_writes(self):
        """plan_install must be pure; only apply_plan touches the registry."""
        plan = plan_install([_fake_browser()], EXT_ID, update_url="https://x/u")
        for change in plan.changes:
            self.assertIsNotNone(change.value)
            self.assertFalse(change.is_removal)


class RemovePlanTest(unittest.TestCase):
    def test_removal_covers_every_key_install_wrote(self):
        install = plan_install([_fake_browser()], EXT_ID, update_url="https://x/u")
        remove = plan_remove([_fake_browser()])
        written = {(c.target, c.name) for c in install.changes}
        deleted = {(c.target, c.name) for c in remove.changes}
        self.assertTrue(written <= deleted,
                        msg=f"these would survive uninstall: {written - deleted}")

    def test_removal_only_deletes_our_value_names(self):
        """Deleting the whole policy key would remove another tool's settings.

        On the machine this was written on, ``Microsoft\\Edge`` already contains
        ``StartupBoostEnabled``, which this product never set.
        """
        remove = plan_remove([_fake_browser()])
        for change in remove.changes:
            if change.kind == "registry":
                self.assertIn(change.name, CHROMIUM_KEYS)

    def test_removal_sweeps_vendors_that_are_no_longer_installed(self):
        """A browser uninstalled between install and uninstall leaves its policy
        key behind; a leftover force-install entry makes it retry forever."""
        remove = plan_remove([], all_chromium_vendors=(r"Vendor\Gone",))
        targets = {c.target for c in remove.changes}
        self.assertIn(r"HKLM\SOFTWARE\Policies\Vendor\Gone", targets)

    def test_removal_is_idempotent_in_shape(self):
        first = plan_remove([_fake_browser()])
        second = plan_remove([_fake_browser()])
        self.assertEqual(first.summary()["deletes"], second.summary()["deletes"])

    def test_removal_reports_uncovered_browsers(self):
        plan = plan_remove([_fake_browser(family="unknown", target=None)])
        self.assertTrue(plan.uncovered)


class PayloadTest(unittest.TestCase):
    """The two schemas are different, not differently spelled."""

    def test_chromium_force_list_format(self):
        entries = force_list(EXT_ID)
        self.assertEqual(entries, [f"{EXT_ID};"])

    def test_chromium_settings_pin_the_extension(self):
        settings = extension_settings(EXT_ID, update_url="https://x/u")
        self.assertEqual(settings[EXT_ID]["installation_mode"], "force_installed")
        self.assertEqual(settings[EXT_ID]["toolbar_pin"], "force_pinned")

    def test_firefox_document_nests_under_policies(self):
        """Firefox's file has a top-level ``policies`` object; Chromium's
        registry value does not. Writing one into the other is silently ignored."""
        document = firefox_policy_document(EXT_ID, install_url="https://x/e.xpi")
        self.assertIn("policies", document)
        self.assertIn("ExtensionSettings", document["policies"])

    def test_firefox_entry_uses_install_url_not_update_url(self):
        document = firefox_policy_document(EXT_ID, install_url="https://x/e.xpi")
        entry = document["policies"]["ExtensionSettings"][EXT_ID]
        self.assertIn("install_url", entry)
        self.assertNotIn("update_url", entry)


class HiveSplitTest(unittest.TestCase):
    def test_split_hklm(self):
        from host.policy.apply import _split_hive

        hive, rest = _split_hive(r"HKLM\SOFTWARE\Policies\X")
        self.assertEqual(hive, winreg.HKEY_LOCAL_MACHINE)
        self.assertEqual(rest, r"SOFTWARE\Policies\X")

    def test_split_hkcu(self):
        from host.policy.apply import _split_hive

        hive, rest = _split_hive(r"HKCU\SOFTWARE\Policies\X")
        self.assertEqual(hive, winreg.HKEY_CURRENT_USER)
        self.assertEqual(rest, r"SOFTWARE\Policies\X")

    def test_split_rejects_a_target_without_a_hive(self):
        """An explicit hive is required so the scope cannot be ambiguous."""
        from host.policy.apply import _split_hive

        with self.assertRaises(ValueError):
            _split_hive(r"SOFTWARE\Policies\X")


class RegistryRoundTripTest(unittest.TestCase):
    """Real writes to HKCU, which needs no elevation.

    This is the same scope an unprivileged install would use, so the test covers
    a path the product actually takes rather than a mock of one.
    """

    TEST_VENDOR = r"HKCU\SOFTWARE\Policies\GreenDam2026Test"

    def tearDown(self):
        self._clear()

    def _clear(self):
        for name in CHROMIUM_KEYS:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    self.TEST_VENDOR.split("\\", 1)[1], 0,
                                    winreg.KEY_SET_VALUE) as handle:
                    winreg.DeleteValue(handle, name)
            except (FileNotFoundError, OSError):
                pass
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER,
                               self.TEST_VENDOR.split("\\", 1)[1],
                               winreg.KEY_WOW64_64KEY)
        except (FileNotFoundError, OSError):
            pass

    def _browser(self):
        return _fake_browser(target=self.TEST_VENDOR)

    def test_write_read_back_delete(self):
        plan = plan_install([self._browser()], EXT_ID, update_url="https://x/u")
        result = apply_plan(plan)
        self.assertTrue(result.ok, msg=str(result.summary()))

        settings = [c for c in plan.changes if c.name == "ExtensionSettings"][0]
        value = read_back(settings)
        self.assertIsNotNone(value)
        self.assertIn(EXT_ID, value)

        removal = plan_remove([self._browser()])
        removal.changes = tuple(c for c in removal.changes
                                if c.target == self.TEST_VENDOR)
        result = apply_plan(removal)
        self.assertTrue(result.ok, msg=str(result.summary()))
        self.assertIsNone(read_back(settings),
                          msg="the value survived removal")

    def test_removal_is_idempotent(self):
        """An uninstall run twice must not fail the second time."""
        plan = plan_install([self._browser()], EXT_ID, update_url="https://x/u")
        apply_plan(plan)
        removal = plan_remove([self._browser()])
        removal.changes = tuple(c for c in removal.changes
                                if c.target == self.TEST_VENDOR)
        first = apply_plan(removal)
        second = apply_plan(removal)
        self.assertTrue(first.ok)
        self.assertTrue(second.ok, msg="second removal failed; it is not "
                                       "idempotent")

    def test_removal_on_a_clean_machine_succeeds(self):
        removal = plan_remove([self._browser()])
        removal.changes = tuple(c for c in removal.changes
                                if c.target == self.TEST_VENDOR)
        result = apply_plan(removal)
        self.assertTrue(result.ok, msg=str(result.summary()))

    def test_empty_key_is_removed_after_removal(self):
        plan = plan_install([self._browser()], EXT_ID, update_url="https://x/u")
        apply_plan(plan)
        removal = plan_remove([self._browser()])
        removal.changes = tuple(c for c in removal.changes
                                if c.target == self.TEST_VENDOR)
        apply_plan(removal)
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           self.TEST_VENDOR.split("\\", 1)[1])
        except FileNotFoundError:
            return
        self.fail("the policy key we created was left behind, empty")

    def test_a_key_owned_by_someone_else_survives(self):
        """Removal must not delete a policy key that still holds foreign values.

        This is the ``StartupBoostEnabled`` case: Edge's policy key on this
        machine already contains a value this product never wrote.
        """
        subkey = self.TEST_VENDOR.split("\\", 1)[1]
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0,
                                winreg.KEY_SET_VALUE) as handle:
            winreg.SetValueEx(handle, "SomebodyElsesSetting", 0,
                              winreg.REG_DWORD, 1)

        removal = plan_remove([self._browser()])
        removal.changes = tuple(c for c in removal.changes
                                if c.target == self.TEST_VENDOR)
        apply_plan(removal)

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as handle:
            value, _kind = winreg.QueryValueEx(handle, "SomebodyElsesSetting")
        self.assertEqual(value, 1,
                         msg="removal deleted a value it did not own")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0,
                            winreg.KEY_SET_VALUE) as handle:
            winreg.DeleteValue(handle, "SomebodyElsesSetting")
        try:
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, subkey,
                               winreg.KEY_WOW64_64KEY)
        except OSError:
            pass

    def test_write_to_hklm_without_elevation_fails_cleanly(self):
        """The failure must be reported, not raised.

        A non-elevated install cannot write machine policy; that is expected. What
        is not acceptable is a crash, because the installer needs to report which
        browsers it could not cover.
        """
        browser = _fake_browser(target=r"HKLM\SOFTWARE\Policies\GreenDamTestDeny")
        plan = plan_install([browser], EXT_ID, update_url="https://x/u")
        result = apply_plan(plan)
        if result.ok:
            self.skipTest("this shell is elevated; the failure path cannot be "
                          "exercised")
        self.assertTrue(result.failed)
        self.assertIn("PermissionError", result.failed[0][1])


if __name__ == "__main__":
    unittest.main()
