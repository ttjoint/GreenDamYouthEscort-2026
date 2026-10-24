"""The host layer's own contracts.

``tests/test_contracts.py`` proves the *engine* does not contain the behaviours
on the refusal list, but it only scans ``src/green_dam``. The host is where a
real ``TerminateProcess``, a real registry write or a real ``socket`` would
actually live, so the same negative requirements have to be asserted here or the
guarantee has a hole exactly where it matters most.

Every test names the requirement ID it enforces. A test without one would be a
test of the code rather than of the design, which is the rule the engine's
contract file already follows.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "host"
sys.path.insert(0, str(ROOT))


def _modules() -> dict[str, Path]:
    """Every host module, subpackages included.

    This used to glob only ``host/*.py``, which silently exempted
    ``host/proxy/`` - the one subpackage that opens real sockets. A scan that
    quietly skips the most dangerous directory is worse than no scan, because it
    reports "clean" for code it never read.

    It then skipped every ``__init__.py`` as well, on the reasonable-sounding
    assumption that an initialiser holds no logic. That exempted three of the
    largest modules in the package - ``imaging`` (the ONNX backend),
    ``extension`` (the filtering rules) and ``policy`` (the registry writes) -
    which is the same failure again, one level down. Package initialisers here
    *are* the modules; the subdirectory is just how they are filed.

    Keys are dotted paths (``proxy.server``, ``imaging``) so a failure names
    the file.
    """
    found: dict[str, Path] = {}
    for path in sorted(HOST.rglob("*.py")):
        parts = path.relative_to(HOST).with_suffix("").parts
        if parts and parts[0] == "tests":
            continue
        # ``host/__init__.py`` is the package's own docstring and version; it
        # has no behaviour to check. Subpackage initialisers do.
        if parts == ("__init__",):
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found[".".join(parts)] = path
    return found


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports(path: Path) -> set[str]:
    """Absolute top-level module names imported anywhere in the file."""
    tree = ast.parse(_source(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class OmissionTest(unittest.TestCase):
    """B1-B12, in the layer that could actually violate them."""

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
        ("InitiateShutdown", "B12", "forced reboot"),
        ("ExitWindowsEx", "B12", "forced reboot"),
        ("Shell_TrayWnd", "B10", "blocking Task Manager"),
        ("Taskmgr", "B10", "blocking Task Manager"),
    ]

    def test_no_forbidden_api_references(self):
        offenders: list[str] = []
        for name, path in _modules().items():
            text = _source(path)
            for pattern, req, why in self.FORBIDDEN_SOURCE:
                if pattern in text:
                    offenders.append(f"{name}: {pattern} ({req}: {why})")
        self.assertEqual(offenders, [],
                         msg="forbidden behaviour found in the host: "
                             + "; ".join(offenders))

    def test_nothing_writes_into_the_system_directory(self):
        """B7: the original wrote into %WinDir%\\System32.

        ``host/paths.py`` mentions the name deliberately, once, as the value it
        refuses. Any *other* module mentioning it is a red flag, and the refusal
        itself is exercised by ``test_capabilities.py``.
        """
        allowed = {"paths"}
        offenders = [name for name, path in _modules().items()
                     if name not in allowed and "System32" in _source(path)]
        self.assertEqual(offenders, [],
                         msg=f"modules referencing the system directory: "
                             f"{offenders}. Writes go under ProgramData or "
                             f"LOCALAPPDATA; see host/paths.py.")

    def test_no_hardcoded_password(self):
        """B5: the original shipped a hardcoded password; the host must not.

        The engine checks this with a 7-digit literal scan. The host gets the
        same scan plus an explicit look for the shape of an assigned secret, so
        a password cannot be smuggled in as a string constant.
        """
        offenders: list[str] = []
        for name, path in _modules().items():
            for node in ast.walk(ast.parse(_source(path))):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if "password" not in target.id.lower():
                        continue
                    if isinstance(node.value, ast.Constant) and node.value.value:
                        offenders.append(f"{name}: {target.id} = <literal>")
        self.assertEqual(offenders, [],
                         msg=f"hardcoded credentials: {offenders}. The engine "
                             f"has secret.py; use it.")


class PlatformSplitTest(unittest.TestCase):
    """A1: the host is the only place that may know about Windows."""

    def test_engine_never_imports_host(self):
        """The dependency arrow points one way, and this is the half the
        engine's own contract file cannot see."""
        engine = ROOT / "src" / "green_dam"
        offenders: list[str] = []
        for path in sorted(engine.glob("*.py")):
            for name in _imports(path):
                if name == "host":
                    offenders.append(path.name)
        self.assertEqual(offenders, [],
                         msg=f"engine modules import the host: {offenders}. "
                             f"The host adapts to the engine, never the "
                             f"reverse (docs/architecture.md §Target).")

    def test_host_imports_engine_through_one_module(self):
        """Every engine import goes through ``host.engine``.

        Not a style rule: if twenty modules import ``green_dam`` directly, a
        broken deployment produces twenty tracebacks for one problem, and the
        resolved-path report in ``Wiring`` stops being the whole story.

        ``proxy.policy`` and ``proxy.inbound`` are deliberate exceptions and the
        only ones. Both call the engine per request - ``transport.plan`` and
        ``Filter.check_request`` - and routing that through ``host.engine``
        would mean ``engine.py`` growing a second responsibility (per-request
        plumbing) instead of staying the one-time wiring point. Both are inside
        ``proxy/``, so the exception is scoped to the package that does the
        per-request work rather than granted module by module.
        """
        allowed = {"engine", "proxy.policy", "proxy.inbound"}
        offenders: list[str] = []
        for name, path in _modules().items():
            if name in allowed:
                continue
            if "green_dam" in _imports(path):
                offenders.append(name)
        self.assertEqual(offenders, [],
                         msg=f"modules importing the engine directly: "
                             f"{offenders}. Call host.engine.open_filter() "
                             f"instead.")


class DependencyTest(unittest.TestCase):
    """A2 applied to the host: third-party imports stay lazy.

    The engine is pure standard library. The host is not, but every third-party
    import must happen *inside* a function, so that importing a host module on a
    machine missing a dependency still succeeds and the capability probe can
    report the absence instead of an ImportError at the top of the stack.
    """

    #: Third-party packages the host is allowed to use, from
    #: docs/dependency-audit.md §3.
    ALLOWED = {
        "psutil", "cryptography", "win32api", "win32con", "win32file",
        "win32service", "win32serviceutil", "win32security", "win32net",
        "webview", "pystray", "PIL", "numpy", "onnxruntime", "uiautomation",
        "winrt", "servicemanager", "pefile",
    }

    def test_third_party_imports_are_inside_functions(self):
        offenders: list[str] = []
        for name, path in _modules().items():
            tree = ast.parse(_source(path))
            for node in tree.body:                 # module level only
                names: set[str] = set()
                if isinstance(node, ast.Import):
                    names = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = {node.module.split(".")[0]}
                bad = names & self.ALLOWED
                if bad:
                    offenders.append(f"{name}: {sorted(bad)}")
        self.assertEqual(offenders, [],
                         msg=f"third-party imports at module level: "
                             f"{offenders}. Move them inside the function "
                             f"that needs them so a missing dependency "
                             f"degrades one feature instead of breaking "
                             f"every import.")

    def test_undeclared_third_party_import(self):
        """A new dependency must be added to the audit, not just used."""
        import sys as _sys

        stdlib = getattr(_sys, "stdlib_module_names", frozenset())
        internal = set(_modules()) | {"host", "green_dam"}
        offenders: dict[str, set[str]] = {}
        for name, path in _modules().items():
            external = _imports(path) - stdlib - internal
            bad = external - self.ALLOWED
            if bad:
                offenders[name] = bad
        self.assertEqual(offenders, {},
                         msg=f"undeclared third-party imports: {offenders}. "
                             f"Record the dependency in "
                             f"docs/dependency-audit.md and add it to "
                             f"ALLOWED here.")


class CapabilityCoverageTest(unittest.TestCase):
    """Every third-party package the host uses must be probed.

    Checked at *distribution* level, not module level: ``win32serviceutil`` and
    ``win32security`` are submodules of pywin32, and probing ``win32api`` already
    answers "is pywin32 installed". Probing each submodule separately would
    report the same fact three times and imply they can be installed apart.
    """

    #: Import name -> the capability name reported by the probe. The two differ
    #: whenever the distribution name is not the import name (``Pillow`` ships
    #: ``PIL``, ``pywebview`` ships ``webview``), which is exactly the confusion
    #: this mapping exists to prevent.
    DISTRIBUTION = {
        "win32api": "pywin32", "win32con": "pywin32",
        "win32file": "pywin32", "win32net": "pywin32",
        "win32security": "pywin32", "win32service": "pywin32",
        "win32serviceutil": "pywin32", "servicemanager": "pywin32",
        "winrt": "WinRT OCR",
        "webview": "pywebview",
        "PIL": "Pillow",
    }

    #: Not a capability: an analysis tool used offline, never at runtime.
    NOT_A_CAPABILITY = {"pefile"}

    def test_allowed_packages_are_probed(self):
        from host.capabilities import OPTIONAL, REQUIRED

        probed = {name for name, _m, _s in REQUIRED + OPTIONAL}
        used = DependencyTest.ALLOWED - self.NOT_A_CAPABILITY
        needed = {self.DISTRIBUTION.get(name, name) for name in used}
        missing = needed - probed
        self.assertEqual(missing, set(),
                         msg=f"packages used but never probed: {sorted(missing)}. "
                             f"Add them to host/capabilities.py so the failure "
                             f"mode is 'feature unavailable', not a traceback.")


if __name__ == "__main__":
    unittest.main()
