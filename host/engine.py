"""The one place the host touches the engine.

Every other host module calls :func:`open_filter` and gets back a fully wired
``Filter``. They do not import ``green_dam`` themselves. Two reasons:

1. **One error message.** If the engine is missing or the data files are not
   where they should be, the failure happens here, once, with a message that
   says which file is missing. Twenty modules each doing their own import
   would produce twenty different tracebacks for the same problem.
2. **The engine must not gain host knowledge.** Keeping the import direction
   one-way means ``src/green_dam`` never sees ``host``, which is what
   ``tests/test_contracts.py`` asserts and what makes the engine testable on a
   machine without Windows.

The wiring here is deliberately thin. Everything interesting - matching,
scoring, gating, logging - belongs to the engine and is already tested there.
If this module starts growing decision logic, that logic is in the wrong place.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import paths

__all__ = [
    "EngineUnavailable",
    "Wiring",
    "engine_source_root",
    "open_filter",
    "open_filter_from",
    "open_image_backend",
    "bundled_root",
    "model_search_path",
    "find_model",
    "read_event_log",
    "empty_filter",
    "load_word_packs",
    "verdict_from_score",
]


class EngineUnavailable(RuntimeError):
    """The engine or its data could not be loaded.

    Distinct from a missing *config*: a fresh install legitimately has no
    ``xconfigs.dat`` and the engine substitutes the shipped defaults. This
    exception means the engine itself is not importable, which is a broken
    deployment rather than a first run.
    """


@dataclass(frozen=True)
class Wiring:
    """What ``open_filter`` had to assume, so the caller can log it.

    A host that silently guesses where the data lives is a host that is
    impossible to diagnose. Reporting the resolved paths costs three strings and
    turns "the filter is not blocking anything" into "the filter is reading
    ``C:\\ProgramData\\GreenDam2026\\config`` and that directory does not exist".
    """

    engine_root: Path
    config: Path
    log: Path
    word_lists: Path | None

    def to_dict(self) -> dict[str, str]:
        return {
            "engine_root": str(self.engine_root),
            "config": str(self.config),
            "log": str(self.log),
            "word_lists": str(self.word_lists) if self.word_lists else "",
        }


def engine_source_root() -> Path:
    """Locate the engine package, whether running from a checkout or a bundle.

    Two layouts are supported, in this order:

    ``<root>/src/green_dam``   a source checkout (this repository)
    ``<root>/engine/green_dam`` a deployed bundle (``docs/dependency-audit.md`` §4)

    The bundle layout is tried second because a checkout also contains an
    ``engine``-less tree; trying the checkout first means a developer running
    the host from the repository gets the live source rather than a stale copy.
    """
    here = Path(__file__).resolve().parent
    root = here.parent
    for candidate in (root / "src", root / "engine"):
        if (candidate / "green_dam" / "__init__.py").is_file():
            return candidate
    raise EngineUnavailable(
        f"no green_dam package found under {root / 'src'} or {root / 'engine'}. "
        f"A source checkout keeps the engine in src/; a deployment keeps it in "
        f"engine/. Neither was present, so the host cannot start.")


def _import_engine():
    """Import the engine, adding its root to ``sys.path`` if needed.

    ``sys.path`` manipulation is normally a smell. It is correct here because
    the host is not installed as a package alongside the engine in the bundle
    layout - it is a directory tree, and the engine is found relative to it.
    Doing it once, in one function, is better than every module doing it.
    """
    root = engine_source_root()
    entry = str(root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    try:
        import green_dam.filter as engine_filter
    except ImportError as exc:                   # pragma: no cover - broken install
        raise EngineUnavailable(
            f"found the engine at {root} but could not import it: {exc}") from exc
    return engine_filter


def open_filter_from(config: Path, *, word_lists: Path | None = None,
                     log_path: Path | None = None,
                     legacy=None, image_model: Path | None = None):
    """Build a ``Filter`` from explicit paths. Returns ``(filter, wiring)``.

    ``word_lists`` is optional. Without it the engine still judges hosts,
    processes and images; it simply has no term index, and the engine already
    distinguishes "no lists" from "lists that matched nothing" rather than
    treating both as clean.

    ``legacy`` overrides the policy array read from ``config``. It exists so a
    caller can supply a decoded array it obtained elsewhere - an imported vendor
    blob, a test fixture - without writing a file first. When it is None the
    engine reads ``config`` and substitutes shipped defaults if absent.

    ``image_model`` is a directory or ``.onnx`` file. When it is present *and*
    the runtime can be imported, the filter is built with a model backend, so
    ``check_image_verdict`` returns a score instead of ``no_model``. When it is
    absent the backend stays None and the engine reports "not assessed", which
    is the honest answer rather than a zero.
    """
    engine_filter = _import_engine()

    log = None
    if log_path is not None:
        from green_dam.eventlog import EventLog     # noqa: PLC0415 - lazy by design

        log = EventLog(path=log_path)

    words = None
    if word_lists is not None and Path(word_lists).is_dir():
        words = _load_words(Path(word_lists))

    backend = open_image_backend(image_model)

    built = engine_filter.from_config_dir(config, log=log, words=words,
                                          legacy=legacy,
                                          image_backend=backend)
    wiring = Wiring(engine_root=engine_source_root(), config=Path(config),
                    log=Path(log_path) if log_path else Path(""),
                    word_lists=Path(word_lists) if words else None)
    return built, wiring


def open_image_backend(model: Path | None = None):
    """Open the NSFW model, or return None when it is not usable.

    Three separate reasons to return None, and they are deliberately not
    collapsed: no path was given, the path does not exist, or the runtime is
    missing. Each one means "no image is judged", and the engine renders that
    as ``no_model`` rather than as a clean score. A caller that wants the reason
    can call :func:`host.capabilities.probe` and look at ``onnxruntime``.

    Import errors are caught rather than propagated because a missing optional
    dependency must degrade one feature, not stop the filter from starting - the
    same rule ``host/tests/test_contracts.py::DependencyTest`` enforces for
    imports.
    """
    if model is None:
        return None
    path = Path(model)
    if path.is_dir():
        path = path / "model.onnx"
    if not path.is_file():
        return None
    try:
        from host.imaging import MarqoOnnxBackend   # noqa: PLC0415 - optional

        return MarqoOnnxBackend.from_path(path)
    except Exception:                                # noqa: BLE001 - degrade
        # A model that exists but cannot be loaded (missing onnxruntime, a
        # corrupt file, an unsupported opset) leaves the filter working and the
        # image path reporting "not assessed". Raising here would make one
        # optional extra able to stop the whole product from starting.
        return None


def _load_words(directory: Path):
    """Decode the shipped ``.dat`` lists, or return None when there are none.

    The engine's own loader is used rather than anything reimplemented here:
    the format was decoded from the vendor binary and verified against it, and a
    second decoder would be a second chance to get it wrong.
    """
    from green_dam.legacy_words import load_directory     # noqa: PLC0415

    report, index = load_directory(directory)
    if not report.lists:
        # Nothing decodable. None, not an empty index: "no lists" and "lists
        # that matched nothing" are different states and only the first should
        # silence evidence.
        return None
    return index


def bundled_root() -> Path:
    """The directory holding this installed host package.

    ``<root>/host/engine.py`` in a checkout, ``<deployment>/host/engine.py`` in
    a bundle. Used to find data that travels *with* the code rather than with
    the machine, which is the distinction :func:`model_search_path` exists for.
    """
    return Path(__file__).resolve().parent.parent


def model_search_path() -> tuple[Path, ...]:
    """Where the image classifier may live, in order of preference.

    Two locations, because the two deployment shapes keep data differently:

    ``<bundle>/data/models/nsfw-384``  ships *with* the product
    ``%ProgramData%/GreenDam2026/models/nsfw-384``  installed for the machine

    The bundle is tried first. The packer puts the weights in the deployment
    directory, and a portable install must find its own model without writing
    anything to ``%ProgramData%`` - that is the whole point of portable. The
    machine-wide path comes second so an administrator can update the model for
    every account without touching the program directory, which is read-only
    under ``Program Files``.

    Returning a tuple rather than one path keeps the "which one won" question
    answerable: :func:`open_filter` records the choice in ``Wiring``, so a
    parent reporting "image filtering is not working" can be told which file
    was looked for and which was missing.
    """
    name = "models" / Path("nsfw-384")
    return (bundled_root() / "data" / name, paths.data_dir() / name)


def find_model() -> Path | None:
    """The first usable model directory, or None.

    "Usable" means the directory exists and contains ``model.onnx``. Checking
    the file rather than only the directory matters because the packer ships a
    README and a LICENSE beside the weights: a directory holding only those is
    a deployment where the model was omitted, and reporting it as present would
    turn "no model" into a confusing onnxruntime error.
    """
    for candidate in model_search_path():
        if (candidate / "model.onnx").is_file():
            return candidate
    return None


def open_filter(*, word_lists: Path | None = None,
                image_model: Path | None = None):
    """Build a ``Filter`` from the standard locations. Returns ``(filter, wiring)``.

    Missing directories are tolerated; missing *files* inside them are handled
    by the engine, which substitutes shipped defaults for a fresh install. The
    only failure mode here is a missing engine, which raises
    :class:`EngineUnavailable`.

    The image model is found by :func:`find_model` unless the caller names one.
    It is looked for rather than required: a deployment built with
    ``--no-optional`` is a supported configuration, and it reports "not
    assessed" instead of silently scoring zero.
    """
    config = paths.config_dir()
    log_path = paths.log_path()
    lists = word_lists if word_lists is not None else paths.data_dir() / "lists"
    if not Path(lists).is_dir():
        lists = None
    if image_model is None:
        image_model = find_model()
    return open_filter_from(config, word_lists=lists, log_path=log_path,
                            image_model=image_model)


def read_event_log(path: Path) -> list[dict]:
    """Read a JSON-lines event log into plain dicts.

    Exposed here so the console does not import ``green_dam.eventlog`` itself:
    every engine import in the host goes through this module, which is what keeps
    a broken deployment to one traceback instead of twenty.

    A missing file is an empty list, not an error. A fresh install has not
    decided anything yet, and that is a normal state rather than a fault.
    """
    if path is None or not Path(path).is_file():
        return []
    from green_dam.eventlog import read_jsonl       # noqa: PLC0415 - lazy by design

    return list(read_jsonl(Path(path)))


def empty_filter():
    """A ``Filter`` with no configuration, no lists and no log.

    Used by the console when it is opened outside an install - showing the
    console of a filter that is not running is more useful than refusing to
    start, and the console already renders that state as "not assessing".
    """
    engine_filter = _import_engine()
    return engine_filter.Filter()


def load_word_packs(directory: Path):
    """Load the word packs from ``directory`` for display.

    Returns an index whose packs may all be disabled, and whose term counts are
    all the console is allowed to show. Kept here for the same reason as
    :func:`read_event_log`: the console does not reach into the engine.
    """
    from green_dam.cli_words import load_index      # noqa: PLC0415

    return load_index(Path(directory))


def verdict_from_score(score, **kwargs):
    """The engine's score-to-verdict mapping, for host backends.

    A model wrapper needs to turn a number into an ``ImageVerdict``, and that
    conversion - the threshold, the ``assessed`` flag, the reason token - must
    exist once. Re-exporting it here rather than letting
    :mod:`host.imaging` import ``green_dam.imagepath`` directly keeps the rule
    that every engine import goes through this module: a broken deployment then
    produces one traceback, not one per module that happens to need the engine.
    """
    _import_engine()                             # puts the engine on sys.path
    from green_dam.imagepath import verdict_from_score as _map  # noqa: PLC0415

    return _map(score, **kwargs)
