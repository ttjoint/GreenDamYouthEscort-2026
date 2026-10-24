"""Test-suite configuration for the engine.

One job: silence the emulator's *expected* access violations.

``test_hnc.py`` and ``test_string_matchers.py`` run the vendor's 32-bit code
under Unicorn and detect the end of a call by letting the emulated ``ret`` jump
to an unmapped page. Unicorn surfaces that as a real OS access violation, which
pytest's ``faulthandler`` plugin dutifully prints with a full traceback - twelve
of them per run, all of them expected, all of them handled by the test.

The problem is not cosmetic. A suite that prints a dozen access violations trains
the reader to ignore access violations, which is exactly the wrong habit in a
repository that also runs native binaries. The signal is real; the noise is ours.

The fix is scoped: ``faulthandler`` is disabled only while those modules'
fixtures are running, and re-enabled afterwards. Doing it globally with
``-p no:faulthandler`` would also hide a genuine segfault anywhere else.

Why a ``conftest.py`` and not a marker on each test: the violation happens inside
``setUpClass`` as well as inside the test bodies, and a fixture that wraps the
module covers both without repeating itself.
"""
from __future__ import annotations

import faulthandler
import sys
from pathlib import Path

import pytest

#: Make ``green_dam`` importable for every test module.
#:
#: Most test files add ``src`` to ``sys.path`` themselves, which works when they
#: are run alone and breaks when they are run together: pytest inserts the
#: project root first, ``green_dam`` resolves as a *namespace* package over
#: ``src/green_dam`` (no ``__init__.py`` at that level in its eyes), and the
#: import fails with "cannot import name X from green_dam (unknown location)".
#:
#: Doing it once here, before any test module is imported, removes the ordering
#: dependency entirely.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: Modules that deliberately fault inside the emulator. Matching on the module
#: name keeps this list honest: a new emulator test must be added here, which is
#: a deliberate act rather than an inherited silence.
EMULATOR_MODULES = ("test_hnc", "test_string_matchers")


def _is_emulator_test(item: pytest.Item) -> bool:
    return item.module.__name__.rsplit(".", 1)[-1] in EMULATOR_MODULES


@pytest.fixture(autouse=True)
def _quiet_expected_faults(request):
    """Disable ``faulthandler`` for the emulator tests, restore it afterwards."""
    if not _is_emulator_test(request.node):
        yield
        return
    faulthandler.disable()
    try:
        yield
    finally:
        # ``enable()`` with no file restores the default stderr sink. pytest's
        # plugin may already have one; re-enabling is idempotent.
        faulthandler.enable()
