"""CLI commands about the product's lifecycle: password and uninstall.

Split out of ``cli.py``. These are the privileged operations, and they share a
rule the other commands do not need: they never echo a secret, and they prompt
for passwords rather than taking them on the command line, because command lines
land in shell history and process listings.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _read_password(args) -> str:
    """Take the password from a prompt, or from --password when scripting."""
    if args.password is not None:
        return args.password
    import getpass

    return getpass.getpass("password: ")


def cmd_password(args) -> int:
    """Set, verify or inspect the parent password."""
    from . import secret

    path = Path(args.path)

    if args.action == "show":
        try:
            record = secret.load(path)
        except secret.NoPasswordSet as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({
                "algorithm": record.algorithm,
                "iterations": record.iterations,
                "needs_rehash": record.needs_rehash,
            }, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"algorithm    : {record.algorithm}")
            print(f"iterations   : {record.iterations}")
            print(f"needs rehash : {record.needs_rehash}")
        return 0

    if args.action == "generate":
        print(secret.generate(args.length))
        return 0

    password = _read_password(args)

    if args.action == "set":
        try:
            record = secret.hash_password(password)
        except secret.PasswordError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        secret.save(record, path)
        print(f"wrote {path} ({record.iterations} iterations)")
        return 0

    if args.action == "verify":
        try:
            ok = secret.check(path, password)
        except secret.NoPasswordSet as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print("ok" if ok else "wrong password")
        return 0 if ok else 1

    print(f"unknown action: {args.action}", file=sys.stderr)
    return 2


def cmd_uninstall(args) -> int:
    """Inspect or plan an uninstall from the install journal.

    Executing the plan is the Windows host's job; this only reads and reports,
    which is why ``plan`` is the default rather than a ``--dry-run`` flag.
    """
    from . import journal, secret

    path = Path(args.manifest)
    try:
        manifest = journal.Manifest.load(path)
    except journal.JournalError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.action == "plan":
        plan = journal.plan_uninstall(manifest, strict=args.strict)
        if args.json:
            print(json.dumps(plan.summary(), ensure_ascii=False, indent=2,
                             sort_keys=True))
        else:
            print(plan.render())
        return 0

    if args.action == "verify":
        problems = journal.verify_inverse(manifest)
        if problems:
            for problem in problems:
                print(f"problem: {problem}", file=sys.stderr)
            return 1
        print(f"ok: {len(manifest)} change(s), all reversible")
        return 0

    if args.action == "rollback":
        # Destructive actions require the parent password, per architecture §M7.
        try:
            if not secret.check(Path(args.password_file), _read_password(args)):
                print("wrong password", file=sys.stderr)
                return 1
        except secret.NoPasswordSet as exc:
            print(f"{exc}; refusing to mark as rolled back", file=sys.stderr)
            return 2
        journal.mark_rolled_back(manifest, path)
        print(f"marked {path} as rolled back; the host performs the steps")
        return 0

    print(f"unknown action: {args.action}", file=sys.stderr)
    return 2
