"""The host layer: everything that touches Windows.

This package sits outside ``src/green_dam`` on purpose. The engine is under a
no-I/O contract enforced by ``tests/test_contracts.py``; the host is where
``socket``, ``subprocess``, ``ctypes`` and ``pywin32`` are allowed to live. The
engine does not change to accommodate the host - the host adapts to the engine's
interfaces (``transport.plan``, ``imagepath.ImageBackend``, ``Filter``).

Two rules that the tests in ``host/tests`` enforce:

1. **Nothing here imports ``green_dam`` at module import time** for a side
   effect. Imports of the engine are explicit and go through
   :mod:`host.engine`, so a broken install produces one clear error instead of
   an import-time traceback from a module nobody called.
2. **The host never re-implements engine logic.** If a decision needs engine
   knowledge, it calls the engine. A second copy of the matching rules would
   drift from the first, and the first is the one with the evidence behind it.

Importing this package must be safe on any platform, including CI without
Windows: everything Windows-specific is imported lazily inside the functions
that need it, and :func:`host.capabilities.probe` reports what is missing
rather than raising.
"""
from __future__ import annotations

__all__ = ["__version__"]

#: Kept in step with ``pyproject.toml``. The host is versioned with the product
#: rather than separately: it has no standalone consumers.
__version__ = "0.1.0"
