# -*- coding: utf-8 -*-
"""模块清单：每个 PE 是什么、谁依赖谁、它引用了哪些数据文件。

这个工具回答一个绕了很多圈的问题——"这一个 exe 和那一个 dll 到底
什么关系"。人工看十五份导入表得出的结论，不写下来就会反复重查。

它只做三件事，都基于文件里的硬字节：

1. **身份**：版本资源里的公司/产品/描述，加导出表。
   这直接分出三方产品线（金惠、大正、KingWay）和第三方库。
2. **依赖**：导入表指向安装树内其它模块的边。
   系统 DLL 全部丢掉，只看自己人。
3. **数据文件**：模块里出现的数据文件名，以及该文件在树上是否存在。

最后一项是重点。安装树里有七张 `.dat` 是从混淆表解码出来的，
但也有模块声明了文件而树上没有——那种"声明了却不存在"的必须
单独列出来，否则会再次把"文件缺失"误当成"还没找到"。

用法：

    python tools/audit_modules.py --legacy ../LH-installed-tree
    python tools/audit_modules.py --legacy ../LH-installed-tree --json data/modules.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    import pefile
except ImportError:  # pragma: no cover
    sys.exit("pefile is required: pip install pefile")

# 安装树里的模块都在这几个目录下。
SCAN_DIRS = ("Windows", "Windows/System32")

# Windows 自带的 DLL，不算产品内部依赖。
SYSTEM_DLLS = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "ole32.dll",
    "oleaut32.dll", "comctl32.dll", "shell32.dll", "comdlg32.dll",
    "winspool.drv", "oledlg.dll", "olepro32.dll", "winmm.dll", "msvcrt.dll",
    "version.dll", "ws2_32.dll", "wsock32.dll", "wininet.dll", "iphlpapi.dll",
    "shlwapi.dll", "rpcrt4.dll", "setupapi.dll", "mfc42.dll", "ieframe.dll",
    "urlmon.dll", "shdocvw.dll", "ntdll.dll",
}

# 各模块读取的数据文件名。出现即记录，但存不存在另行核实。
# 只接受完整字面文件名：必须位于 NUL 之后，且中间不含 `%`。
# 否则 "%02d%02d%02d.dat" 会被截成 "02d.dat"，列成“缺失文件”只是噪声。
DATA_FILE_RE = re.compile(rb"\x00([A-Za-z0-9_]{1,40}\.(?:dat|Dat|DAT|lib|Lib|LIB|ini|s2g))\x00")

# 产品线标签。判据是版本资源里的公司名，不是文件名。
VENDOR_RULES = (
    ("金惠科技", "kingsoft-hnc"),
    ("大正科技", "dazheng"),
    ("KingWay", "kingway"),
    ("Matinsoft", "thirdparty-appface"),
    ("Pizzolato", "thirdparty-cximage"),
    ("Microsoft", "thirdparty-ms"),
    ("APPFACE", "thirdparty-appface"),
    ("hnc", "hnc-engine"),
)


def _decode(b: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1")


def _version_info(pe) -> dict:
    out: dict = {}
    try:
        for fi in pe.FileInfo or []:
            for entry in fi:
                if not hasattr(entry, "StringTable"):
                    continue
                for st in entry.StringTable:
                    for k, v in st.entries.items():
                        key = _decode(k)
                        if key in (
                            "CompanyName",
                            "ProductName",
                            "FileDescription",
                            "InternalName",
                        ):
                            out[key] = _decode(v).strip()
    except Exception:
        pass
    return out


def _vendor(info: dict) -> str:
    blob = " ".join(info.values())
    for needle, label in VENDOR_RULES:
        if needle in blob:
            return label
    return "unknown"


def _exports(pe) -> list:
    if not hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        return []
    return [e.name.decode("latin-1") for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name]


# OpenCV 类的导出名占了绝大多数（`xcore.dll` 单个就有 377 个 `Cv*`）。
# 全存下来会把 113 KB 的清单变成一本 OpenCV 符号表，掩盖真正的信号。
# 数量总是保留，名字只留非 OpenCV 的。
_NOISE_PREFIX = ("Cv", "cv", "??", "?Cv", "?Increase", "?PutC", "?Get")


def _interesting_exports(names: list) -> list:
    keep = [n for n in names if not n.startswith(_NOISE_PREFIX)]
    return keep


def _imports(pe) -> dict:
    """返回 {dll: [function, ...]}，只保留产品内部依赖。"""
    out: dict = {}
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
        dll = entry.dll.decode("latin-1")
        if dll.lower() in SYSTEM_DLLS:
            continue
        fns = [(im.name.decode("latin-1") if im.name else "ord%d" % im.ordinal)
               for im in entry.imports]
        out[dll] = fns
    return out


def _data_files(raw: bytes) -> list:
    names = set()
    for m in DATA_FILE_RE.finditer(raw):
        s = m.group(1).decode("latin-1")
        # 只看纯文件名，丢掉路径前缀
        base = s.replace("\\", "/").rsplit("/", 1)[-1]
        if len(base) >= 5:
            names.add(base)
    return sorted(names, key=str.lower)


def collect(legacy: Path) -> dict:
    modules = {}
    declared = set()

    paths = []
    for d in SCAN_DIRS:
        base = legacy / d
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if p.is_file() and p.suffix.lower() in (".exe", ".dll", ".sys", ".ocx"):
                paths.append(p)

    for p in paths:
        raw = p.read_bytes()
        rec = {
            "path": str(p.relative_to(legacy)).replace("\\", "/"),
            "size": len(raw),
            "sha256_12": hashlib.sha256(raw).hexdigest()[:12],
        }
        try:
            pe = pefile.PE(str(p), fast_load=True)
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"],
                ]
            )
        except Exception as exc:
            rec["error"] = type(exc).__name__
            modules[p.name] = rec
            continue

        exports = _exports(pe)
        info = _version_info(pe)
        rec.update(
            {
                "kind": "dll" if pe.FILE_HEADER.Characteristics & 0x2000 else "exe",
                "vendor": _vendor(info),
                "company": info.get("CompanyName", ""),
                "product": info.get("ProductName", ""),
                "description": info.get("FileDescription", ""),
                "export_count": len(exports),
                "exports": _interesting_exports(exports),
                "depends_on": _imports(pe),
                "data_files": _data_files(raw),
            }
        )
        declared.update(rec["data_files"])
        modules[p.name] = rec

    # 每个被引用的数据文件是否真的在树上
    present = {}
    for p in legacy.rglob("*"):
        if p.is_file():
            present.setdefault(p.name.lower(), []).append(
                str(p.relative_to(legacy)).replace("\\", "/")
            )
    declared_status = {
        name: present.get(name.lower(), []) for name in sorted(declared, key=str.lower)
    }

    # 反向依赖：谁用了谁
    reverse: dict = {}
    for name, rec in modules.items():
        for dep, fns in rec.get("depends_on", {}).items():
            reverse.setdefault(dep.lower(), []).append(
                {"consumer": name, "functions": fns}
            )

    return {
        "_note": "模块清单：只含结构、路径、依赖与文件名，不含词库条目原文。",
        "legacy_root": str(legacy.name),
        "module_count": len(modules),
        "modules": modules,
        "reverse_dependencies": reverse,
        "declared_data_files": declared_status,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--legacy", required=True, help="path to LH-installed-tree")
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args()

    report = collect(Path(args.legacy))

    print(f"modules: {report['module_count']}")
    print()
    print("== by vendor ==")
    by_vendor: dict = {}
    for rec in report["modules"].values():
        by_vendor.setdefault(rec.get("vendor", "?"), []).append(rec["path"].rsplit("/", 1)[-1])
    for v, names in sorted(by_vendor.items()):
        print(f"  {v:24s} {len(names):2d}  {', '.join(sorted(names))}")

    print()
    print("== declared data files ==")
    missing = []
    for name, where in report["declared_data_files"].items():
        if where:
            print(f"  {name:20s} present  ({len(where)} copy)")
        else:
            missing.append(name)
    if missing:
        print()
        print(f"  declared but absent from the tree ({len(missing)}):")
        for name in missing:
            readers = [
                m for m, rec in report["modules"].items()
                if name in rec.get("data_files", [])
            ]
            print(f"    {name:20s} referenced by {', '.join(readers)}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print()
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
