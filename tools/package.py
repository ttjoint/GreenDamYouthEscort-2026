"""H7 / A7: assemble a deployment directory that runs with nothing installed.

``host.packaging`` decides what goes in the box; this script puts it there. It is
a *tool*, not part of the product: it runs on a build machine and is never
shipped.

    python -m tools.package --out ../GreenDam-2026
    python -m tools.package --out ../GreenDam-2026 --python C:/Python312
    python -m tools.package --dry-run

Three things it deliberately does not do:

**It does not download CPython.** The embeddable distribution is an external
artefact fetched once by a human, not something a build script should pull from
the network unannounced. ``--python`` points at an existing interpreter whose
``Lib``/DLLs are copied; ``--embeddable`` points at an already-extracted
embeddable zip. Without either, the deployment is assembled *without* an
interpreter and the report says so - which is still useful, because the engine,
host and data layout can then be verified.

**It does not run pip.** Third-party packages are copied from the interpreter
you point at, by name, from the audit's list. A build that pip-installs is a
build that depends on PyPI being reachable and on a resolver being stable; the
audit already decided which packages are needed, so copying them is enough.

**It does not silently overwrite.** If the output directory already contains a
deployment, the script refuses unless ``--force``. A packer that clobbers is a
packer that destroys the one artefact you needed to compare against.

The output is verified against the manifest after copying - file count and sizes
- so a half-finished copy fails loudly rather than producing a deployment that
starts and then cannot find its data.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host.packaging import (  # noqa: E402
    HOST_PACKAGE,
    MODEL_DIR,
    Manifest,
    build_manifest,
    file_digest,
    verify_manifest,
)

#: Third-party packages copied from the build interpreter, by import name.
#:
#: This is the audit's list (``docs/dependency-audit.md`` §3), restricted to what
#: the host actually imports. ``pefile`` is absent on purpose: it is an analysis
#: tool used offline, never at runtime.
RUNTIME_PACKAGES: tuple[str, ...] = (
    "psutil",
    "cryptography",
)

#: pywin32 is not laid out as normal packages. It installs ``win32/``,
#: ``win32com/`` and ``win32comext/`` directories plus a ``.pth`` file that adds
#: ``win32`` and ``win32/lib`` to ``sys.path``. Copying by import name therefore
#: fails for every ``win32*`` module: they are not top-level packages, they are
#: subdirectories of ``win32/`` that only become importable because of the
#: ``.pth``.
#:
#: This tuple lists the directories to copy wholesale, and
#: :data:`PYWIN32_PTH` is the ``.pth`` that makes them importable in the
#: deployment too. Verified against a real pywin32 312 install: without the
#: ``.pth`` the modules are present and unimportable, which is a worse failure
#: than not shipping them.
PYWIN32_DIRS: tuple[str, ...] = (
    "win32", "win32com", "win32comext", "pywin32_system32",
)
PYWIN32_PTH = "pywin32.pth"

#: Optional per-feature packages. Copied when present, skipped with a note when
#: not: a deployment without M4 or M5 is a valid deployment.
OPTIONAL_PACKAGES: tuple[str, ...] = (
    "PIL", "onnxruntime", "uiautomation", "winrt",
)

#: Optional packages that are also optional to *find*. A deployment without a UI
#: framework still enforces policy; the console then degrades to the CLI.
OPTIONAL_UI: tuple[str, ...] = ("webview", "pystray")


def _say(message: str) -> None:
    print(message, flush=True)


def _copy_tree(source: Path, destination: Path) -> int:
    """Copy a package directory, skipping caches. Returns the file count."""
    count = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def _python_exe(interpreter: Path) -> Path | None:
    """The interpreter executable inside ``interpreter``, which may be a dir.

    Callers naturally pass the installation directory (``C:\\Python312``).
    Handing a directory to ``CreateProcess`` raises ``PermissionError``, which
    an earlier version caught and reported as "the interpreter may be broken" -
    a misleading message for a caller who did exactly the right thing.
    """
    if interpreter.is_file():
        return interpreter
    for name in ("python.exe", "pythonw.exe", "python"):
        candidate = interpreter / name
        if candidate.is_file():
            return candidate
    return None


def _site_packages(interpreter: Path) -> list[Path]:
    """Every directory ``interpreter`` would import packages from.

    Read from the interpreter's own ``sysconfig`` and ``site`` by running it,
    rather than guessing ``Lib/site-packages``: a venv, a Store install, a
    standard install and a ``pip install --user`` install all differ.

    Returning a *list* matters here. On this machine ``psutil`` and ``pywin32``
    live in the per-user site directory
    (``%APPDATA%\\Python\\Python312\\site-packages``) while ``cryptography``
    lives in the installation's own ``Lib\\site-packages``. A single-directory
    lookup found the latter, missed the former, and reported "required packages
    missing" for packages that were installed - a wrong diagnosis pointing at a
    reinstall that would have changed nothing.
    """
    import subprocess

    executable = _python_exe(interpreter)
    if executable is None:
        return []

    script = (
        "import sysconfig, site, json\n"
        "paths = []\n"
        "for key in ('purelib', 'platlib'):\n"
        "    paths.append(sysconfig.get_paths()[key])\n"
        "try:\n"
        "    paths.extend(site.getusersitepackages()\n"
        "                 if isinstance(site.getusersitepackages(), list)\n"
        "                 else [site.getusersitepackages()])\n"
        "except Exception:\n"
        "    pass\n"
        "print(json.dumps(paths))")
    try:
        completed = subprocess.run(
            [str(executable), "-c", script], capture_output=True,
            text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []

    import json as _json

    try:
        raw = _json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return []

    found: list[Path] = []
    for text in raw:
        candidate = Path(text)
        if candidate.is_dir() and candidate not in found:
            found.append(candidate)
    return found


def _copy_interpreter(interpreter: Path, destination: Path) -> dict[str, object]:
    """Copy a CPython installation into ``destination``.

    Everything a run needs and nothing else: the interpreter binary, its DLLs,
    and the standard library. ``Lib/test`` is excluded because it is 30 MB of
    tests for CPython itself.

    **``Lib/site-packages`` is excluded here and populated separately** by
    :func:`_copy_packages`. Copying it as part of ``Lib`` pulled in every package
    installed on the build machine - on this one that was 5.9 GB, of which 4.3 GB
    was ``torch`` and 1.3 GB ``nvidia``. The deployment is supposed to be about
    20 MB; the bug was invisible in the file *count* and obvious in the size.
    """
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0

    for name in ("python.exe", "pythonw.exe", "python3.dll", "python312.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll"):
        source = interpreter / name
        if source.is_file():
            shutil.copy2(source, destination / name)
            copied += 1

    for name in ("DLLs", "Lib"):
        source = interpreter / name
        if not source.is_dir():
            continue
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            parts = path.relative_to(source).parts
            if parts and parts[0] in {"test", "tests", "__pycache__"}:
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            if name == "Lib" and parts[:1] == ("site-packages",):
                # Third-party packages are chosen by name in _copy_packages.
                continue
            target = destination / name / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1

    return {"files": copied, "source": str(interpreter)}


def _copy_packages(interpreter: Path, destination: Path,
                   *, required: bool) -> dict[str, object]:
    """Copy the named packages from ``interpreter``'s site-packages.

    ``required=True`` makes a missing package a hard error; ``False`` records it
    and continues. The distinction is the difference between a broken deployment
    and one missing an optional feature.
    """
    sites = _site_packages(interpreter)
    if not sites:
        if required:
            raise SystemExit(
                f"could not find site-packages for {interpreter}; "
                f"the interpreter may be broken")
        return {"copied": [], "missing": [], "site_packages": []}

    names = RUNTIME_PACKAGES if required else OPTIONAL_PACKAGES + OPTIONAL_UI
    copied: list[str] = []
    missing: list[str] = []

    for name in names:
        found = None
        for site in sites:
            found = _locate_package(site, name)
            if found is not None:
                break
        if found is None:
            missing.append(name)
            continue
        target = destination / found.name
        if target.exists():
            continue
        if found.is_dir():
            _copy_tree(found, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, target)
        copied.append(name)

    if required:
        # pywin32's layout needs separate handling; see PYWIN32_DIRS.
        pywin32 = _copy_pywin32(sites, destination)
        copied.extend(pywin32["copied"])
        missing.extend(pywin32["missing"])

    if required and missing:
        raise SystemExit(
            f"required packages missing from {interpreter}: {missing}. "
            f"Install them, or pass --python pointing at an interpreter that "
            f"has them.")

    return {"copied": copied, "missing": missing,
            "site_packages": [str(s) for s in sites]}


def _copy_pywin32(sites: list[Path], destination: Path) -> dict[str, object]:
    """Copy pywin32 in the layout its own ``.pth`` expects.

    pywin32 does not install importable top-level modules. It installs
    ``win32/``, ``win32com/`` and ``win32comext/`` directories and a ``.pth``
    file whose contents add ``win32`` and ``win32/lib`` to ``sys.path``. The
    importable names (``win32api``, ``win32serviceutil``, ``pythoncom``) are
    *inside* those directories.

    So this copies the directories wholesale and reproduces the ``.pth``. Without
    the ``.pth`` the files are present and unimportable - a failure mode that
    looks like "pywin32 is installed" in a directory listing and like
    ``ModuleNotFoundError`` at runtime, which is the worst combination to debug.
    """
    copied: list[str] = []
    missing: list[str] = []
    found_any = False

    for site in sites:
        if not (site / "win32").is_dir():
            continue
        found_any = True
        for name in PYWIN32_DIRS:
            source = site / name
            if not source.is_dir():
                continue
            target = destination / name
            if target.exists():
                continue
            _copy_tree(source, target)
            copied.append(name)
        pth = site / PYWIN32_PTH
        if pth.is_file():
            target = destination / PYWIN32_PTH
            if not target.exists():
                shutil.copy2(pth, target)
                copied.append(PYWIN32_PTH)
        # ``pywin32.version.txt`` is read by some pywin32 helpers at import time.
        version = site / "pywin32.version.txt"
        if version.is_file():
            target = destination / version.name
            if not target.exists():
                shutil.copy2(version, target)

    if not found_any:
        missing.append("pywin32")
    return {"copied": copied, "missing": missing}


def _locate_package(site: Path, name: str) -> Path | None:
    """Find one package's directory or module file in ``site``."""
    candidate = site / name
    if candidate.is_dir():
        return candidate
    for suffix in (".py", ".pyd"):
        candidate = site / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    # Distributions whose import name differs from the directory name.
    for pattern in (f"{name}*.dist-info",):
        for match in site.glob(pattern):
            top = match.name.split("-")[0]
            candidate = site / top
            if candidate.exists():
                return candidate
    return None


def _write_metadata(out: Path, manifest: Manifest,
                    python_info: dict | None,
                    packages: dict | None) -> None:
    """Write ``VERSION`` and ``manifest.json``.

    ``manifest.json`` is what the installer's integrity pass and the uninstaller
    read. It records sizes and hashes so a deployment can be verified later
    without the source tree.
    """
    version = "0.1.0"
    pyproject = ROOT / "pyproject.toml"
    if pyproject.is_file():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version ="):
                version = line.split("=", 1)[1].strip().strip('"\'')
                break

    (out / "VERSION").write_text(f"{version}\n", encoding="utf-8")

    payload = {
        "version": version,
        "python": python_info,
        "packages": packages,
        "files": [
            {"path": entry.destination, "reason": entry.reason,
             "size": entry.size, "sha256": file_digest(entry.source)}
            for entry in manifest.entries
        ],
        "excluded": [{"path": path, "why": why}
                     for path, why in manifest.excluded],
    }
    (out / "manifest.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")


def _drop_model(manifest: Manifest) -> Manifest:
    """Remove the model entries, recording the omission rather than hiding it.

    Used by ``--no-optional``. The entry moves to ``excluded`` with a reason, so
    ``manifest.json`` still says the model exists and was left out - a box that
    cannot filter images should say why, not look like it never could.
    """
    prefix = f"data/{MODEL_DIR}/"
    kept = tuple(e for e in manifest.entries
                 if not e.destination.startswith(prefix))
    dropped = tuple((e.destination, "omitted by --no-optional")
                    for e in manifest.entries
                    if e.destination.startswith(prefix))
    still_missing = tuple(m for m in manifest.missing
                          if not m[0].startswith(prefix))
    return Manifest(entries=kept,
                    excluded=manifest.excluded + dropped,
                    missing=still_missing)


def _report(out: Path, manifest: Manifest, python_info, packages) -> None:
    _say("")
    _say(f"deployment: {out}")
    _say(f"  files   : {len(manifest.entries)}")
    _say(f"  size    : {manifest.total_bytes / 1048576:.1f} MB (engine+host+data)")
    for top, count in sorted(manifest.by_top().items()):
        _say(f"    {top:8}: {count}")
    if python_info:
        _say(f"  python  : {python_info['files']} files from {python_info['source']}")
    else:
        _say("  python  : NOT INCLUDED (pass --python or --embeddable)")
    if packages:
        _say(f"  packages: {', '.join(packages['copied']) or 'none'}")
        if packages["missing"]:
            _say(f"    missing: {', '.join(packages['missing'])}")
    if manifest.excluded:
        _say(f"  excluded: {len(manifest.excluded)} files "
             f"(tests, caches; see manifest.json)")
    if manifest.missing:
        _say(f"  MISSING : {[p for p, _ in manifest.missing]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assemble a Green Dam 2026 deployment directory.")
    parser.add_argument("--out", type=Path, required=True,
                        help="output directory for the deployment")
    parser.add_argument("--python", type=Path, default=None,
                        help="an existing CPython installation to embed")
    parser.add_argument("--embeddable", type=Path, default=None,
                        help="an already-extracted embeddable CPython zip")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the manifest without copying")
    parser.add_argument("--force", action="store_true",
                        help="write into a non-empty output directory")
    parser.add_argument("--no-optional", action="store_true",
                        help="skip optional packages and the image model "
                             "(M4/M5); the deployment then reports 'no model'")
    args = parser.parse_args(argv)

    manifest = build_manifest(ROOT)
    if args.no_optional:
        # ``--no-optional`` is the one way to build without the model, and the
        # omission is recorded rather than hidden: the manifest still lists it
        # as excluded, so the box explains itself.
        manifest = _drop_model(manifest)
    if manifest.missing:
        _say("refusing to package: the manifest is incomplete")
        for path, why in manifest.missing:
            _say(f"  {path}: {why}")
        if any(path.startswith("data/models") for path, _why in manifest.missing):
            _say("")
            _say("The image classifier is default content and ships inside the "
                 "deployment: this product is offline by design, and image "
                 "filtering that needs a download is image filtering that does "
                 "not work. See data/models/nsfw-384/README.md for provenance "
                 "and how to place the file.")
            _say("Pass --no-optional for a deliberately smaller build that "
                 "enables no image filtering.")
        return 2

    if args.dry_run:
        _report(args.out, manifest, None, None)
        return 0

    out: Path = args.out
    if out.exists() and any(out.iterdir()) and not args.force:
        _say(f"refusing to overwrite {out}: it is not empty. "
             f"Pass --force to replace it.")
        return 2
    out.mkdir(parents=True, exist_ok=True)

    for entry in manifest.entries:
        target = out / entry.destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry.source, target)

    interpreter = args.python or args.embeddable
    python_info = None
    packages = None
    if interpreter is not None:
        if not interpreter.exists():
            _say(f"refusing to package: {interpreter} does not exist")
            return 2
        python_info = _copy_interpreter(interpreter, out / "python")
        packages = _copy_packages(interpreter, out / "python",
                                  required=True)
        if not args.no_optional:
            optional = _copy_packages(interpreter, out / "python",
                                      required=False)
            packages["copied"].extend(optional["copied"])
            packages["missing"].extend(optional["missing"])

    problems = verify_manifest(out, manifest)
    if problems:
        _say("the copy does not match the manifest:")
        for problem in problems:
            _say(f"  {problem}")
        return 3

    _write_metadata(out, manifest, python_info, packages)
    _report(out, manifest, python_info, packages)
    _say("")
    _say("verified: every planned file is present with the expected size.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
