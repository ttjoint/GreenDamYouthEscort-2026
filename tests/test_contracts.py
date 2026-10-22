"""Architecture contracts: the properties that must hold structurally.

These tests do not check behaviour. They check that the *shape* of the code
still matches the design in ``docs/architecture.md``, so a well-meaning refactor
cannot quietly turn the reimplementation into something the original product
was criticised for.

Every test names the requirement it enforces, and the requirement IDs match
``docs/requirements-traceability.md``. A test without a requirement ID would be
a test of the code rather than of the design.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "green_dam"
sys.path.insert(0, str(ROOT / "src"))


def _modules() -> dict[str, Path]:
    return {p.stem: p for p in sorted(SRC.glob("*.py")) if p.stem != "__init__"}


def _imports(path: Path) -> tuple[set[str], set[str]]:
    """Return (internal green_dam modules, absolute top-level external names)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    internal: set[str] = set()
    external: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == "green_dam":
                    parts = alias.name.split(".")
                    if len(parts) > 1:
                        internal.add(parts[1])
                else:
                    external.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # relative import
                if node.module:
                    internal.add(node.module.split(".")[0])
                else:
                    for alias in node.names:
                        internal.add(alias.name.split(".")[0])
            elif node.module:
                top = node.module.split(".")[0]
                if top == "green_dam":
                    parts = node.module.split(".")
                    if len(parts) > 1:
                        internal.add(parts[1])
                else:
                    external.add(top)
    return internal, external


class LayeringTest(unittest.TestCase):
    """A2, A3, A4: acyclic, one-directional module graph."""

    #: Allowed dependency edges. A module may import only these.
    #: Ordering encodes the intended layering: leaves first.
    ALLOWED = {
        "sysdat": set(),
        "eventlog": set(),
        "namelist": set(),
        "imaging": set(),
        "imaging_checkex": set(),
        "imaging_skin": set(),
        "imaging_skin_hsv": set(),
        "hnc": set(),
        "l_concepts": set(),
        "chunk_tags": set(),
        "wordlists": set(),
        "config": {"sysdat"},
        "gate": {"sysdat"},
        "secret": set(),
        "journal": set(),
        "namelists": {"namelist"},
        "chunker": {"chunk_tags"},
        "policy": {"sysdat", "wordlists"},
        "intercept": {"eventlog", "namelists"},
        "semantic": {"policy"},
        "backends": {"semantic"},
        "similarity": {"chunker"},
        "chunkpath": {"chunker", "policy"},
        "simpath": {"chunker", "policy", "similarity"},
        "transport": {"intercept"},
        "textpath": {"policy", "semantic"},
        "filter": {"backends", "chunkpath", "config", "eventlog", "gate",
                   "imaging", "intercept", "namelists", "policy", "schedule",
                   "semantic", "sysdat", "textpath", "wordlists"},
        "replay": {"filter", "transport"},
        "schedule": set(),
        "cli_admin": {"config", "eventlog", "filter", "schedule",
                      "sysdat"},
        "cli_filter": {"backends", "chunkpath", "eventlog", "filter", "imaging",
                       "imaging_checkex", "imaging_skin_hsv", "intercept", "replay",
                       "simpath"},
        "cli_lifecycle": {"journal", "secret"},
        "cli": {"cli_admin", "cli_filter", "cli_lifecycle"},
    }

    def test_every_module_is_declared(self):
        """A new module must be placed in the layering, not added silently."""
        self.assertEqual(set(_modules()), set(self.ALLOWED))

    def test_no_undeclared_edges(self):
        for name, path in _modules().items():
            internal, _ = _imports(path)
            actual = internal & set(self.ALLOWED)
            extra = actual - self.ALLOWED[name]
            self.assertEqual(
                extra, set(),
                msg=f"{name} imports {sorted(extra)}, which the layering "
                    f"forbids. Update ALLOWED deliberately, or remove it.")

    def test_no_cycles(self):
        graph = {name: self.ALLOWED[name] for name in self.ALLOWED}
        seen: set[str] = set()
        stack: list[str] = []
        found: list[list[str]] = []

        def walk(node: str) -> None:
            if node in stack:
                found.append(stack[stack.index(node):] + [node])
                return
            if node in seen:
                return
            seen.add(node)
            stack.append(node)
            for dep in graph.get(node, ()):
                walk(dep)
            stack.pop()

        for module in graph:
            walk(module)
        self.assertEqual(found, [], msg=f"import cycles: {found}")

    def test_leaves_import_nothing_internal(self):
        """A2's structural half: the engine core has no internal dependencies."""
        for name in ("sysdat", "eventlog", "namelist", "imaging", "wordlists"):
            internal, _ = _imports(SRC / f"{name}.py")
            self.assertEqual(internal, set(), msg=f"{name} is a leaf module")


class DependencyTest(unittest.TestCase):
    """A2: the engine core is pure standard library."""

    #: Standard-library modules the engine is allowed to use. Anything else is
    #: a third-party dependency, which the architecture forbids.
    STDLIB = {
        "__future__", "argparse", "ast", "base64", "collections",
        "dataclasses", "datetime", "enum", "hashlib", "hmac", "json",
        "math", "os", "pathlib", "re", "secrets", "struct", "sys",
        "tempfile", "threading", "time", "typing", "unicodedata",
        "itertools", "functools", "abc", "copy", "io", "getpass",
    }

    def test_only_stdlib(self):
        offenders: dict[str, set[str]] = {}
        for name, path in _modules().items():
            _, external = _imports(path)
            bad = external - self.STDLIB
            if bad:
                offenders[name] = bad
        self.assertEqual(
            offenders, {},
            msg=f"third-party imports in the engine: {offenders}. "
                f"architecture.md requires a zero-dependency core.")

    def test_no_numpy_or_ml_runtime(self):
        """A2 / F4: image classification must be optional, not a core import."""
        for name, path in _modules().items():
            _, external = _imports(path)
            for forbidden in ("numpy", "cv2", "onnxruntime", "jieba", "torch"):
                self.assertNotIn(forbidden, external,
                                 msg=f"{name} imports {forbidden}")


class NoIOTest(unittest.TestCase):
    """A5: the engine layer performs no OS or network I/O at import time."""

    FORBIDDEN = {
        "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "telnetlib",
        "subprocess", "ctypes", "winreg", "_winapi", "multiprocessing",
        "asyncio", "select", "mmap", "shutil",
    }

    def test_no_io_imports(self):
        offenders: dict[str, set[str]] = {}
        for name, path in _modules().items():
            _, external = _imports(path)
            bad = external & self.FORBIDDEN
            if bad:
                offenders[name] = bad
        self.assertEqual(
            offenders, {},
            msg=f"I/O imports in the engine layer: {offenders}. "
                f"The engine must stay platform-independent (A1/A5).")

    def test_no_import_time_file_reads(self):
        """A module must not read files while being imported.

        ``load_default()`` exists precisely so loading is explicit. A module-level
        ``open(...)`` or ``Path(...).read_text()`` would make importing the
        package depend on the filesystem.
        """
        offenders: list[str] = []
        for name, path in _modules().items():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:                 # module level only
                if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            func = sub.func
                            if isinstance(func, ast.Name) and func.id == "open":
                                offenders.append(f"{name}: open()")
                            if isinstance(func, ast.Attribute) and \
                                    func.attr in ("read_text", "read_bytes",
                                                  "load"):
                                offenders.append(f"{name}: {func.attr}()")
        self.assertEqual(offenders, [],
                         msg=f"import-time file reads: {offenders}")


class OmissionTest(unittest.TestCase):
    """B1-B12: the behaviours the architecture explicitly refuses to reproduce.

    These are negative requirements. Proving the code does not contain them is
    the whole point - the original product's security record is why this project
    exists, and reproducing any of these would defeat it.
    """

    #: (pattern, requirement id, why it is forbidden)
    FORBIDDEN_SOURCE = [
        ("SetWindowsHookEx", "B1", "global keyboard/mouse hook"),
        ("WH_KEYBOARD", "B1", "global keyboard hook"),
        ("WH_MOUSE", "B1", "global mouse hook"),
        ("CreateRemoteThread", "B2", "DLL injection"),
        ("VirtualAllocEx", "B2", "DLL injection"),
        ("WriteProcessMemory", "B2", "DLL injection"),
        ("NtCreateDriver", "B3", "kernel driver"),
        ("IoCreateDevice", "B3", "kernel driver"),
        ("ZwLoadDriver", "B3", "kernel driver"),
        ("TerminateProcess", "B8", "killing other processes"),
        ("NtQuerySystemInformation", "B9", "hiding processes"),
        ("NtSetInformationProcess", "B9", "hiding processes"),
        ("HKLM\\\\SOFTWARE\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Run",
         "B11", "surviving uninstall"),
        ("InitiateShutdown", "B12", "forced reboot"),
        ("ExitWindowsEx", "B12", "forced reboot"),
        ("System32", "B7", "writing into the system directory"),
    ]

    def test_no_forbidden_api_references(self):
        offenders: list[str] = []
        for name, path in _modules().items():
            text = path.read_text(encoding="utf-8")
            for pattern, req, why in self.FORBIDDEN_SOURCE:
                if pattern in text:
                    offenders.append(f"{name}: {pattern} ({req}: {why})")
        self.assertEqual(offenders, [],
                         msg="forbidden behaviour found: " + "; ".join(offenders))

    def test_no_hardcoded_password(self):
        """B5: the original shipped a hardcoded password; we must not."""
        # The legacy value is checked for by shape, not by writing it down here:
        # a 7-digit literal that is not part of an offset or a test fixture.
        import re as _re
        offenders: list[str] = []
        for name, path in _modules().items():
            text = path.read_text(encoding="utf-8")
            for match in _re.finditer(r"(?<![\w.])(\d{7})(?![\w.])", text):
                value = match.group(1)
                # Ignore obvious non-passwords: sizes, masks, magic numbers.
                if value in {"1000000", "9999999", "1234567"}:
                    continue
                offenders.append(f"{name}: {value}")
        self.assertEqual(offenders, [],
                         msg=f"7-digit literals that may be passwords: {offenders}")

    def test_no_network_upload_of_collected_data(self):
        """B6: the original uploaded collected identity numbers in plaintext."""
        # Structurally covered by NoIOTest (no socket/http import). This test
        # states the requirement explicitly so the intent is not lost.
        for name, path in _modules().items():
            _, external = _imports(path)
            self.assertFalse(external & {"socket", "http", "urllib"},
                             msg=f"{name} can reach the network")

    def test_no_gdi_inline_hook(self):
        """G2: M5 must use UIA + OCR, not a GDI TextOut hook."""
        for name, path in _modules().items():
            text = path.read_text(encoding="utf-8")
            for pattern in ("TextOut", "ExtTextOut", "GetDC", "BitBlt"):
                self.assertNotIn(pattern, text,
                                 msg=f"{name} references {pattern} (GDI hook)")


class PlatformSplitTest(unittest.TestCase):
    """A1: the engine is platform-independent; the host is Windows-only."""

    def test_no_platform_conditionals_in_engine(self):
        for name, path in _modules().items():
            text = path.read_text(encoding="utf-8")
            for pattern in ("sys.platform", "platform.system", "os.name"):
                self.assertNotIn(
                    pattern, text,
                    msg=f"{name} branches on platform ({pattern}); the engine "
                        f"must be platform-independent")

    def test_no_windows_only_stdlib(self):
        for name, path in _modules().items():
            _, external = _imports(path)
            self.assertFalse(external & {"winreg", "_winapi", "msvcrt", "win32api"},
                             msg=f"{name} imports a Windows-only module")


if __name__ == "__main__":
    unittest.main()
