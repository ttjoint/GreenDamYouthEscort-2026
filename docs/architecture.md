# Architecture

## Target

Windows 10 1809 or newer, x64. The original package ran on XP; reproducing
that is pointless (no TLS 1.2), and the mechanisms it relied on are gone.

### One language: Python throughout

The whole product is Python - detection engines and host alike. The split that
matters is not between languages but between **I/O and logic**:

| Layer | Package | I/O | Testable without Windows |
|---|---|---|---|
| engines | `src/green_dam` | forbidden by contract | yes |
| host | `host/` | allowed, Windows-only | no |

The engine keeps its zero-dependency, no-I/O property (``tests/test_contracts.py``
enforces it), so the interesting part still runs and tests anywhere. The host
holds every ``socket``, ``subprocess``, ``ctypes`` and ``pywin32`` call.

**This was .NET 8 until the host work started.** That choice was never justified
in this document; it was the conventional default for Windows desktop work. An
audit of what each module actually needs showed no requirement that .NET alone
satisfies (see ``docs/dependency-audit.md``), and staying in one language removes
the cross-language bridge, halves the deployment surface, and lets the UI be
HTML - which is both more modern than WinForms and far easier to iterate on.

### Deployment: nothing to install on the target

A deployment is an embeddable CPython plus the host and engine - roughly 20 MB,
no installer, no redistributable runtime. That constraint is why the UI is a
WebView2 shell rather than a Qt application: Qt would add 60-150 MB.

WebView2 ships with Windows 11 and with any Windows 10 machine that has seen
Edge, Teams or Office. It is *not* guaranteed on Windows 10, so the host degrades:

| Available | UI |
|---|---|
| WebView2 | native window via ``pywebview`` |
| Edge only | ``msedge --app=`` window, visually equivalent |
| neither | open the local page in the default browser |

All three serve the same HTML. The UI is written once.

The cost of this choice is stated rather than hidden: the WinRT OCR bindings for
Python are community-maintained and weaker than the .NET ones — which turned out
not to matter, because M5 is declined on product grounds (see below).

## Modules

```
┌──────────────────────────────────────────────────────────────┐
│ M9  console + tray (single machine, no server)               │
├──────────────────────────────────────────────────────────────┤
│ M8  policy engine — combines detector verdicts, schedules    │
├───────────┬───────────┬──────────┬──────────┬───────────────┤
│ M1 data   │ M2 network│ M3 text  │ M4 image │ (M5 declined) │  detector faces
├───────────┴───────────┴──────────┴──────────┴───────────────┤
│ M6 application control                                       │
├──────────────────────────────────────────────────────────────┤
│ M7  infrastructure — config, logging, IPC, service, hardening│
└──────────────────────────────────────────────────────────────┘
```

| Module | Responsibility | Replaces |
|---|---|---|
| **M1 data** | load JSON, build index structures, category model | the `.dat` word lists |
| **M2 network** | staged interception + browser extension, see below | `dbfilter.dll` LSP + `Surfgd.dll` |
| **M3 text** | tokenise, judge context, two interchangeable backends | `SentenceObj.dll` HNC |
| **M4 image** | NSFW / face classification | `kwimage.dll` + OpenCV 1.x |
| ~~M5 screen~~ | *declined — see M5 below* | `IPGate.dll` GDI hooks |
| **M6 app control** | block named applications from the network | `kwselectinfopp.dll` |
| **M7 infrastructure** | service host, config, logging, change journal | assorted `gn.exe` parts |
| **M8 policy** | aggregate verdicts into allow / block / alert | `sys.dat` 67 slots |
| **M9 console** | parent UI, logs, scheduler | `XNet2.exe`, `looklog.exe` |

## M2: staged, and one stage deliberately declined

Content inspection needs plaintext. That is an arithmetic fact, not an
implementation detail — TLS exists to prevent it.

| Stage | Mechanism | Needs root CA | Sees content | Status |
|---|---|---|---|---|
| 1. domain / SNI | firewall + proxy host list | no | no | **done** |
| 2. plain HTTP | transparent proxy | no | yes | **done** |
| 3. HTTPS MITM | local root CA | **yes** | yes | **declined** |
| 4. image | on the stage-2 body | inherits | yes | **done** (optional model) |

### Why stage 3 is declined

By 2026 HTTPS is not optional for the web: Chrome's HTTPS-First is on by
default, HSTS preloading forces it for major sites, the `.app` and `.dev` TLDs
are HTTPS-only, and search result pages are HTTPS throughout. A filter that stops
at stage 2 sees host names and nothing else — for a *content* filter that is
functional abandonment, so the problem had to be solved. A local CA was the
obvious solution and it is not the one taken.

Three reasons, in order of weight:

1. **Scope.** A root CA decrypts *every* TLS connection on the machine — the
   parent's banking, the company VPN, medical records. The product's premise is
   "a parent protecting their own child", and that premise does not support that
   scope.
2. **Single point of failure.** The CA private key lives on the target machine.
   A leaked key is worse than no interception at all, and nothing about this
   project makes key custody easier.
3. **It contradicts the logging promise.** ``eventlog.py`` states that no record
   ever contains a body or a full host path. That must remain true under MITM,
   which means every new code path has to re-prove it.

**The replacement is a browser extension installed by policy.** It receives the
*decrypted DOM* — not the TLS byte stream — so no certificate, no key, and no
change to the system trust store. Uninstall is a registry value or a file
(``host/browsers.py``, ``host/policy/``).

The decisive difference is granularity. Domain and URL blocking can only allow or
deny a whole page, and the two cases that matter cannot be handled that way:

* a forum or social platform where NSFW and ordinary content share a page — the
  right action is to hide that post, not the site;
* adult sites that rotate domains or are reached by bare IP — a list cannot
  predict them, and maintaining one needs network updates, which the product
  does not have.

Judging content rather than addresses makes all of that moot.

Known limits, stated up front: stage 1 sees only the host name, so a site served
over HTTPS whose content is objectionable is not blocked at the network layer;
QUIC/HTTP3 is UDP and must be disabled or handled separately; the extension
covers the browsers it is installed into and no others, which the installer
reports rather than hides.

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

## M5: declined, and why

The original hooked GDI `TextOut`/`ExtTextOut` inline. DirectWrite now does most
rendering, so such a hook sees a shrinking fraction of on-screen text, and a
32-bit hook cannot reach 64-bit processes anyway. The obvious replacement is UI
Automation plus `Windows.Media.Ocr`, which needs no injection and is supported.

**It is not being built.** Three reasons, the second of which is decisive:

1. **Screen OCR is screen recording.** Even if only the verdict is stored, the
   intermediate frames pass through memory. A product that promises never to
   keep a body or a full path cannot then hold a picture of the desktop.
2. **It changes what is in scope.** Screen capture governs *what is on the
   display*, not *what is on the network*. The parent's banking, work documents
   and private conversations all fall inside it. This product has no basis for
   looking at those.
3. **It would not survive contact with a user.** Full-screen OCR costs 100 ms or
   more per frame; running continuously makes the machine hot and the fans
   audible. A feature the user turns off within a day is not a feature.

DOM-level filtering is the same goal done correctly: it sees the page, not the
desktop, and it is cheap enough to run continuously. The original's GDI hook was
an artefact of 2009 rendering; the modern equivalent of "read what is displayed"
is "read the document the browser rendered", and that is the extension.

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
install time and rolled back in reverse: remove firewall rules, remove the
browser policy values, delete the service, clean registry, delete files, restore
settings. A `--dry-run` lists what would be removed. No forced reboot.

Removal deletes only the values this product wrote. On the machine this was
written on, `SOFTWARE\Policies\Microsoft\Edge` already contained
`StartupBoostEnabled`, which this product never set — deleting the policy key
wholesale would have removed someone else's setting, so each removal is a value
delete and a key is removed only when it ends up empty.

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
| P2 | M2 stages 1–2 (proxy + firewall) | **done** |
| P3 | M3 both backends | **done** |
| P4 | M4 ONNX image classification | **done** (optional model, `docs/image-backend.md`) |
| P5 | M5 UIA + OCR | **declined** (see M5 above) |
| P6 | service packaging, M9 console, hardening | service + console remain |
| **H** | **host layer** | **done** except service and console (``docs/host-layer.md``) |

Host work happens in ``host/``, which is outside ``src/`` because ``src/`` is
under the no-I/O contract. The engine does not change to accommodate the host;
the host adapts to the engine's interfaces (``transport.plan``,
``imagepath.ImageBackend``, ``Filter``).

Note on P4: the reverse-engineering work recovered the original's image
judgement (``analysis/probe/p27``, ``p28``) and two of those paths are ported in
``imaging_checkex``/``imaging_skin``. That is archaeology, not the deliverable.

The deliverable is ``host/imaging/``: a Marqo 384 ViT-tiny classifier exported to
ONNX, Apache-2.0, 21.4 MB, measured at 90 ms per frame on one core. It ships as an
optional package because ``onnxruntime`` alone is 38 MB — larger than the model.

The ported vendor code is kept because it records what the original did, and
because it is still worth running: it costs nothing and it is honest about not
being a classifier. What it is *not* is a substitute. Its skin test is
``R > G > B`` plus a saturation floor, which means it can never fire on a
greyscale image at all, and it fires on a beach or a wooden floor. See
``docs/image-backend.md`` for the measurements.
