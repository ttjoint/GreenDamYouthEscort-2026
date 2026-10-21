#!/usr/bin/env python3
"""P05 - Verify every entry in the REVERSE-ENGINEERING IOC lists against real bytes.

For each claimed string, search the claimed module both as ASCII/GBK (narrow)
and as UTF-16LE (wide). Report FOUND / NOT-FOUND with the file offset.

A claim that cannot be found is REFUTED, regardless of how confidently it was
written. This is the direct antidote to fabricated extraction lists.

Usage: python ANALYSIS/probe/p05_verify_iocs.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TREE = os.path.join(ROOT, "LH-installed-tree")


def find_module(name):
    """Locate a module anywhere in the tree by basename, case-insensitive."""
    low = name.lower()
    for dirpath, _dn, filenames in os.walk(TREE):
        for fn in filenames:
            if fn.lower() == low:
                return os.path.join(dirpath, fn)
    return None


def load_list(path):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line)
    return out


def search(data, needle, case_insensitive=False):
    """Return list of (encoding, offset) hits.

    NOTE: these process-name lists are stored MIXED CASE in the binary
    (e.g. '\\Regmon.exe', '\\Tcpview.exe', '\\tdimon.EXE'), so a case-insensitive
    pass must lowercase BOTH sides. Lowercasing only the needle was a real bug
    in an earlier revision of this probe and produced three false NOT-FOUNDs.
    Entries in the .txt lists omit the leading backslash that the binary has.
    """
    hits = []
    for enc, enc_name in (("latin1", "narrow"), ("gbk", "gbk")):
        try:
            probe = needle.encode(enc)
        except UnicodeEncodeError:
            continue
        hay = data
        if case_insensitive:
            probe = probe.lower()
            hay = data.lower()
        idx = hay.find(probe)
        if idx >= 0:
            hits.append((enc_name, idx))
    try:
        probe = needle.encode("utf-16-le")
        idx = data.find(probe)
        if idx >= 0:
            hits.append(("wide", idx))
    except UnicodeEncodeError:
        pass
    return hits


def verify(label, module, entries, case_insensitive=False):
    path = find_module(module)
    print(f"\n=== {label}  ({module}) ===")
    if not path:
        print(f"  !! module not found: {module}")
        return
    data = open(path, "rb").read()
    print(f"  module: {os.path.relpath(path, ROOT)}  {len(data)} B")
    ok = miss = 0
    for e in entries:
        hits = search(data, e, case_insensitive=case_insensitive)
        if hits:
            ok += 1
            desc = ", ".join(f"{enc}@{off:#x}" for enc, off in hits)
            print(f"  FOUND     {e[:60]!r}  [{desc}]")
        else:
            miss += 1
            print(f"  NOT-FOUND {e[:60]!r}")
    print(f"  --> {ok} found / {miss} missing of {len(entries)}")
    return ok, miss


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    re_dir = os.path.join(ROOT, "REVERSE-ENGINEERING")

    verify("注入目标进程名单", "InjLib32.dll",
           load_list(os.path.join(re_dir, "injlib-injection-targets.txt")),
           case_insensitive=True)
    verify("进程终止名单", "Handler.dll",
           load_list(os.path.join(re_dir, "handler-kill-list.txt")),
           case_insensitive=True)
    verify("硬编码 URL", "XNet2.exe",
           load_list(os.path.join(re_dir, "ioc-urls.txt")))
    verify("硬编码 IP", "XNet2.exe",
           load_list(os.path.join(re_dir, "ioc-ips.txt")))
    verify("硬编码注册表路径", "XNet2.exe",
           load_list(os.path.join(re_dir, "ioc-registry.txt")))


if __name__ == "__main__":
    main()
