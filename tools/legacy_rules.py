"""
Legacy data importer — stage 2: filter rule lists.

The five ``*fil.dat`` / ``adwapp.dat`` files use a compact rule grammar.
A line is one of::

    [literal]                         plain domain / substring
    [+prefix]                         matches "prefix" as a word prefix
    [=suffix]                         matches "suffix" as a suffix
    [+,=][a,b,c]                      several tokens, both prefix and suffix
    [,x][foo][y,ual,uality]           concatenated fragments (composed match)

Two delimiter flavours appear in the shipped data and are equivalent:

    [...]   modern rule groups ("adwfil.dat", "iawfil.dat", ...)
    {...}   the same grammar inside braces ("adwfil.dat" alternates)

Observed grammar (empirically, from the shipped lists):

* ``[`` ... ``]`` delimit a token group.
* Inside a group, ``+`` means "prefix match", ``=`` means "suffix match",
  a bare token means "exact / substring match".
* A leading ``,`` separates alternatives.
* Multiple groups on one line are concatenated in order, so
  ``[,homo,bi][sex][y,ual,uality]`` expands to
  ``homosex`` / ``homosexual`` / ``homosexuality`` / ``bisex`` / ...

The parser below expands every line into the concrete match terms and
classifies each by match mode. Nothing is guessed: lines that do not fit
the grammar are preserved verbatim in ``raw`` so no information is lost.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

GROUP_RE = re.compile(r"\[([^\]]*)\]")


def _split_alternatives(body: str) -> list[str]:
    """Split the inside of a bracket group into alternative tokens."""
    body = body.strip()
    if not body:
        return [""]
    # leading / trailing commas produce empty alternatives in the source
    return body.split(",")


def _expand_group(body: str) -> list[tuple[str, str]]:
    """
    Expand one bracket group into ``(alternative, match_mode)`` pairs.

    Modifiers are not simple flags: ``+`` and ``=`` select a match
    direction for the whole group, and when both appear the group fans
    out into *both* directions. So this returns a list, not one mode.
    """
    body = body.strip()
    flags = ""
    rest = body
    # the modifier block is its own bracket group: "[+,=][a,b]"
    if body and all(c in "+=," for c in body):
        return []
    while rest and rest[0] in "+=":
        flags += rest[0]
        rest = rest[1:]
    if rest.startswith(","):
        rest = rest[1:]
    alts = _split_alternatives(rest)

    directions: list[str]
    if not flags:
        directions = ["literal"]
    else:
        directions = []
        if "+" in flags:
            directions.append("prefix")
        if "=" in flags:
            directions.append("suffix")

    return [(a, d) for d in directions for a in alts]


def parse_line(line: str) -> dict:
    """
    Parse one rule line into a structured record.

    Each produced term keeps its own match mode, because a single line can
    mix directions, e.g. ``[+,=][coed,sex]`` means "prefix OR suffix".
    """
    line = line.strip()
    if not line:
        return {}
    line = line.replace("{", "[").replace("}", "]")
    groups = GROUP_RE.findall(line)
    if not groups:
        return {"raw": line}

    # A leading modifier-only group such as "[+,=]" applies to the group that
    # immediately follows it. The same holds for modifier groups that appear
    # in the middle of a concatenation, e.g. "[commit][,+][suicide]", so
    # modifiers are peeled off the whole group list first and attached to the
    # next productive group.
    modifiers: dict[int, str] = {}
    productive: list[str] = []
    carry = ""
    for body in groups:
        if body and all(c in "+=," for c in body):
            # "+," and "+,=" both mean prefix and/or suffix; "[,]" adds no
            # direction and simply carries over.
            carry += body
            continue
        modifiers[len(productive)] = carry
        carry = ""
        productive.append(body)
    groups = productive
    if not groups:
        return {"raw": line}

    def _dirs_from(flags: str) -> list[str]:
        out = []
        if "+" in flags:
            out.append("prefix")
        if "=" in flags:
            out.append("suffix")
        return out

    # Expand groups left-to-right; each partial term carries its mode.
    partials: list[tuple[str, str]] = [("", "literal")]
    for gi, body in enumerate(groups):
        dirs = _dirs_from(modifiers.get(gi, "")) or None
        nxt: list[tuple[str, str]] = []
        for prefix, pmode in partials:
            for a, gmode in _expand_group(body):
                if dirs is not None:
                    for d in dirs:
                        nxt.append((prefix + a, d if pmode == "literal" else pmode))
                else:
                    merged = pmode if pmode != "literal" else gmode
                    nxt.append((prefix + a, merged))
        partials = nxt

    terms = [
        {"text": t, "match": m}
        for t, m in partials
        if t
    ]
    if not terms:
        return {"raw": line}
    modes = {t["match"] for t in terms}
    return {
        "terms": terms,
        "mode": modes.pop() if len(modes) == 1 else "mixed",
        "groups": groups,
        "raw": line,
    }


def parse_file(path: Path) -> dict:
    """Parse a decoded rule file into a list of rule records."""
    text = path.read_text(encoding="utf-8")
    rules = []
    for ln in text.splitlines():
        rec = parse_line(ln)
        if rec:
            rules.append(rec)
    return {"source": path.name, "count": len(rules), "rules": rules}


# wfile.dat is the authoritative index of the five categories.
CATEGORY_INDEX = "wfile.dat"


def read_category_index(path: Path) -> list[dict]:
    """Parse wfile.dat: '<enabled> <name> <file>' per line."""
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split()
        if len(parts) >= 3:
            out.append(
                {
                    "enabled": parts[0] == "1",
                    "name": parts[1],
                    "file": parts[2],
                }
            )
    return out


def dump(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
