"""The browser extension's filtering rules, as data, before any DOM exists.

The extension itself is JavaScript and cannot be unit-tested here. What *can* be
tested - and what carries the risk - is the decision layer: given a block of text
or an image URL, should it be hidden? That decision is specified here, in Python,
with tests, and the JavaScript implements exactly this. The two are kept in step
by :func:`extension_config`, which emits the JSON the script loads, so a change
to the rules is a change to a tested artefact rather than to a string in a file
nobody checks.

Why the extension exists at all is in ``docs/architecture.md`` §M2: a root CA
decrypts every TLS connection on the machine, which is a larger scope than "a
parent protecting their own child" supports. The extension receives the
*decrypted DOM* instead - no certificate, no key, no change to the system trust
store - and, more importantly, it judges **individual elements** rather than
whole pages. A forum thread with one bad post should lose that post, not the
site.

Three rules this module encodes, each of which is a decision:

**Hide, do not navigate.** Blocking an element leaves the page usable. Replacing
the whole document means a forum with one bad post becomes unreadable, and a
parent will turn the filter off.

**Never hide the page's own chrome.** Navigation, comments boxes and the site's
own controls stay. Hiding them makes the site look broken, which produces a
support call rather than a protected child.

**Say what happened, in place.** A hidden element is replaced by a short notice,
not by nothing. An element that vanishes silently looks like a rendering bug, and
the child learns that reloading fixes it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "TEXT_TAGS",
    "SKIP_TAGS",
    "IMAGE_EXTENSIONS",
    "Rules",
    "default_rules",
    "extension_config",
    "decide_text",
    "decide_image",
    "MANIFEST_VERSION",
    "EXTENSION_NAME",
    "manifest",
    "build_extension",
]

#: Element names whose text content is page content worth judging.
#:
#: Deliberately a list of *containers*, not every tag. Judging every inline
#: element would produce a hide decision for a ``<span>`` inside a paragraph that
#: the parent element already covers, and the DOM would be rewritten for no gain.
TEXT_TAGS: tuple[str, ...] = (
    "p", "li", "td", "th", "dd", "dt", "blockquote", "figcaption",
    "article", "section", "h1", "h2", "h3", "h4", "h5", "h6",
    "div", "span", "a", "pre", "code", "label", "summary",
)

#: Containers that are never judged on their own text.
#:
#: ``nav``, ``header`` and ``footer`` are the page's chrome. ``script`` and
#: ``style`` have no visible text. ``form`` and its controls carry labels, not
#: prose. Hiding any of these makes a site look broken without protecting
#: anything.
SKIP_TAGS: tuple[str, ...] = (
    "nav", "header", "footer", "script", "style", "noscript",
    "form", "button", "input", "select", "textarea",
    "svg", "canvas", "video", "audio", "iframe",
)

#: Image URL suffixes worth sending to the image backend.
#:
#: An image with no extension (a CDN path, a data: URI) is still an image, so this
#: is used to *rank* candidates rather than to exclude them - see
#: :func:`decide_image`.
IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif",
)

#: Manifest version. Chromium requires 3; Firefox reads 3 as of 109 and treats a
#: v2 manifest as deprecated. One number, one file, both browsers.
MANIFEST_VERSION = 3

EXTENSION_NAME = "Green Dam Youth Escort 2026"


@dataclass(frozen=True)
class Rules:
    """The extension's configuration, generated from the engine's own settings.

    ``terms`` come from the word packs (``green_dam.wordpacks``), so the
    extension and the proxy judge text by the same lists. Shipping a second copy
    of the rules inside the extension would let the two drift, and the drift
    would be invisible until a parent noticed one path blocking and the other
    not.

    ``hide_images`` is separate from ``hide_text`` because the two have different
    cost: hiding an element is free, and scoring an image costs 90 ms. A parent
    on a slow machine may reasonably want text only.
    """

    terms: tuple[str, ...] = ()
    #: Case-insensitive substring matching, matching the engine's list semantics.
    case_sensitive: bool = False
    hide_text: bool = True
    hide_images: bool = True
    #: Score at or above this is hidden. Must match ``imagepath``'s default.
    image_threshold: float = 0.5
    #: Longest text node worth judging. A megabyte of text is not a paragraph.
    max_text_length: int = 4000
    #: Where the extension asks the local service to score an image.
    service_url: str = "http://127.0.0.1:17871"

    def to_dict(self) -> dict[str, object]:
        """The JSON the content script loads. Terms are included - this is the
        extension's own copy, and it must not fetch them at runtime because the
        product has no network dependency."""
        return {
            "terms": list(self.terms),
            "caseSensitive": self.case_sensitive,
            "hideText": self.hide_text,
            "hideImages": self.hide_images,
            "imageThreshold": self.image_threshold,
            "maxTextLength": self.max_text_length,
            "serviceUrl": self.service_url,
            "textTags": list(TEXT_TAGS),
            "skipTags": list(SKIP_TAGS),
            "imageExtensions": list(IMAGE_EXTENSIONS),
        }


def default_rules(terms: tuple[str, ...] = ()) -> Rules:
    """Rules with no terms. The default state, and an honest one.

    A filter with no lists hides nothing, and the extension says so in its
    options page rather than appearing to work.
    """
    return Rules(terms=tuple(terms))


def decide_text(text: str, rules: Rules) -> str | None:
    """Return the term that should cause ``text`` to be hidden, or None.

    Mirrors the engine's list-C semantics: substring, case-insensitive by
    default. Returns the *term* rather than a boolean so the caller can log which
    rule fired without logging the text.

    Two exclusions:

    * text longer than ``max_text_length`` is not judged - it is a document, not
      a paragraph, and scanning it on every mutation would stall the page;
    * text shorter than two characters is not judged - a single character matches
      too much to be meaningful, and the engine's own lists are multi-character.
    """
    if not rules.hide_text or not rules.terms:
        return None
    if not text or len(text) < 2:
        return None
    if len(text) > rules.max_text_length:
        return None

    haystack = text if rules.case_sensitive else text.lower()
    for term in rules.terms:
        needle = term if rules.case_sensitive else term.lower()
        if needle and needle in haystack:
            return term
    return None


def decide_image(url: str, rules: Rules) -> str:
    """Classify an image element as ``"score"``, ``"skip"`` or ``"ignore"``.

    ``"score"``   send it to the backend - it looks like content.
    ``"skip"``    an image, but not worth 90 ms: tracking pixels, spacers,
                  inline icons, data URIs, anything smaller than 64 px.
    ``"ignore"``  not an image at all.

    Size is not known from the URL, so the script checks the rendered size
    separately; this function only decides from the URL, which is what can be
    tested here.

    ``data:`` URIs are skipped rather than scored: they are usually inline icons,
    and a large one would mean shipping the bytes to the service over HTTP, which
    duplicates a payload the page already has. The service accepts bytes; the
    script sends them only for network images.
    """
    if not url:
        return "ignore"
    if not rules.hide_images:
        return "skip"

    lowered = url.lower()
    if lowered.startswith("data:"):
        return "skip"
    if lowered.startswith(("chrome:", "about:", "moz-extension:")):
        return "ignore"
    if not lowered.startswith(("http://", "https://", "//")):
        return "ignore"

    path = lowered.split("?", 1)[0].split("#", 1)[0]
    if path.endswith(IMAGE_EXTENSIONS):
        return "score"

    # No extension: could be a CDN path, could be a tracking beacon. Scored only
    # if it is plausibly large, which the script decides from rendered size. The
    # default here is "score" because a missing extension is not evidence of
    # being an icon, and a missed image is worse than a wasted 90 ms.
    return "score"


def extension_config(rules: Rules) -> str:
    """The JSON file the content script loads at startup."""
    return json.dumps(rules.to_dict(), indent=1, ensure_ascii=False)


def manifest(*, version: str = "0.1.0", extension_id: str = "") -> dict:
    """The ``manifest.json`` shared by Chromium and Firefox.

    One manifest, not two. The differences that matter are handled by optional
    keys both browsers tolerate:

    * ``background.service_worker`` (Chromium MV3) versus
      ``background.scripts`` (Firefox) - both are emitted, and each browser
      ignores the other's key;
    * ``browser_specific_settings`` is Firefox-only and ignored by Chromium.

    ``host_permissions`` is deliberately narrow: the extension reads pages the
    user visits, and nothing else. It does not touch ``chrome://`` pages, other
    extensions, or the file system.
    """
    document = {
        "manifest_version": MANIFEST_VERSION,
        "name": EXTENSION_NAME,
        "version": version,
        "description": (
            "Hides individual page elements that match the parent's word lists "
            "or image classifier. Judges content, not domains."),
        "permissions": ["storage", "activeTab"],
        "host_permissions": ["http://*/*", "https://*/*"],
        "content_scripts": [
            {
                "matches": ["http://*/*", "https://*/*"],
                "js": ["content.js"],
                "run_at": "document_idle",
                "all_frames": True,
            }
        ],
        "background": {
            # Chromium MV3 reads service_worker; Firefox reads scripts. Each
            # ignores the other key, which is why one manifest works for both.
            "service_worker": "background.js",
            "scripts": ["background.js"],
        },
        "action": {"default_title": EXTENSION_NAME},
        "options_ui": {"page": "options.html", "open_in_tab": True},
        # Firefox requires an explicit id for a self-hosted extension installed
        # by policy; Chromium uses the Web Store id or a key-derived one.
        "browser_specific_settings": {
            "gecko": {
                "id": "greendam2026@localhost",
                "strict_min_version": "109.0",
            }
        },
    }
    if extension_id:
        document["browser_specific_settings"]["gecko"]["id"] = extension_id
    return document


def build_extension(destination: Path, rules: Rules, *,
                    version: str = "0.1.0",
                    extension_id: str = "") -> dict[str, object]:
    """Write a loadable extension directory.

    Emits the static assets beside this module, the generated ``config.json``, and
    the manifest. Writing the config rather than shipping a hand-edited one is
    what keeps the extension's rules identical to the engine's: ``config.json``
    is produced from :class:`Rules`, which the tests exercise.

    Returns a summary for the installer to log. Nothing here touches the
    registry - installing into a browser is :mod:`host.policy`.
    """
    import shutil

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    assets = Path(__file__).resolve().parent
    copied: list[str] = []
    for name in ("content.js", "background.js", "options.html", "options.js"):
        source = assets / name
        if not source.is_file():                 # pragma: no cover - packaging error
            raise FileNotFoundError(f"extension asset missing: {source}")
        shutil.copy2(source, destination / name)
        copied.append(name)

    config_text = extension_config(rules)
    (destination / "config.json").write_text(config_text, encoding="utf-8")
    copied.append("config.json")

    manifest_text = json.dumps(manifest(version=version,
                                        extension_id=extension_id),
                               indent=1, ensure_ascii=False)
    (destination / "manifest.json").write_text(manifest_text, encoding="utf-8")
    copied.append("manifest.json")

    return {
        "destination": str(destination),
        "files": copied,
        "terms": len(rules.terms),
        "hide_text": rules.hide_text,
        "hide_images": rules.hide_images,
    }
