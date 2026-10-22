"""CLI commands about policy, configuration and inspection.

Split out of ``cli.py`` when that file passed 450 lines. The split is by
subject, not by size: these commands all read or write the product's
configuration and state, and none of them touch text analysis or the journal.

Each command takes an ``argparse.Namespace`` and returns an exit code, which is
the convention the dispatcher in ``cli.py`` expects.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config, sysdat
from .eventlog import EventLog, read_jsonl
from .filter import Filter


def cmd_status(args) -> int:
    """Show switches and list counts."""
    f = Filter.from_config_dir(Path(args.config))
    payload = f.status()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(f"config dir      : {args.config}")
    print(f"filtering off   : {payload['filtering_disabled']}")
    print(f"keyword off     : {payload['keyword_filter_disabled']}")
    level = payload["image_filter_level"]
    print(f"image filter    : {payload['image_filter_enabled']} (level {level})")
    print(f"wordlists       : {'loaded' if payload['wordlists_loaded'] else 'not loaded'}")
    print("lists:")
    for name, info in payload["lists"].items():
        cap = info["capacity"] if info["capacity"] is not None else "unbounded"
        print(f"  {name}  {info['count']:>5} / {cap:<9}  {info['role']}")
    return 0


def _locate_policy(path: Path) -> tuple[sysdat.PolicyArray, object]:
    """Find a policy file under a directory or use it directly."""
    for name in ("xconfigs.dat", "sys.dat"):
        candidate = path / name if path.is_dir() else path
        if candidate.exists():
            return config.load(candidate), candidate
    return config.default_for_fresh_install(), "(shipped defaults)"


def cmd_policy(args) -> int:
    """Show the decoded policy array."""
    array, source = _locate_policy(Path(args.config))

    if args.json:
        print(json.dumps(
            {
                "source": str(source),
                "nonzero": [
                    {"policy": no, "name": sysdat.SLOT_NAMES[no - 1], "value": v}
                    for no, v in array.nonzero()
                    if no <= sysdat.NAMED_SLOTS
                ],
            },
            ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(f"source: {source}")
    print(f"{'policy':>6}  {'value':>5}  name")
    for no, value in array.nonzero():
        if no > sysdat.NAMED_SLOTS:
            print(f"{no:>6}  {value:>5}  (reserved)")
        else:
            print(f"{no:>6}  {value:>5}  {sysdat.SLOT_NAMES[no - 1]}")
    return 0


def cmd_validate(args) -> int:
    """Validate a policy blob."""
    path = Path(args.path)
    try:
        array = config.load(path, strict=args.strict)
    except (config.ConfigError, ValueError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 2
    print(f"ok: {sysdat.SLOT_COUNT} slots, {len(array.nonzero())} non-zero")
    if args.strict:
        print("strict value-domain check passed")
    return 0


def cmd_log(args) -> int:
    """Read back a JSON-lines event log."""
    path = Path(args.path)
    if not path.exists():
        print(f"no such log: {path}", file=sys.stderr)
        return 2
    rows = list(read_jsonl(path))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{'seq':>5}  {'event':<9}  {'source':<8}  {'stage':<8}  reason")
    for row in rows:
        print(f"{row.get('seq', 0):>5}  {row.get('event', ''):<9}  "
              f"{row.get('source', ''):<8}  {row.get('stage', ''):<8}  "
              f"{row.get('reason', '')}")
    return 0


def cmd_save_defaults(args) -> int:
    """Write the shipped defaults to a file."""
    path = Path(args.path)
    array = config.default_for_fresh_install()
    config.save(array, path, source="cli:save-defaults")
    print(f"wrote {path} ({sysdat.BLOB_SIZE} bytes) + sidecar")
    return 0
