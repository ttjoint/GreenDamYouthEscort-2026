"""Command-line entry point: a dispatcher over three subject groups.

Scope, stated up front: this is a **diagnostic** CLI, not the product UI. It
answers questions about a configured filter so the library can be exercised
without writing throwaway scripts. M9 (tray, scheduler, log viewer) is separate
work.

The commands live in three modules by subject, not by size:

===================  ==========================================
module               commands
===================  ==========================================
``cli_filter``       check, screen, replay, ab - judging things
``cli_admin``        status, policy, validate, log, save-defaults
``cli_lifecycle``    password, uninstall - privileged operations
===================  ==========================================

This file only builds the parser and dispatches. Keeping it that way means the
argparse definitions are in one place while the bodies are where their subject
lives, and it keeps the file well under the project's size limit as commands are
added.

Two rules the whole CLI follows, both inherited from the library:

* it never prints list entries or wordlist terms, only counts and switches
* it never prints matched content; the event log stores digests, and so does
  anything this CLI reports

Privileged commands add a third: they never echo a password, and they prompt for
it rather than taking it as an argument.
"""
from __future__ import annotations

import argparse

from .cli_admin import (
    cmd_log,
    cmd_policy,
    cmd_save_defaults,
    cmd_status,
    cmd_validate,
)
from .cli_filter import cmd_ab, cmd_check, cmd_replay, cmd_screen
from .cli_lifecycle import cmd_password, cmd_uninstall


def _add_filter_commands(sub) -> None:
    p = sub.add_parser("check", help="run one request/text through the filter")
    p.add_argument("--config", default=".", help="config directory")
    p.add_argument("--direction", choices=("outbound", "inbound"),
                   default="outbound")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--process")
    p.add_argument("--body", help="request body (outbound URL extraction)")
    p.add_argument("--process-name", dest="process_name",
                   help="program name to check against the watched list")
    p.add_argument("--text", help="text to judge against wordlists")
    p.add_argument("--text-file", help="read text from a file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("screen", help="run the ported image checks on a "
                                      "synthetic frame (no file is read)")
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--red", type=int,
                   help="fill the R plane with one value (0..255); omit to "
                        "skip the high-tone check")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("replay", help="replay a JSON flow file through the "
                                      "four-stage planner (no network)")
    p.add_argument("path", help="JSON array of flow objects")
    p.add_argument("--config", default=".", help="config directory")
    p.add_argument("--ca-installed", action="store_true",
                   help="assume the local CA is trusted, enabling stage 3")
    p.add_argument("--no-images", action="store_true",
                   help="treat image judging as unsupported")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("ab", help="compare the two text backends over a corpus "
                                  "(one text per line)")
    p.add_argument("corpus", help="UTF-8 text file, one sample per line")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ab)


def _add_admin_commands(sub) -> None:
    p = sub.add_parser("status", help="show switches and list counts")
    p.add_argument("--config", default=".", help="config directory")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("policy", help="show the decoded policy array")
    p.add_argument("--config", default=".", help="config directory or file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_policy)

    p = sub.add_parser("validate", help="validate a policy blob")
    p.add_argument("path")
    p.add_argument("--strict", action="store_true",
                   help="also enforce the value domain")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("log", help="read back a JSON-lines event log")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("save-defaults", help="write shipped defaults to a file")
    p.add_argument("path")
    p.set_defaults(func=cmd_save_defaults)


def _add_lifecycle_commands(sub) -> None:
    p = sub.add_parser("password", help="manage the parent password")
    p.add_argument("action", choices=("set", "verify", "show", "generate"))
    p.add_argument("--path", default="parent.pw",
                   help="password record path")
    p.add_argument("--password", help="password (omit to be prompted)")
    p.add_argument("--length", type=int, default=20,
                   help="length for 'generate'")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_password)

    p = sub.add_parser("uninstall", help="inspect or plan an uninstall from "
                                         "the install journal")
    p.add_argument("action", choices=("plan", "verify", "rollback"))
    p.add_argument("--manifest", default="install-journal.json")
    p.add_argument("--strict", action="store_true",
                   help="fail if any change cannot be undone")
    p.add_argument("--password", help="parent password for rollback")
    p.add_argument("--password-file", default="parent.pw")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_uninstall)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="green-dam",
        description="Diagnostics for the Green Dam 2026 reimplementation. "
                    "Prints counts and switches only, never content.")
    sub = ap.add_subparsers(dest="command", required=True)
    _add_filter_commands(sub)
    _add_admin_commands(sub)
    _add_lifecycle_commands(sub)
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
