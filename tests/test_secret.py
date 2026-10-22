"""Tests for parent-password storage.

The properties that matter are security properties, so several tests exist to
assert that a weakness is *absent* rather than that a feature works:

* no default password exists
* comparison is constant-time
* a record never prints its digest
* a weak stored record is rejected

Iteration counts in tests are the minimum accepted, to keep the suite fast.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from green_dam import secret  # noqa: E402
from green_dam.secret import (  # noqa: E402
    ALGORITHM,
    DEFAULT_ITERATIONS,
    KEY_BYTES,
    MIN_ITERATIONS,
    NoPasswordSet,
    PasswordError,
    PasswordRecord,
    hash_password,
    verify,
)

FAST = MIN_ITERATIONS          # keep the suite quick


def _record(password="correct horse", iterations=FAST):
    return hash_password(password, iterations=iterations)


class NoDefaultTest(unittest.TestCase):
    """The original shipped a hardcoded password; this must not."""

    #: String constants that are legitimately present: identifiers and format
    #: tokens, not secrets.
    ALLOWED_STRINGS = {"ALGORITHM", "PREFIX"}

    def test_no_password_like_constant(self):
        import re as _re
        for name in dir(secret):
            if not name.isupper() or name in self.ALLOWED_STRINGS:
                continue
            value = getattr(secret, name)
            self.assertNotIsInstance(
                value, str,
                msg=f"secret.{name} is an unexpected string constant")
        # And no seven-digit literal anywhere in the module: that is the shape
        # of the legacy hardcoded password.
        source = (ROOT / "src" / "green_dam" / "secret.py").read_text(encoding="utf-8")
        for match in _re.finditer(r"(?<![\w.])(\d{7})(?![\w.])", source):
            self.fail(f"seven-digit literal {match.group(1)!r} in secret.py; "
                      f"that is the shape of the legacy hardcoded password")

    def test_empty_password_rejected(self):
        with self.assertRaises(PasswordError):
            hash_password("")

    def test_missing_file_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(NoPasswordSet):
                secret.load(Path(td) / "absent")
            with self.assertRaises(NoPasswordSet):
                secret.check(Path(td) / "absent", "anything")

    def test_empty_file_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pw"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(NoPasswordSet):
                secret.load(path)


class HashTest(unittest.TestCase):
    def test_algorithm_is_the_one_required(self):
        self.assertEqual(ALGORITHM, "pbkdf2_sha256")

    def test_default_iterations_meet_owasp_floor(self):
        self.assertGreaterEqual(DEFAULT_ITERATIONS, 600_000)

    def test_digest_length(self):
        self.assertEqual(len(_record().digest), KEY_BYTES)

    def test_salt_is_random(self):
        a, b = _record(), _record()
        self.assertNotEqual(a.salt, b.salt)
        self.assertNotEqual(a.digest, b.digest)

    def test_same_password_same_salt_gives_same_digest(self):
        salt = os.urandom(secret.SALT_BYTES)
        a = hash_password("x", iterations=FAST, salt=salt)
        b = hash_password("x", iterations=FAST, salt=salt)
        self.assertEqual(a.digest, b.digest)

    def test_different_passwords_differ(self):
        salt = os.urandom(secret.SALT_BYTES)
        a = hash_password("one", iterations=FAST, salt=salt)
        b = hash_password("two", iterations=FAST, salt=salt)
        self.assertNotEqual(a.digest, b.digest)

    def test_unicode_password(self):
        rec = hash_password("口令甲乙", iterations=FAST)
        self.assertTrue(verify("口令甲乙", rec))

    def test_short_salt_rejected(self):
        with self.assertRaises(PasswordError):
            PasswordRecord(salt=b"short", digest=b"x" * KEY_BYTES,
                           iterations=FAST)

    def test_wrong_digest_length_rejected(self):
        with self.assertRaises(PasswordError):
            PasswordRecord(salt=os.urandom(16), digest=b"short",
                           iterations=FAST)

    def test_weak_iterations_rejected(self):
        with self.assertRaises(PasswordError):
            PasswordRecord(salt=os.urandom(16), digest=b"x" * KEY_BYTES,
                           iterations=1)


class VerifyTest(unittest.TestCase):
    def test_correct_password(self):
        rec = _record("s3cret")
        self.assertTrue(verify("s3cret", rec))

    def test_wrong_password(self):
        rec = _record("s3cret")
        self.assertFalse(verify("s3cret ", rec))
        self.assertFalse(verify("S3cret", rec))
        self.assertFalse(verify("", rec))

    def test_uses_compare_digest(self):
        """Timing-safe comparison, asserted structurally.

        A behavioural timing test is flaky in CI, so this checks that the code
        calls compare_digest rather than ==. That is the property we actually
        control.
        """
        source = (ROOT / "src" / "green_dam" / "secret.py").read_text(encoding="utf-8")
        self.assertIn("hmac.compare_digest", source)


class SerialisationTest(unittest.TestCase):
    def test_round_trip(self):
        rec = _record("abc")
        back = PasswordRecord.decode(rec.encode())
        self.assertEqual(back.salt, rec.salt)
        self.assertEqual(back.digest, rec.digest)
        self.assertEqual(back.iterations, rec.iterations)
        self.assertTrue(verify("abc", back))

    def test_format_shape(self):
        text = _record("abc").encode()
        self.assertTrue(text.startswith("$pbkdf2_sha256$"))
        self.assertEqual(len(text.split("$")), 5)   # '', alg, iter, salt, digest

    def test_rejects_wrong_prefix(self):
        with self.assertRaises(PasswordError):
            PasswordRecord.decode("$md5$100000$AAAA$BBBB")

    def test_rejects_wrong_field_count(self):
        with self.assertRaises(PasswordError):
            PasswordRecord.decode("$pbkdf2_sha256$100000$AAAA")

    def test_rejects_non_integer_iterations(self):
        with self.assertRaises(PasswordError):
            PasswordRecord.decode("$pbkdf2_sha256$many$AAAA$BBBB")

    def test_rejects_bad_base64(self):
        with self.assertRaises(PasswordError):
            PasswordRecord.decode("$pbkdf2_sha256$100000$!!!$BBBB")

    def test_rejects_weak_record_on_decode(self):
        # A tampered file must not be accepted just because it parses.
        weak = PasswordRecord(salt=os.urandom(16), digest=b"x" * KEY_BYTES,
                              iterations=FAST).encode().replace(
                                  f"${FAST}$", "$1$")
        with self.assertRaises(PasswordError):
            PasswordRecord.decode(weak)


class ReprTest(unittest.TestCase):
    def test_repr_hides_secrets(self):
        rec = _record("abc")
        text = repr(rec)
        self.assertNotIn(rec.digest.hex(), text)
        self.assertNotIn(rec.salt.hex(), text)
        self.assertIn("pbkdf2_sha256", text)


class RehashTest(unittest.TestCase):
    def test_weak_record_needs_rehash(self):
        self.assertTrue(_record(iterations=MIN_ITERATIONS).needs_rehash)

    def test_current_record_does_not(self):
        self.assertFalse(_record(iterations=DEFAULT_ITERATIONS).needs_rehash)


class StorageTest(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sub" / "pw"
            rec = _record("abc")
            secret.save(rec, path)
            self.assertTrue(secret.check(path, "abc"))
            self.assertFalse(secret.check(path, "wrong"))

    def test_creates_parent_directory(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a" / "b" / "pw"
            secret.save(_record(), path)
            self.assertTrue(path.exists())

    def test_no_temp_files_left(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pw"
            secret.save(_record(), path)
            leftovers = [p.name for p in Path(td).iterdir()
                         if p.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_overwrite_replaces(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pw"
            secret.save(_record("first"), path)
            secret.save(_record("second"), path)
            self.assertFalse(secret.check(path, "first"))
            self.assertTrue(secret.check(path, "second"))

    def test_file_is_not_world_readable_where_supported(self):
        if os.name != "posix":
            self.skipTest("mode bits are POSIX-only")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pw"
            secret.save(_record(), path)
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode & 0o077, 0,
                             msg=f"file mode {oct(mode)} allows group/other")


class GenerateTest(unittest.TestCase):
    def test_length(self):
        self.assertEqual(len(secret.generate(20)), 20)

    def test_too_short_rejected(self):
        with self.assertRaises(PasswordError):
            secret.generate(8)

    def test_random(self):
        self.assertNotEqual(secret.generate(), secret.generate())

    def test_generated_password_works(self):
        rec = hash_password(secret.generate(), iterations=FAST)
        self.assertIsInstance(rec, PasswordRecord)


if __name__ == "__main__":
    unittest.main()
