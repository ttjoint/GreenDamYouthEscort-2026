"""P29: time-control feature - what the original actually stores.

Reproduces analysis/probe/p29_time_control.txt. Read-only.

Sources: XNet2.exe strings, xnet2_lang.ini, and the product manual pages 18-19
(screenshots in the manual folder). The manual is evidence like any other; it is
cited as MANUAL rather than DISASM.
"""
import re
import sys
from pathlib import Path

EXE = Path(sys.argv[1] if len(sys.argv) > 1 else
           "LH-installed-tree/Windows/System32/XNet2.exe")
INI = Path(sys.argv[2] if len(sys.argv) > 2 else
           "LH-installed-tree/Windows/System32/xnet2_lang.ini")


def main() -> int:
    data = EXE.read_bytes()

    # -- the policy name (slot 24) in the reversed GBK name table
    print("== policy name table (reversed order, GBK) ==")
    for needle in ("允许上网时间控制", "自动更新时间", "自动抓屏间隔"):
        offset = data.find(needle.encode("gbk"))
        print(f"  {offset:#010x}: {needle}")

    # -- the AllowTime string and what follows it
    print("\n== 'AllowTime' and the grid header strings that follow ==")
    offset = data.find(b"AllowTime\x00")
    print(f"  'AllowTime' at {offset:#010x}")
    # The bytes after the string are GBK dialog labels: the grid's column and
    # row headers, matching the manual screenshots.
    blob = data[offset:offset + 0x90]
    decoded = blob.decode("gbk", errors="replace")
    for token in ("上午", "下午", "星期日", "星期一", "星期二", "星期三",
                  "星期四", "星期五", "星期六"):
        if token in decoded:
            at = offset + decoded.index(token)
            print(f"  {at:#010x}: {token}")

    # -- NTP servers, for the anti-tamper clock sync
    print("\n== NTP server addresses (clock tamper resistance) ==")
    ips = [m.group().decode() for m in
           re.finditer(rb"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", data)
           if data[m.start():m.start() + 1].isdigit()]
    # Keep the block near AllowTime only; other IPs are update servers.
    near = [ip for off, ip in
            ((m.start(), m.group().decode()) for m in
             re.finditer(rb"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", data))
            if 0x68200 <= off <= 0x68300]
    for ip in near:
        print(f"  {ip}")
    print(f"  ({len(near)} addresses in the block after AllowTime)")

    # -- UI strings from the language file
    print("\n== xnet2_lang.ini AllowTime dialog strings ==")
    text = INI.read_bytes().decode("gbk", errors="replace")
    for line in text.splitlines():
        if line.startswith("AllowTime0_"):
            print(f"  {line.strip()}")

    # -- what the manual shows
    print("\n== manual (花季护航使用手册-18.jpg / -19.jpg), method=MANUAL ==")
    print("""  - grid: 7 rows (星期日..星期六) x 24 columns (上午 12,1..11 / 下午 12,1..11)
  - header row reads 'D/H' - day vs hour
  - cells are either grey (deny) or green (allow); the legend is 允许
  - fig 18: default grid, all cells grey, 允许上网时间控制 unchecked
  - fig 19: 星期三 has three green cells; the checkbox is ticked
  - below the grid:
      允许上网时间控制          (the enable switch = policy slot 24)
      自动时间同步间隔（秒）= 10 (NTP refresh interval)
      切换提前5分钟预警         (warn 5 minutes before a transition)""")

    print("""
== conclusion ==
The feature is a 7x24 allow/deny grid, one cell per hour of the week, plus an
enable switch (slot 24), an NTP sync interval, and a 5-minute pre-transition
warning. The header strings (上午/下午/星期日..星期六) sit immediately after
'AllowTime' in the binary, matching the manual screenshots.

Where the 168 bits of grid state live is NOT recovered: sys.dat is 268 bytes and
every non-zero slot is in 3..24, none of which can hold a week of bits, and no
grid file exists in the installed tree. The honest statement is that the
reimplementation ports the semantics and defines its own storage. An earlier
draft of this probe guessed the dword after 'AllowTime' was a buffer length;
decoding the bytes as GBK shows they are the header strings, and that guess is
withdrawn.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
