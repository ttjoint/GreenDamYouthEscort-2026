"""CLI commands about the filter's decisions: check, screen, replay, A/B.

Split out of ``cli.py``. These commands all *judge* something - a request, a
frame, a flow file, a corpus - as opposed to configuring the product or managing
its lifecycle.

Every command here follows the project's redaction rule: it reports counts,
stages and reasons, never the text or host that produced them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .eventlog import EventLog
from .filter import Filter
from .intercept import Request


def cmd_check(args) -> int:
    """Run one request and/or text through the filter."""
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


def cmd_screen(args) -> int:
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


def cmd_texture(args) -> int:
    """Run the CHECKEX13x texture path on a synthetic frame.

    Takes a stddev directly rather than an image: the arithmetic is what this
    exercises, and generating a frame whose centre window has a chosen stddev
    would add machinery without testing anything new.
    """
    from . import imaging_checkex as cx

    if args.width and args.height:
        ok = cx.checkex_aspect_ok(args.width, args.height)
    else:
        ok = None

    grade = cx.texture_grade(args.stddev)
    payload = {
        "stddev": args.stddev,
        "grade": grade,
        "weight": cx.texture_grade_weight(grade),
        "passes_texture": cx.texture_ok(args.stddev),
        "above_threshold": args.stddev >= cx.CENTRE_STDDEV_THRESHOLD,
    }
    if ok is not None:
        payload["aspect_ok"] = ok

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(f"stddev         : {args.stddev}")
    print(f"grade          : {grade} (0..7)")
    print(f"weight         : {cx.texture_grade_weight(grade)}")
    print(f"above 1.4      : {payload['above_threshold']}")
    print(f"passes texture : {payload['passes_texture']}")
    if ok is not None:
        print(f"aspect ok      : {ok} (integer gate, max {cx.CHECKEX_MAX_ASPECT})")
    return 0


def cmd_skin(args) -> int:
    """Classify one BGR triple, or a flat frame, with CHECKEX8x's skin rules.

    Prints the verdict and the reason, never the pixel values.
    """
    from . import imaging_skin_hsv as hsv

    if args.r is not None:
        if None in (args.r, args.g, args.b):
            print("--r/--g/--b must be given together", file=sys.stderr)
            return 2
        rgb = (args.r, args.g, args.b)
        skin = hsv.is_skin_pixel(*rgb)
        hue, sat, val = hsv.rgb_to_hsv(*rgb)
        payload = {
            "skin": skin,
            "hue": round(hue, 2),
            "saturation": round(sat, 4),
            "value": round(val, 4),
            "hue_grade": hsv.hue_grade(hue),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2,
                             sort_keys=True))
        else:
            print(f"skin       : {skin}")
            print(f"hue        : {hue:.2f}")
            print(f"saturation : {sat:.4f}")
            print(f"value      : {val:.4f}")
            print(f"hue grade  : {hsv.hue_grade(hue)}")
        return 0

    if args.flat is not None:
        r, g, b = (int(x) for x in args.flat.split(","))
        width = args.width or 8
        height = args.height or 8
        buffer = bytes([b, g, r]) * (width * height)
        stats = hsv.skin_pass(buffer, width, height)
        if args.json:
            print(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2,
                             sort_keys=True))
        else:
            print(f"pixels     : {stats.pixels}")
            print(f"skin       : {stats.skin}")
            print(f"ratio      : {stats.ratio:.4f}")
            print(f"rejected   : order={stats.rejected_by_order} "
                  f"sat={stats.rejected_by_saturation} "
                  f"hue={stats.rejected_by_hue}")
        return 0

    print("give either --r/--g/--b or --flat", file=sys.stderr)
    return 2


def cmd_image(args) -> int:
    """Run the image backend chain on a flat synthetic frame.

    Takes a colour and dimensions rather than a file: the interface is what this
    exercises, and reading a real image would need a decoder the engine does not
    have.
    """
    from . import imagepath as ip

    if args.score is not None:
        # A host backend would supply this; --score simulates one so the
        # threshold path can be exercised without onnxruntime.
        class _Stub:
            name = "stub"

            def classify(self, buffer, width, height):
                return ip.verdict_from_score(args.score, backend=self.name)

        backend = _Stub()
    else:
        backend = ip.ReferenceBackend(run_skin=args.skin)

    chain = ip.ClassifierChain([backend])
    buffer = bytes([args.b, args.g, args.r]) * (args.width * args.height)
    verdict = chain.classify(buffer, args.width, args.height)

    if args.json:
        print(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2,
                         sort_keys=True))
        return 0

    print(f"backend     : {verdict.backend or '-'}")
    print(f"assessed    : {verdict.assessed}")
    print(f"nsfw        : {verdict.nsfw if verdict.nsfw is not None else '-'}")
    print(f"flagged     : {verdict.flagged}")
    print(f"reason      : {verdict.reason}")
    for key, value in sorted(verdict.metrics.items()):
        print(f"  {key:<14}: {value}")
    return 0


def cmd_replay(args) -> int:
    """Replay a flow file through the planner and the filter."""
    from .replay import FlowFileError, replay_file

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


def cmd_chunk(args) -> int:
    """Show the semantic chunk structure of a sentence.

    Prints roles and counts, never the block text: the point is to inspect the
    structure, and the structure is what the log records.
    """
    from .chunkpath import ChunkSource

    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8",
                                              errors="replace")

    source = ChunkSource()
    chunking = source.analyse(text)
    summary = source.summarise(text)

    if args.json:
        print(json.dumps({
            "summary": summary.to_dict(),
            "roles": [c.role.value for c in chunking.chunks],
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(f"shape      : {summary.shape}")
    print(f"pattern    : {summary.pattern or '-'}")
    print(f"blocks     : {summary.block_count} "
          f"(main {summary.main_count}, secondary {summary.secondary_count})")
    print(f"min conf   : {summary.min_confidence:.2f}")
    if args.show_text:
        for chunk in chunking.chunks:
            print(f"  {chunk.role.value:<3} {chunk.text!r}")
    else:
        print("  (use --show-text to print block text)")
    return 0


def cmd_similar(args) -> int:
    """Score how similar two sentences are, or explain why it cannot be done.

    Prints the score and the terms that produced it, never the sentences: the
    numbers are what a caller would log.
    """
    from .simpath import SimilaritySource

    left = args.left
    right = args.right
    if args.file:
        lines = [ln for ln in
                 Path(args.file).read_text(encoding="utf-8",
                                           errors="replace").splitlines()
                 if ln.strip()]
        if len(lines) < 2:
            print("file needs two non-empty lines", file=sys.stderr)
            return 2
        left, right = lines[0], lines[1]

    if not left or not right:
        print("need two sentences", file=sys.stderr)
        return 2

    source = SimilaritySource(threshold=args.threshold)
    result = source.compare(left, right)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2,
                         sort_keys=True))
        return 0

    print(f"scored     : {result.scored}")
    if not result.scored:
        print(f"reason     : {result.reason}")
        print("  (a pair the chunker cannot structure is refused, not scored)")
        return 0
    print(f"total      : {result.total:.4f}")
    print(f"threshold  : {result.threshold}")
    print(f"similar    : {result.similar}")
    print(f"degraded   : {len(result.degraded)} substitute term(s)")
    for key, value in sorted(result.metrics.items()):
        print(f"  {key:<8}: {value:.4f}")
    return 0


def cmd_ab(args) -> int:
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
