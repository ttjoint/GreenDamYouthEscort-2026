"""从 Internet Archive 抓取黄曾阳《HNC 理论》在线全书。

来源（2011-08-28 快照）：
https://web.archive.org/web/20110828054807/http://www.hackchi.com/hnc/books/hnc/bookml.html

页面声明 `charset=gb_2312-80`，但 archive.org 回放时按 UTF-8 发送，直接
`requests.text` 会得到乱码。必须拿 **原始字节** 再按 GBK 解码。

用法：
    python tools/fetch_hnc_archive.py --out data/_hnc_archive
    python tools/fetch_hnc_archive.py --out ... --only book301,book302

产物：
    data/_hnc_archive/html/   原始 HTML（GBK 解码后重存为 UTF-8）
    data/_hnc_archive/text/   纯文本（去标签，段落保留）
    data/_hnc_archive/index.json  页面清单与标题
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SNAPSHOT = "20110828054807"
BASE = f"https://web.archive.org/web/{SNAPSHOT}"
ROOT = "http://www.hackchi.com/hnc/"

#: 已知入口页，用于发现全书页面
ENTRIES = ("books/hnc/bookml.html", "hnc52ml.html", "paperml.htm")

#: Wayback 注入的工具栏/脚本块，读正文前必须剥掉
_BOILER = re.compile(
    r"<!--\s*(?:BEGIN|END)\s+WAYBACK TOOLBAR INSERT.*?"
    r"|BEGIN\s+INTERNET\s+ARCHIVE\s+TOOLBAR.*?END\s+INTERNET\s+ARCHIVE\s+TOOLBAR"
    r"|<script\b.*?</script>",
    re.S | re.I,
)
_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</(?:p|div|tr|h[1-6]|blockquote|li|br)>", re.I)
#: “14 captures”、“13 Sep 2003 - 10 Aug 2007” 这类 Wayback 头信息
_CAPTURES_RE = re.compile(r"^\d+ captures?$")
_NAV_PROBE_RE = re.compile(
    r"^(?:\d{4}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$"
)


def fetch(path: str, *, retries: int = 4) -> bytes | None:
    """取一个页面。返回原始字节（未解码）。

    archive.org 经常在 TLS 握手阶段直接断连（`UNEXPECTED_EOF_WHILE_READING`），
    这不是 404，重试一次通常就能过。所以 retries 设得比一般抓取高。
    """
    url = f"{BASE}/{ROOT}{path}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (research; HNC theory archive)",
                    "Accept": "text/html,*/*",
                    "Connection": "close",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # 确实不存在，不浪费重试
            if attempt == retries - 1:
                print(f"  ! {path}: {exc}", file=sys.stderr)
                return None
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                print(f"  ! {path}: {exc}", file=sys.stderr)
                return None
        time.sleep(1.5 * (attempt + 1))
    return None


def decode(raw: bytes) -> str:
    """GBK 优先。archive.org 的包装层里有 UTF-8 片段，故逐段降级。"""
    for enc in ("gbk", "gb18030", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="replace")


def to_text(html: str) -> tuple[str, str]:
    """(标题, 正文)。去掉工具栏、脚本与标签。

    注意：Wayback 的工具栏在 `<body>** 内部**，所以先按 `<body>` 取正文
    还不够，必须从 `END WAYBACK TOOLBAR INSERT` 之后开始。早期版本只剥
    了 `<!-- BEGIN ... -->`，结果每页开头都残留“19 captures / TIMESTAMPS”
    一大段导航文字。
    """
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    title = _TAG.sub("", m.group(1)).strip() if m else ""

    body = html
    b = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    if b:
        body = b.group(1)
    end = body.find("END WAYBACK TOOLBAR INSERT")
    if end >= 0:
        body = body[end + len("END WAYBACK TOOLBAR INSERT") :]
        body = body.lstrip(" ->\n\t")
    end = body.find("End Wayback Rewrite JS Include")
    if end >= 0:
        body = body[end + len("End Wayback Rewrite JS Include") :]
        body = body.lstrip(" ->\n\t")
    # 旧版 Wayback 工具栏（2003–2007 快照）没有 BEGIN/END INSERT 注释，
    # 只留下“N captures / TIMESTAMPS / The Wayback Machine - url -->”
    # 一段文本。逐行扔掉这些行，直到出现真正的正文。
    lines = body.split("\n")
    while lines:
        probe = _TAG.sub("", lines[0]).strip()
        if (
            not probe
            or probe == "-->"
            or probe.startswith("The Wayback Machine")
            or probe.startswith("About this capture")
            or probe in ("TIMESTAMPS", "success", "fail")
            or _CAPTURES_RE.match(probe)
            or _NAV_PROBE_RE.match(probe)
        ):
            lines.pop(0)
            continue
        break
    body = "\n".join(lines)
    body = _BOILER.sub(" ", body)
    body = _BLOCK.sub("\n", body)
    body = _TAG.sub("", body)
    body = body.replace("&nbsp;", " ").replace("&amp;", "&")
    body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    lines = [ln.strip() for ln in body.split("\n")]
    lines = [ln for ln in lines if ln]
    return title, "\n".join(lines)


def discover(entries: tuple[str, ...]) -> list[str]:
    """从入口页里收集同站链接（限定在 hnc 书籍/论文目录内）。"""
    seen: dict[str, None] = {}
    queue = list(entries)
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        raw = fetch(path)
        if raw is None:
            seen[path] = None  # 记下但无链接可找
            continue
        seen[path] = None
        html = decode(raw)
        for m in re.finditer(r'href="([^"#]+)"', html, re.I):
            href = m.group(1)
            if "web.archive.org" in href or href.startswith(("mailto:", "javascript:")):
                continue
            if href.startswith("http://www.hackchi.com/hnc/"):
                href = href[len(ROOT):]
            elif href.startswith("/web/"):
                continue
            href = urllib.parse.urljoin(path, href)
            if href.startswith("../") or not href.lower().endswith(
                (".htm", ".html")
            ):
                continue
            # 规范到 ROOT 相对路径：archive 上的路径可能带前缀
            for prefix in ("books/hnc/", "papers/hnc/", "books/", "papers/"):
                if href.startswith(prefix):
                    break
            else:
                if "/hnc/" in href:
                    href = href.split("/hnc/", 1)[1]
                else:
                    continue
            if href not in seen:
                queue.append(href)
        time.sleep(0.3)
    return list(seen)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default="", help="逗号分隔的页面名，调试用")
    ap.add_argument("--discover", action="store_true", help="先爬全站链接")
    ap.add_argument("--resume", action="store_true",
                    help="跳过 text/ 里已有非空文本的页面")
    ap.add_argument("--plan", type=Path,
                    help="从文件读页面清单（一行一个，用于断点续传）")
    args = ap.parse_args(argv)

    html_dir = args.out / "html"
    text_dir = args.out / "text"
    html_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    if args.plan:
        pages = [ln.strip() for ln in args.plan.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
    elif args.only:
        pages = [p.strip() for p in args.only.split(",") if p.strip()]
    elif args.discover:
        print("discovering…", file=sys.stderr)
        pages = discover(ENTRIES)
        (args.out / "plan.txt").write_text("\n".join(pages), encoding="utf-8")
    else:
        pages = list(ENTRIES)

    index = []
    skipped = 0
    for i, path in enumerate(pages, 1):
        stem = path.replace("/", "_")
        txt_path = text_dir / f"{stem}.txt"
        if args.resume and txt_path.exists() and txt_path.stat().st_size > 200:
            skipped += 1
            continue
        raw = fetch(path)
        if raw is None:
            continue
        html = decode(raw)
        title, text = to_text(html)
        (html_dir / f"{stem}.html").write_text(html, encoding="utf-8")
        txt_path.write_text(text, encoding="utf-8")
        index.append({"path": path, "title": title, "chars": len(text)})
        print(f"[{i}/{len(pages)}] {path}  {title}  ({len(text)} 字)")

    # 索引总是从磁盘重建，保证断点续传后依然完整
    index = []
    for p in sorted(text_dir.glob("*.txt")):
        text = p.read_text(encoding="utf-8")
        stem = p.stem
        index.append({"path": stem.replace("_", "/", 1) if "_" in stem else stem,
                      "file": p.name, "chars": len(text)})
    (args.out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n新增 {len(index) - skipped if skipped else len(index)} 页、跳过 {skipped} 页"
          f"、共 {len(index)} 页 -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
