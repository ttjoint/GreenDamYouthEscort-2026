"""The parent console's data layer: what the UI is allowed to see.

Pure functions that turn the system's state into JSON-ready dictionaries. No
GUI toolkit, no sockets, no file writes - which is what makes every screen of
the console testable without opening a window.

The rule this module exists to enforce
--------------------------------------
The console is the one place where "what is the filter doing?" gets answered, so
it is also the place where a careless implementation would dump the thing it is
supposed to be filtering. Two hard constraints, both inherited from
``eventlog.py``:

**No content, ever.** The log view shows a record's ``reason`` token, its stage
and its digest. It never shows the matched text, because the log never contains
it - the console cannot leak what was never stored. The digest is shown as a
short string so a parent can see "the same thing was blocked twice" without
being told what it was.

**No word lists.** The word-pack view reports counts, categories and licences.
Listing the terms would make the console a copy of the filter, and the console is
a page a child can open.

A third rule is this module's own:

**"Not assessed" is shown as not assessed.** A filter with no word packs, no
image model and no proxy running has not looked at anything. The status view
says so in words rather than rendering a green tick, because a green tick over
an empty filter is the exact failure this project keeps refusing to ship.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "status_view",
    "log_view",
    "packs_view",
    "browsers_view",
    "image_view",
    "overview",
    "MAX_LOG_ROWS",
]

#: How many log rows one page of the log view returns. The console is for
#: reading, not for exporting an archive; ``green-dam log`` does that.
MAX_LOG_ROWS = 200


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_view(filter_) -> dict:
    """Switches and counts. Mirrors ``Filter.status()`` with the honesty pass.

    ``Filter.status()`` reports raw facts. This adds the interpretation a parent
    needs, and specifically the difference between "off" and "nothing to check":

    * ``assessing`` is False when no word packs are loaded *and* no image model is
      present. In that state the filter cannot block on content, and saying
      "protection is on" would be a lie.
    * ``gaps`` names each reason the filter is not fully covering, so the console
      has something specific to tell the parent instead of a generic warning.
    """
    raw = filter_.status() if filter_ is not None else {}
    lists = raw.get("lists") or {}
    list_counts = {name: int(info.get("count", 0)) for name, info in lists.items()}

    packs = getattr(filter_, "packs", None)
    pack_count = len(packs.usable) if packs is not None else 0
    pack_terms = packs.total_terms if packs is not None else 0

    has_text = bool(raw.get("text_sources_loaded") or raw.get("wordlists_loaded")
                    or pack_terms)
    has_image = bool(raw.get("image_filter_enabled")
                     and raw.get("image_filter_level") is not None)

    gaps: list[str] = []
    if raw.get("filtering_disabled"):
        gaps.append("filtering_disabled")
    if raw.get("keyword_filter_disabled"):
        gaps.append("keyword_filter_disabled")
    if not has_text:
        gaps.append("no_word_lists")
    if not has_image:
        gaps.append("no_image_backend")
    if sum(list_counts.values()) == 0:
        gaps.append("no_network_rules")

    return {
        "when": _now(),
        "filtering_disabled": bool(raw.get("filtering_disabled")),
        "keyword_filter_disabled": bool(raw.get("keyword_filter_disabled")),
        "image_filter_enabled": bool(raw.get("image_filter_enabled")),
        "image_filter_level": raw.get("image_filter_level"),
        "assessing": has_text or has_image,
        "coverage": {
            "text": has_text,
            "image": has_image,
            "network": sum(list_counts.values()) > 0,
        },
        "packs": {"usable": pack_count, "terms": pack_terms},
        "lists": list_counts,
        "log_records": int(raw.get("log_records", 0)),
        "gaps": gaps,
    }


def log_view(path: Path | None, *, limit: int = MAX_LOG_ROWS,
             reader=None) -> dict:
    """Recent decisions, newest first.

    Reads the JSON-lines log through the engine's own reader so the console
    cannot drift from the format. A missing log is an empty list, not an error:
    a fresh install has not decided anything yet.

    Every row is passed through :func:`_safe_row`, which drops any key not on the
    allowlist. That is deliberate belt-and-braces: if a future ``LogRecord``
    gains a field carrying content, this view will not start displaying it
    because someone forgot to update the console.
    """
    if path is None or not Path(path).is_file():
        return {"when": _now(), "rows": [], "total": 0, "truncated": False,
                "path_known": path is not None}

    # ``reader`` is injected rather than imported: the console must not import
    # the engine itself, or a broken deployment produces a traceback per screen
    # instead of one at startup. ``host.engine.read_event_log`` is the real one.
    if reader is None:
        from .. import engine                        # noqa: PLC0415 - lazy
        reader = engine.read_event_log

    rows = [_safe_row(row) for row in reader(Path(path))]
    total = len(rows)
    tail = rows[-limit:] if total > limit else rows
    tail.reverse()
    return {
        "when": _now(),
        "rows": tail,
        "total": total,
        "truncated": total > limit,
        "path_known": True,
    }


#: The only keys the log view will pass through. Anything else is dropped, so a
#: future field cannot accidentally reach the screen.
_SAFE_KEYS = ("seq", "when", "event", "source", "stage", "reason",
              "direction", "span_length", "content_digest")


def _safe_row(row: dict) -> dict:
    return {key: row[key] for key in _SAFE_KEYS if key in row}


def packs_view(index) -> dict:
    """Word packs: id, name, category, licence, count, state. Never terms.

    A disabled pack stays in the list. "Why is this blocked" needs an answer, and
    so does "why is this *not* blocked" - hiding disabled packs would make the
    second question unanswerable.
    """
    packs = []
    for pack in index.packs:
        packs.append({
            "id": pack.id,
            "name": pack.name,
            "category": pack.category,
            "licence": pack.licence,
            "source": pack.source,
            "terms": len(pack.terms),
            "enabled": bool(pack.enabled),
            "usable": bool(pack.usable),
            "error": pack.error or "",
        })
    return {
        "when": _now(),
        "packs": packs,
        "usable": len(index.usable),
        "total_terms": index.total_terms,
    }


def browsers_view(browsers, *, extension_installed: bool | None = None) -> dict:
    """Which browsers exist, and whether the extension can be installed there.

    ``uncovered`` is the point of this view. A browser the product cannot
    configure is not silently omitted: the parent is told which browser the
    filter does not cover, because the alternative is a false sense of coverage.
    """
    rows = []
    for browser in browsers:
        rows.append({
            "name": browser.display_name,
            "family": str(browser.family),
            "version": getattr(browser, "version", "") or "",
            "configurable": bool(browser.configurable),
            "policy_target": getattr(browser, "policy_target", "") or "",
        })
    uncovered = [row["name"] for row in rows if not row["configurable"]]
    return {
        "when": _now(),
        "browsers": rows,
        "covered": len(rows) - len(uncovered),
        "uncovered": uncovered,
        "extension_installed": extension_installed,
    }


def image_view(backend_name: str | None, *, threshold: float = 0.5) -> dict:
    """Whether an image model is present, and what that means.

    ``assessing`` False is the default deployment: no model, so no image is
    judged. The view says that plainly rather than showing an empty score, and
    ``explanation`` spells out the consequence - images are not checked, which is
    different from images being fine.
    """
    assessing = bool(backend_name)
    return {
        "when": _now(),
        "backend": backend_name or "",
        "assessing": assessing,
        "threshold": threshold,
        "explanation": (
            f"图像判定由 {backend_name} 提供，阈值 {threshold}。"
            if assessing else
            "未安装图像模型，因此不会检查任何图片。这不等于图片是安全的。"),
    }


def overview(*, filter_=None, log_path: Path | None = None,
             packs=None, browsers=None, image_backend: str | None = None,
             extension_installed: bool | None = None) -> dict:
    """Every view in one payload, for the console's initial load.

    One request rather than five, because the console opens on a single screen
    and five round trips would show it assembling itself.
    """
    payload = {
        "when": _now(),
        "status": status_view(filter_),
        "log": log_view(log_path),
        "image": image_view(image_backend),
    }
    if packs is not None:
        payload["packs"] = packs_view(packs)
    if browsers is not None:
        payload["browsers"] = browsers_view(
            browsers, extension_installed=extension_installed)
    return payload
