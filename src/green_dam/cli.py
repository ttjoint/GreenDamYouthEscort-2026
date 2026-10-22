"""Command-line entry point.

Scope, stated up front: this is a **diagnostic** CLI, not the product UI. It
answers questions about a configured filter - what the policy array says, what
the lists hold, what a given request or text would do - so the library can be
exercised without writing throwaway scripts. M9 (tray, scheduler, log viewer)
is separate work.

Two rules the CLI follows, both inherited from the library:

* it never prints list entries or wordlist terms, only counts and switches
* it never prints matched content; the event log stores digests, and so does
  anything this CLI reports
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, sysdat
from .eventlog import EventLog, read_jsonl
from .filter import Filter
from .intercept import Request


def _cmd_status(args: argparse.Namespace) -> int:
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


def _cmd_policy(args: argparse.Namespace) -> int:
    path = Path(args.config)
    for name in ("xconfigs.dat", "sys.dat"):
        candidate = path / name if path.is_dir() else path
        if candidate.exists():
            array = config.load(candidate)
            source = candidate
            break
    else:
        array = config.default_for_fresh_install()
        source = "(shipped defaults)"

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


def _cmd_check(args: argparse.Namespace) -> int:
    log = EventLog()
    f = Filter.from_config_dir(Path(args.config), log=log)

    request = None
    if args.host or args.process or args.port:
        request = Request(
            direction=args.direction,
            process=args.process,
            port=args.port,
            host=args.host,
            body=args.body,
        )

    text = None
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    elif args.text:
        text = args.text

    assessment = f.check(request=request, text=text,
                         process=args.process_name)

    if args.json:
        print(json.dumps({
            "action": assessment.action,
            "source": assessment.source,
            "reason": assessment.reason,
            "events": [json.loads(r.to_json()) for r in log.records],
        }, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"action : {assessment.action}")
        print(f"source : {assessment.source}")
        print(f"reason : {assessment.reason}")
        for rec in log.records:
            print(f"event  : {rec.event.value} {rec.stage} {rec.direction}".rstrip())
    return 0


def _cmd_screen(args: argparse.Namespace) -> int:
    """Screen a synthetic frame. No image file is read: dimensions only."""
    from .imaging import screen

    red = None
    if args.red is not None:
        if not 0 <= args.red <= 255:
            print("--red must be 0..255", file=sys.stderr)
            return 2
        red = bytes([args.red]) * (args.width * args.height)

    result = screen(width=args.width, height=args.height, red_channel=red)
    if args.json:
        print(json.dumps({
            "suspect": result.suspect,
            "detail": result.detail,
            "fully_screened": result.fully_screened,
            "fired": [c.value for c in result.fired],
            "checks_run": [c.value for c in result.checks_run],
            "metrics": result.metrics,
        }, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"suspect        : {result.suspect}")
        print(f"detail         : {result.detail}")
        print(f"fully screened : {result.fully_screened}")
        print(f"fired          : {[c.value for c in result.fired] or '-'}")
        for key, value in sorted(result.metrics.items()):
            print(f"{key:<15}: {value:.4f}")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    """Replay a flow file through the planner and the filter."""
    from .replay import FlowFileError, replay_file
    from .transport import describe

    log = EventLog()
    f = Filter.from_config_dir(Path(args.config), log=log)
    try:
        results = replay_file(Path(args.path), filter_obj=f,
                              ca_installed=args.ca_installed,
                              image_supported=not args.no_images)
    except FlowFileError as exc:
        print(f"invalid flow file: {exc}", file=sys.stderr)
        return 2

    rows = [{
        "stage": int(r.plan.stage),
        "limits": list(r.plan.limits),
        "action": r.assessment.action if r.assessment else None,
        "source": r.assessment.source if r.assessment else None,
        "reason": r.assessment.reason if r.assessment else None,
    } for r in results]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{'stage':>5}  {'action':<8}  {'source':<8}  limits")
        for row in rows:
            print(f"{row['stage']:>5}  {str(row['action']):<8}  "
                  f"{str(row['source']):<8}  {','.join(row['limits']) or '-'}")
        gated = sum(1 for rec in log.records if rec.event.value == "gated")
        print(f"\n{len(rows)} flows, {gated} gated")
    return 0


def _cmd_ab(args: argparse.Namespace) -> int:
    """Run the two text backends over a corpus and report their agreement."""
    from .backends import load_ab_pair, run_ab

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"no such corpus: {corpus_path}", file=sys.stderr)
        return 2
    lines = [line for line in
             corpus_path.read_text(encoding="utf-8", errors="replace").splitlines()
             if line.strip()]
    if not lines:
        print("corpus is empty", file=sys.stderr)
        return 2

    left, right = load_ab_pair()
    report = run_ab(left, right, lines)
    summary = report.summary()

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"backends       : {left.name} vs {right.name}")
        print(f"texts          : {summary['texts']}")
        print(f"mean agreement : {summary['mean_agreement']}")
        print(f"identical      : {summary['identical']}")
        print(f"disagreed      : {summary['disagreed']}")
        print(f"spans agreed   : {summary['spans_agreed']}")
        print(f"spans only L/R : {summary['spans_only_left']} / "
              f"{summary['spans_only_right']}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
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


def _cmd_log(args: argparse.Namespace) -> int:
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


def _cmd_save_defaults(args: argparse.Namespace) -> int:
    path = Path(args.path)
    array = config.default_for_fresh_install()
    config.save(array, path, source="cli:save-defaults")
    print(f"wrote {path} ({sysdat.BLOB_SIZE} bytes) + sidecar")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="green-dam",
        description="Diagnostics for the Green Dam 2026 reimplementation. "
                    "Prints counts and switches only, never content.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="show switches and list counts")
    p.add_argument("--config", default=".", help="config directory")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("policy", help="show the decoded policy array")
    p.add_argument("--config", default=".", help="config directory or file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_policy)

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
    p.set_defaults(func=_cmd_check)

    p = sub.add_parser("screen", help="run the ported image checks on a "
                                      "synthetic frame (no file is read)")
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--red", type=int,
                   help="fill the R plane with one value (0..255); omit to "
                        "skip the high-tone check")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_screen)

    p = sub.add_parser("replay", help="replay a JSON flow file through the "
                                      "four-stage planner (no network)")
    p.add_argument("path", help="JSON array of flow objects")
    p.add_argument("--config", default=".", help="config directory")
    p.add_argument("--ca-installed", action="store_true",
                   help="assume the local CA is trusted, enabling stage 3")
    p.add_argument("--no-images", action="store_true",
                   help="treat image judging as unsupported")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_replay)

    p = sub.add_parser("ab", help="compare the two text backends over a corpus "
                                  "(one text per line)")
    p.add_argument("corpus", help="UTF-8 text file, one sample per line")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_ab)

    p = sub.add_parser("validate", help="validate a policy blob")
    p.add_argument("path")
    p.add_argument("--strict", action="store_true",
                   help="also enforce the value domain")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("log", help="read back a JSON-lines event log")
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_log)

    p = sub.add_parser("save-defaults", help="write shipped defaults to a file")
    p.add_argument("path")
    p.set_defaults(func=_cmd_save_defaults)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
