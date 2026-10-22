"""Structural limits: the code must stay navigable as it grows.

``test_contracts.py`` checks the *shape of the dependency graph*. This file
checks the *size of the pieces*. They answer different questions: a module can
have perfect layering and still become a 2000-line dumping ground.

The limits are not aesthetic. Each one corresponds to a specific way this
project could become hard to work on:

===============  ==========================================================
limit            what exceeding it would mean
===============  ==========================================================
module lines     the module has more than one responsibility
class lines      the class is a god object
function lines   the function needs sub-steps extracted
defs per module  the module is a grab bag
methods per cls  the class is doing too many jobs
===============  ==========================================================

Oversize items that were audited and accepted live in ``ALLOWED_*`` with a
reason. Adding a new one requires an explicit edit here - that is the point.
A comment in a doc saying "this class is big" rots; a test does not.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "green_dam"
sys.path.insert(0, str(ROOT / "src"))

#: Hard ceilings. Chosen to be comfortably above the current code so ordinary
#: growth does not trip them, but low enough to catch a real runaway.
MAX_MODULE_LINES = 450
MAX_CLASS_LINES = 350
MAX_FUNCTION_LINES = 90
MAX_DEFS_PER_MODULE = 14
MAX_METHODS_PER_CLASS = 25

#: Audited exceptions. Every entry needs a reason and a pointer to the audit.
#: See docs/structure-audit.md.
ALLOWED_OVERSIZE_MODULE = {
    "chunker": (
        "1192 lines, accepted: the whole module is the Chinese chunking rules. "
        "It imports nothing from the decision layers and has its own test file, "
        "so its size costs nothing outside itself. Audited in "
        "structure-audit.md 3.2."),
}

ALLOWED_OVERSIZE_CLASS = {
    "chunker.SentenceChunker": (
        "600 lines, accepted: the complexity is the Chinese chunking rules "
        "themselves, not the design. Splitting would mean threading shared "
        "state between pieces and make it harder to check against the "
        "reverse-engineering notes. Audited in structure-audit.md 3.2."),
}

ALLOWED_OVERSIZE_FUNCTION = {
    "chunker._chunk_clause": (
        "128 lines, accepted: one clause-splitting algorithm. Same reasoning "
        "as SentenceChunker. Audited in structure-audit.md 3.2."),
    "hnc.classify": (
        "112 lines, accepted: a decision table over concept classes. The "
        "length is the number of categories, not tangled logic; splitting it "
        "would scatter one table across several functions."),
}


def _modules() -> dict[str, Path]:
    return {p.stem: p for p in sorted(SRC.glob("*.py")) if p.stem != "__init__"}


def _defs(path: Path) -> list[tuple[str, ast.AST]]:
    """Top-level classes and functions in a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [(n.name, n) for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef))]


def _span(node: ast.AST) -> int:
    return node.end_lineno - node.lineno + 1


class ModuleSizeTest(unittest.TestCase):
    def test_module_lines(self):
        offenders = []
        for name, path in _modules().items():
            if name in ALLOWED_OVERSIZE_MODULE:
                continue
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > MAX_MODULE_LINES:
                offenders.append(f"{name}: {lines} lines")
        self.assertEqual(
            offenders, [],
            msg=f"modules over {MAX_MODULE_LINES} lines: {offenders}. "
                f"Split by responsibility, or raise the limit deliberately.")

    def test_defs_per_module(self):
        offenders = []
        for name, path in _modules().items():
            count = len(_defs(path))
            if count > MAX_DEFS_PER_MODULE:
                offenders.append(f"{name}: {count} defs")
        self.assertEqual(
            offenders, [],
            msg=f"modules with more than {MAX_DEFS_PER_MODULE} top-level "
                f"definitions: {offenders}")


class ClassSizeTest(unittest.TestCase):
    def test_class_lines(self):
        offenders = []
        for name, path in _modules().items():
            for def_name, node in _defs(path):
                if not isinstance(node, ast.ClassDef):
                    continue
                key = f"{name}.{def_name}"
                if key in ALLOWED_OVERSIZE_CLASS:
                    continue
                span = _span(node)
                if span > MAX_CLASS_LINES:
                    offenders.append(f"{key}: {span} lines")
        self.assertEqual(
            offenders, [],
            msg=f"classes over {MAX_CLASS_LINES} lines: {offenders}. "
                f"Split by responsibility, or register an audited exception.")

    def test_methods_per_class(self):
        offenders = []
        for name, path in _modules().items():
            for def_name, node in _defs(path):
                if not isinstance(node, ast.ClassDef):
                    continue
                methods = [n for n in node.body
                           if isinstance(n, (ast.FunctionDef,
                                             ast.AsyncFunctionDef))]
                if len(methods) > MAX_METHODS_PER_CLASS:
                    offenders.append(f"{name}.{def_name}: {len(methods)}")
        self.assertEqual(
            offenders, [],
            msg=f"classes with more than {MAX_METHODS_PER_CLASS} methods: "
                f"{offenders}")


class FunctionSizeTest(unittest.TestCase):
    def test_function_lines(self):
        offenders = []
        for name, path in _modules().items():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef,
                                         ast.AsyncFunctionDef)):
                    continue
                key = f"{name}.{node.name}"
                if key in ALLOWED_OVERSIZE_FUNCTION:
                    continue
                span = _span(node)
                if span > MAX_FUNCTION_LINES:
                    offenders.append(f"{key}: {span} lines")
        self.assertEqual(
            offenders, [],
            msg=f"functions over {MAX_FUNCTION_LINES} lines: {offenders}. "
                f"Extract sub-steps, or register an audited exception.")

    def test_nesting_depth(self):
        """Deep nesting is the usual reason a function grew."""
        offenders = []

        def depth(node: ast.AST, level: int = 0) -> int:
            best = level
            for child in ast.iter_child_nodes(node):
                step = 1 if isinstance(child, (ast.If, ast.For, ast.While,
                                               ast.With, ast.Try)) else 0
                best = max(best, depth(child, level + step))
            return best

        for name, path in _modules().items():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    d = depth(node)
                    if d > 5:
                        offenders.append(f"{name}.{node.name}: depth {d}")
        self.assertEqual(offenders, [],
                         msg=f"functions nested deeper than 5: {offenders}")


class ExceptionRegistryTest(unittest.TestCase):
    """An exception must name something that still exists and still needs it."""

    def test_allowed_classes_still_exist(self):
        for key in ALLOWED_OVERSIZE_CLASS:
            module, _, name = key.partition(".")
            path = SRC / f"{module}.py"
            self.assertTrue(path.exists(), msg=f"{key}: no module {module}")
            names = {n for n, _ in _defs(path)}
            self.assertIn(name, names, msg=f"{key}: no such definition")

    def test_allowed_functions_still_exist(self):
        for key in ALLOWED_OVERSIZE_FUNCTION:
            module, _, name = key.partition(".")
            path = SRC / f"{module}.py"
            self.assertTrue(path.exists(), msg=f"{key}: no module {module}")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = {n.name for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)}
            self.assertIn(name, found, msg=f"{key}: no such function")

    def test_allowed_modules_still_exist(self):
        for key in ALLOWED_OVERSIZE_MODULE:
            self.assertTrue((SRC / f"{key}.py").exists(),
                            msg=f"{key}: no such module")

    def test_exceptions_have_reasons(self):
        for table in (ALLOWED_OVERSIZE_MODULE, ALLOWED_OVERSIZE_CLASS,
                      ALLOWED_OVERSIZE_FUNCTION):
            for key, reason in table.items():
                self.assertTrue(reason.strip(),
                                msg=f"{key} has no reason recorded")
                self.assertGreater(len(reason), 40,
                                   msg=f"{key}'s reason is too terse to be "
                                       f"useful")


class ResponsibilityTest(unittest.TestCase):
    """Modules that were split apart must stay apart.

    This encodes the outcome of the structure audit: the four evidence paths
    each live in their own module, and the facade does not re-implement them.
    """

    def _source(self, name: str) -> str:
        return (SRC / f"{name}.py").read_text(encoding="utf-8")

    def test_gate_is_its_own_module(self):
        source = self._source("gate")
        for token in ("class Gate", "DISABLED_ALL", "DISABLED_KEYWORD",
                      "DISABLED_IMAGE"):
            self.assertIn(token, source)

    def test_textpath_is_its_own_module(self):
        source = self._source("textpath")
        for token in ("def gather", "def evaluate", "def findings_from_backend"):
            self.assertIn(token, source)

    def test_filter_delegates_instead_of_reimplementing(self):
        source = self._source("filter")
        # It should call into the helpers...
        self.assertIn("from .gate import Gate", source)
        self.assertIn("from .textpath import TextSources, evaluate", source)
        # ...and not carry the switch tokens or the level rule itself.
        for token in ("legacy_filtering_disabled", "legacy_keyword_filter_disabled",
                      "image_filter_disabled"):
            self.assertNotIn(
                token, source,
                msg=f"filter.py hardcodes the gate reason {token!r}; that "
                    f"belongs in gate.py")

    def test_filter_does_not_scan_levels_itself(self):
        source = self._source("filter")
        self.assertNotIn("all_levels", source,
                         msg="filter.py interprets hit levels; that belongs "
                             "in textpath.py")

    def test_chunker_stays_isolated(self):
        # chunker is allowed to be large, but it must not start importing the
        # decision layers - that would make it a second facade.
        source = self._source("chunker")
        for forbidden in ("from .filter", "from .policy", "from .intercept",
                          "from .textpath"):
            self.assertNotIn(forbidden, source,
                             msg=f"chunker imports {forbidden}; it must stay a "
                                 f"pure text-analysis module")


if __name__ == "__main__":
    unittest.main()
