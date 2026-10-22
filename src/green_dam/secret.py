"""Parent-password storage: PBKDF2-HMAC-SHA256, never plaintext.

``docs/architecture.md`` §M7 requires:

    tray exit and the uninstaller both verify the password
    password stored as PBKDF2-HMAC-SHA256 with a random salt, never plaintext

Why this is its own module
--------------------------
Authentication secrets have their own failure modes, and none of them belong in
a config loader or a log writer:

* **Timing.** Comparing digests with ``==`` leaks information through how early
  it returns. :func:`verify` uses :func:`hmac.compare_digest`.
* **Salt handling.** A missing or reused salt silently weakens the hash, so the
  stored form carries its own salt and parameters and cannot be constructed
  without them.
* **Parameter upgrades.** PBKDF2 iteration counts rise over time. The stored
  record includes the count it was created with, so an old record still
  verifies and can be re-hashed on next successful login.

Putting these in ``config.py`` would mean every reader of the config format also
handles secrets. Keeping them here means the config layer never sees a password.

What the original did, and what this does instead
-------------------------------------------------
The legacy product shipped a hardcoded password in the binary (a seven-digit
literal, still present in ``gn.exe`` at ``0x4d288`` and ``XNet2.exe`` at
``0x4d48c``). A hardcoded secret is not a secret: anyone with the installer has
it. This module has no default and no fallback - a system with no password
record refuses every privileged action rather than accepting a known one.

The honest limit
----------------
An administrator with local access can always reset this by replacing the file
or stopping the service. ``architecture.md`` says so explicitly: the goal is
that a *non-admin* user cannot casually bypass the filter, which is achievable,
legal, and sufficient. This module does not pretend otherwise.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

#: Hash algorithm. Fixed by architecture.md §M7.
ALGORITHM = "pbkdf2_sha256"

#: Iteration count for new records. OWASP's 2023 floor for PBKDF2-SHA256 is
#: 600,000. Higher is better but this runs on the login path of a desktop app,
#: so it is a deliberate compromise, documented rather than hidden.
DEFAULT_ITERATIONS = 600_000

#: Below this, a stored record is treated as too weak to accept. Guards against
#: a tampered or legacy file claiming 1 iteration.
MIN_ITERATIONS = 100_000

#: Salt length in bytes. 16 is the usual choice and is what the RFC recommends.
SALT_BYTES = 16

#: Derived-key length. SHA-256 output size.
KEY_BYTES = 32

#: Prefix for the serialised form, so a future format can be recognised.
PREFIX = f"${ALGORITHM}$"


class PasswordError(ValueError):
    """Raised when a password record is malformed or too weak to accept."""


@dataclass(frozen=True)
class PasswordRecord:
    """A stored password: algorithm, parameters, salt, digest.

    The serialised form is ``$pbkdf2_sha256$<iterations>$<salt>$<digest>`` with
    salt and digest base64-encoded. That is deliberately the same shape Django
    uses, so existing tooling and intuition apply.
    """

    salt: bytes
    digest: bytes
    iterations: int = DEFAULT_ITERATIONS
    algorithm: str = ALGORITHM

    def __post_init__(self) -> None:
        if self.algorithm != ALGORITHM:
            raise PasswordError(f"unsupported algorithm: {self.algorithm!r}")
        if len(self.salt) < SALT_BYTES:
            raise PasswordError(
                f"salt must be at least {SALT_BYTES} bytes, got {len(self.salt)}")
        if len(self.digest) != KEY_BYTES:
            raise PasswordError(
                f"digest must be {KEY_BYTES} bytes, got {len(self.digest)}")
        if self.iterations < MIN_ITERATIONS:
            raise PasswordError(
                f"iterations must be at least {MIN_ITERATIONS}, "
                f"got {self.iterations}")

    # -- serialisation -----------------------------------------------------

    def encode(self) -> str:
        salt = base64.b64encode(self.salt).decode("ascii")
        digest = base64.b64encode(self.digest).decode("ascii")
        return f"{PREFIX}{self.iterations}${salt}${digest}"

    @classmethod
    def decode(cls, text: str) -> "PasswordRecord":
        """Parse the serialised form, rejecting anything unexpected."""
        if not text.startswith(PREFIX):
            raise PasswordError(f"not a {ALGORITHM} record")
        parts = text[len(PREFIX):].split("$")
        if len(parts) != 3:
            raise PasswordError(
                f"expected iterations$salt$digest, got {len(parts)} fields")
        iterations_text, salt_text, digest_text = parts
        try:
            iterations = int(iterations_text)
        except ValueError as exc:
            raise PasswordError(f"iterations is not an integer: {iterations_text!r}") from exc
        try:
            salt = base64.b64decode(salt_text, validate=True)
            digest = base64.b64decode(digest_text, validate=True)
        except Exception as exc:                      # binascii.Error et al
            raise PasswordError("salt or digest is not valid base64") from exc
        return cls(salt=salt, digest=digest, iterations=iterations)

    # -- queries -----------------------------------------------------------

    @property
    def needs_rehash(self) -> bool:
        """True when the record was made with weaker parameters than current."""
        return self.iterations < DEFAULT_ITERATIONS

    def __repr__(self) -> str:
        """Never prints the salt or digest.

        A repr ends up in tracebacks. The digest is not the password, but it is
        the thing an offline attacker wants, so it stays out of logs.
        """
        return (f"PasswordRecord(algorithm={self.algorithm!r}, "
                f"iterations={self.iterations})")


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    """PBKDF2-HMAC-SHA256 over the UTF-8 password."""
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=KEY_BYTES)


def hash_password(password: str, *,
                  iterations: int = DEFAULT_ITERATIONS,
                  salt: bytes | None = None) -> PasswordRecord:
    """Create a record for ``password``.

    ``salt`` is injectable for tests only. Production callers must let it be
    generated: reusing a salt across records would let one crack cover many.
    """
    if not password:
        raise PasswordError("password must not be empty")
    if salt is None:
        salt = os.urandom(SALT_BYTES)
    return PasswordRecord(salt=salt,
                          digest=_derive(password, salt, iterations),
                          iterations=iterations)


def verify(password: str, record: PasswordRecord) -> bool:
    """Constant-time check of ``password`` against ``record``.

    Uses :func:`hmac.compare_digest`, which does not short-circuit on the first
    differing byte. A plain ``==`` would leak how much of the digest matched,
    which is enough to recover it byte by byte given enough attempts.
    """
    candidate = _derive(password, record.salt, record.iterations)
    return hmac.compare_digest(candidate, record.digest)


# -- storage ---------------------------------------------------------------

class NoPasswordSet(PasswordError):
    """Raised when a privileged action is attempted with no password record.

    Distinct from a wrong password: "not configured" must not be accepted, and
    must not be confused with "you typed it wrong" in a UI.
    """


def save(record: PasswordRecord, path: Path) -> None:
    """Write a record atomically, with owner-only permissions where possible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.encode().encode("utf-8")
    # Same-directory temp file so os.replace is atomic. Mirrors config.save.
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def load(path: Path) -> PasswordRecord:
    """Read a record, raising :class:`NoPasswordSet` when the file is absent."""
    if not path.exists():
        raise NoPasswordSet(f"no password record at {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise NoPasswordSet(f"password record at {path} is empty")
    return PasswordRecord.decode(text)


def check(path: Path, password: str) -> bool:
    """Load and verify in one step. Raises :class:`NoPasswordSet` if unset."""
    return verify(password, load(path))


def generate(length: int = 20) -> str:
    """A random password, for a fresh install to show the administrator once.

    Uses :mod:`secrets`, not :mod:`random`: the latter is seeded predictably.
    """
    if length < 12:
        raise PasswordError("generated passwords must be at least 12 characters")
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
