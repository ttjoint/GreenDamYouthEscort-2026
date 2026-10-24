"""``python -m host.imaging``: run the local image-scoring service.

This is what makes the browser extension's image path work. ``background.js``
POSTs image bytes to ``127.0.0.1:17871/score``; without this process listening,
every image comes back unscored and stays visible.

Run it beside the proxy::

    python -m host.imaging                 # default port, model from data dir
    python -m host.imaging --model D:/models/nsfw-384
    python -m host.imaging --no-model      # service up, answers "not assessed"

``--no-model`` is a supported mode, not a degraded one: a service that reports
``assessed: false`` lets the extension leave images alone, which is the correct
behaviour when no classifier is installed. Starting it anyway means the
extension's options page can distinguish "service down" from "no model", and
those need different fixes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):                # pragma: no cover - module invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from host import paths                              # noqa: E402
from host.engine import open_image_backend          # noqa: E402
from host.imaging.service import (                  # noqa: E402
    DEFAULT_PORT,
    ScoringConfig,
    serve_in_thread,
)


def default_model_path() -> Path:
    """Where the installer puts the model, per ``host/paths.py``."""
    return paths.data_dir() / "models" / "nsfw-384"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m host.imaging",
        description="Score images for the browser extension on loopback.")
    parser.add_argument("--model", default=None,
                        help="model directory or .onnx file (default: the "
                             "installed model, if present)")
    parser.add_argument("--no-model", action="store_true",
                        help="run without a model; answers 'not assessed'")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port to bind on 127.0.0.1 (default: {DEFAULT_PORT})")
    args = parser.parse_args(argv)

    backend = None
    if not args.no_model:
        model = Path(args.model) if args.model else default_model_path()
        backend = open_image_backend(model)
        if backend is None:
            print(f"note   : no usable model at {model}; the service will "
                  f"answer 'not assessed' and the extension will leave images "
                  f"visible. Install the 'image' extra and the model, or pass "
                  f"--no-model to silence this.")

    config = ScoringConfig(backend=backend, port=args.port)
    server, _thread = serve_in_thread(config)
    print(f"scoring: {server.url}")
    print(f"backend: {getattr(backend, 'name', 'none')}")
    try:
        # Serve until interrupted. ``serve_forever`` is already on the daemon
        # thread; this blocks the main thread so Ctrl-C works and the process
        # does not exit immediately.
        while True:
            _thread.join(timeout=1.0)
            if not _thread.is_alive():           # pragma: no cover - shutdown
                break
    except KeyboardInterrupt:                    # pragma: no cover
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main())
