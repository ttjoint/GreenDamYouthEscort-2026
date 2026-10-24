"""``python -m host.ui``: start the parent console.

Wires the real data sources into the console's provider and opens a window. This
is the only place the console touches the filesystem, the browser detector and
the image backend - everything else in ``host/ui`` takes what it needs as an
argument, which is what makes the screens testable.

If the engine cannot be loaded, the console still starts and says so. A parent
who opens the console to find out why nothing is blocked must not be met with a
traceback: "the engine is not installed" is exactly the answer they need.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):                # pragma: no cover - module invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from host import engine, paths                       # noqa: E402
from host.ui import data                             # noqa: E402
from host.ui.launch import open_console              # noqa: E402
from host.ui.server import ConsoleConfig             # noqa: E402


def _browsers():
    try:
        from host.browsers import detect             # noqa: PLC0415
        return detect()
    except Exception:                                # pragma: no cover - platform
        return None


def _image_backend() -> str | None:
    """The name of the installed model, or None.

    Checked by looking for the model file rather than by importing
    ``onnxruntime``: the console only needs to know whether image judgement will
    happen, and importing a 38 MB runtime to answer a status question would make
    the console slow to open on the machine where it matters.
    """
    model = paths.data_dir() / "models" / "nsfw-384" / "model.onnx"
    return "marqo-384" if model.is_file() else None


def build_provider(*, log_path: Path | None = None):
    """A callable returning the console's payload.

    Every failure is caught and turned into a degraded-but-rendered state. The
    console's job is to explain, so an exception here would defeat the purpose.
    """
    def provider() -> dict:
        filter_ = None
        packs = None
        try:
            filter_, _wiring = engine.open_filter()
        except engine.EngineUnavailable:
            filter_ = None
        try:
            packs = engine.load_word_packs(paths.data_dir() / "wordpacks")
        except Exception:                            # pragma: no cover - defensive
            packs = None

        return data.overview(
            filter_=filter_,
            log_path=log_path if log_path is not None else paths.log_path(),
            packs=packs,
            browsers=_browsers(),
            image_backend=_image_backend(),
        )
    return provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m host.ui",
        description="Open the parent console in a local window.")
    parser.add_argument("--route", default="auto",
                        choices=("auto", "webview", "edge", "browser", "none"),
                        help="which window to use (default: auto)")
    parser.add_argument("--no-block", action="store_true",
                        help="return immediately instead of waiting for the "
                             "window to close")
    parser.add_argument("--print-url", action="store_true",
                        help="print the URL and exit; useful for a browser that "
                             "is already running")
    args = parser.parse_args(argv)

    config = ConsoleConfig(provider=build_provider())
    result, server = open_console(config, route=args.route,
                                  block=not args.no_block)
    try:
        print(f"console: {result.url}")
        print(f"route  : {result.route}")
        if result.headless:
            print("note   : no window could be opened; the URL above works in "
                  "any browser.")
    finally:
        if args.print_url or args.no_block:
            server.shutdown()
            server.server_close()
    return 0


if __name__ == "__main__":                    # pragma: no cover
    raise SystemExit(main())
