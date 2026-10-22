# Architecture

## Target

Windows 10 1809 or newer, x64. The original package ran on XP; reproducing
that is pointless (no TLS 1.2), and the mechanisms it relied on are gone.

The detection engines are written in Python and are platform-independent. The
host — service, network interception, UI automation, console — is .NET 8 and
Windows-only. This split lets the interesting part be tested anywhere while
keeping the OS integration native.

A Windows 7 / 8.1 build is possible by dropping to .NET Framework 4.8 and
replacing WinRT OCR with Tesseract, at the cost of ~30 MB and worse OCR. Not
a current goal.

## Modules

```
┌──────────────────────────────────────────────────────────────┐
│ M9  console + tray (single machine, no server)               │
├──────────────────────────────────────────────────────────────┤
│ M8  policy engine — combines detector verdicts, schedules    │
├───────────┬───────────┬──────────┬──────────┬───────────────┤
│ M1 data   │ M2 network│ M3 text  │ M4 image │ M5 screen     │  detector faces
├───────────┴───────────┴──────────┴──────────┴───────────────┤
│ M6 application control                                       │
├──────────────────────────────────────────────────────────────┤
│ M7  infrastructure — config, logging, IPC, service, hardening│
└──────────────────────────────────────────────────────────────┘
```

| Module | Responsibility | Replaces |
|---|---|---|
| **M1 data** | load JSON, build index structures, category model | the `.dat` word lists |
| **M2 network** | four-stage interception, see below | `dbfilter.dll` LSP + `Surfgd.dll` |
| **M3 text** | tokenise, judge context, two interchangeable backends | `SentenceObj.dll` HNC |
| **M4 image** | NSFW / face classification | `kwimage.dll` + OpenCV 1.x |
| **M5 screen** | text extraction from the display | `IPGate.dll` GDI hooks |
| **M6 app control** | block named applications from the network | `kwselectinfopp.dll` |
| **M7 infrastructure** | service host, config, logging, change journal | assorted `gn.exe` parts |
| **M8 policy** | aggregate verdicts into allow / block / alert | `sys.dat` 67 slots |
| **M9 console** | parent UI, logs, scheduler | `XNet2.exe`, `looklog.exe` |

## M2: four stages, degrading

Content inspection needs plaintext. That is an arithmetic fact, not an
implementation detail — TLS exists to prevent it. So interception is staged
and each stage is independently useful:

| Stage | Mechanism | Needs root CA | Sees content |
|---|---|---|---|
| 1. domain / SNI | firewall + proxy host list | no | no |
| 2. plain HTTP | transparent proxy | no | yes |
| 3. HTTPS | local MITM proxy | **yes** | yes |
| 4. image / OCR | on the stage-2/3 body | inherits | yes |

A local MITM proxy plus firewall rules replaces the LSP. It is auditable, needs
no kernel driver, and unlike the original it can actually inspect HTTPS — the
old LSP sat below TLS and saw only cleartext sockets, so stage 3 is a net gain
over the product being replaced.

Known limits, stated up front: certificate-pinned applications cannot be
decrypted; QUIC/HTTP3 is UDP and must be disabled or handled separately; users
will see certificate warnings for anything the CA does not cover.

## M3: two backends behind one interface

```
M3 ──► SemanticBackend.analyse(text) -> [Hit]
         ├── HncBackend    — reimplementation of the concept-network approach
         └── JiebaBackend  — statistical segmentation, reference / fallback
```

Both are selectable and the pair can be run A/B over the same corpus. This is
deliberate: a reimplementation that cannot be measured against a baseline is
just an opinion.

**What can and cannot be recovered.** The shipped lexicon yields 142,742 terms,
a sense table, and a 0..7 severity class (`hong_hei`). It does *not* yield the
per-word normal/blocked flag the original runtime consults — that lived in
`FMean.tmp`, which was never shipped (see `falunword-format.md`). The HNC
grammar proper lived in code we have not fully reversed.

So `HncBackend` is a reimplementation of the *approach* — term network, context
window, polarity — seeded with the recovered lexicon. It is not a
bit-faithful reproduction of the original engine, and this document says so
rather than implying otherwise.

## M5: why not hooks

The original hooked GDI `TextOut`/`ExtTextOut` inline. DirectWrite now does
most rendering, so such a hook sees a shrinking fraction of on-screen text, and
a 32-bit hook cannot reach 64-bit processes anyway. UI Automation plus
`Windows.Media.Ocr` covers more, requires no injection, and is supported.

## M7: protection and uninstall

Exiting or uninstalling requires the parent password. Enforcement:

- Windows service under `LocalSystem`; service ACL denies non-admin `STOP`
- recovery actions restart the service if it dies
- tray exit and the uninstaller both verify the password
- password stored as PBKDF2-HMAC-SHA256 with a random salt, never plaintext

**Explicitly out of scope**, because these are malware behaviours that trigger
AV and are indefensible regardless of intent:

- killing security tools by process name
- hiding processes or services
- blocking Task Manager
- registry keys that survive uninstall
- forced reboots

The honest boundary: an administrator can always disable this. Any product
claiming otherwise is using rootkit techniques. The goal is that a *non-admin
user* cannot casually bypass it — which is achievable, legal, and sufficient.

Uninstall is a strict inverse. Every change is journalled to a manifest at
install time and rolled back in reverse: remove firewall rules, revoke the CA,
delete the service, clean registry, delete files, restore settings. A
`--dry-run` lists what would be removed. No forced reboot.

## Deliberate omissions

Not carried over, for the reasons in the original product's security record:
global keyboard/mouse hooks, DLL injection into third-party processes, kernel
driver, plaintext HTTP delivery of executables, hardcoded passwords, plaintext
upload of collected identity numbers, writing into `%WinDir%\System32`,
hardware dongle checks.

## Phases

| Phase | Content | State |
|---|---|---|
| P0 | data migration | done |
| P1 | M1 + M8 skeleton: can answer "should this be blocked" | **done** |
| P2 | M2 stages 1–2 (proxy + firewall) | planning done, execution needs admin |
| P3 | M3 both backends | **done** |
| P4 | M4 ONNX image classification | **not started** (see note) |
| P5 | M5 UIA + OCR | not started |
| P6 | service packaging, M9 console, hardening | not started |

Note on P4: the reverse-engineering work recovered the original's image
judgement (``analysis/probe/p27``, ``p28``) and two of those paths are ported in
``imaging_checkex``/``imaging_skin``. That is archaeology, not the deliverable.
This document's M4 target is an ONNX NSFW classifier, and it has not begun. The
ported code is useful for understanding what the original did; it is not a
substitute for the model, and no part of the project claims it detects NSFW
content as well as the original did.
