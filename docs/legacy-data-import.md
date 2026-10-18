# Legacy data import

The 2009-era product shipped its policy state, filter lists and semantic
lexicon in opaque binary blobs. Those blobs are the only genuinely valuable
part of the original package: the mechanisms are obsolete (see
`docs/win11-migration.md`), but the *data* is not.

This directory holds that data in plain JSON, produced once from the legacy
install tree and then owned entirely by this project. Nothing here is read
back from the old binaries at runtime.

## How the import works

```
legacy install tree  ──►  tools/import_legacy.py  ──►  data/legacy-imported/*.json
     (not shipped)
```

The legacy tree is **not** part of this repository. To regenerate the data
you must supply your own unpacked copy:

```bash
python tools/import_legacy.py --legacy /path/to/tree
```

The importer is deterministic: same input, same bytes out. Every output file
records the SHA-256 of the blob it came from, so a future reader can prove
which original produced which JSON.

## What was recovered

| Output | Source | Count | Status |
|---|---|---|---|
| `policy.json` | `sys.dat` | 67 slots | array decoded, most slot meanings unknown |
| `categories.json` | `wfile.dat` | 5 categories | complete |
| `trust-urls.json` | `TrustUrl.dat` | 1,177 domains | complete |
| `rules/adwfil.json` | `adwfil.dat` | 2,003 rules → 3,618 terms | complete |
| `rules/adwapp.json` | `adwapp.dat` | 6,856 rules → 6,856 terms | complete |
| `rules/iawfil.json` | `iawfil.dat` | 113 rules → 334 terms | complete |
| `rules/lgwfil.json` | `lgwfil.dat` | 54 rules → 170 terms | complete |
| `rules/vgamfil.json` | `vgamfil.dat` | 117 rules → 207 terms | complete |
| `im-targets.json` | `kwselectinfopp.dll` | 8 applications | complete |
| `falun-terms.json` | `FalunWord.lib` | 142,742 terms | complete |
| `summary.json` | — | — | index of the above |

## The word-list obfuscation

`TrustUrl.dat`, `wfile.dat` and the five `*fil.dat` / `adwapp.dat` files are
not encrypted. They use a fixed 2:1 expansion over a 64-symbol alphabet:

- Each plaintext byte becomes **two** symbols drawn from `0x30..0x6F`.
- The pair forms a 12-bit value, of which only 8 bits carry data. Two data
  bits are inverted and four bits are fixed padding — so there is **no key
  material at all**. It is a lookup-table rewrite, not a cipher.
- `CRLF` is preserved verbatim and payload text is **GBK**.

`tools/legacy_wordlist_codec.py` implements both directions, which lets the
tests round-trip arbitrary input instead of trusting a golden file.

## The filter rule grammar

The rule lists use a small bracket DSL. Two delimiter flavours appear and are
equivalent: `[...]` and `{...}`.

```
[literal]              exact / substring match
[+token]               token is a prefix
[=token]               token is a suffix
[+,=][a,b]             both directions: a-prefix, a-suffix, b-prefix, b-suffix
[,x,y][z]              fragments concatenated left to right
```

The parser expands each line into concrete match terms and **keeps the
direction per term**, because one line can mix directions. Lines that do not
fit the grammar would be preserved verbatim in `raw` rather than dropped; in
the shipped data every line parses.

## The semantic lexicon (`FalunWord.lib`)

This was the hardest part, and it was cracked from the **consumer side**, not
by pattern-matching the file. Disassembling `CFWordLib::LoadLib` in
`SentenceObj.dll` gives the exact read sequence:

```
fread(&n1, 4, 1, fp)            n1 = 272,920   chain nodes
fread(buf1, 16, n1, fp)                        chain-node array
fread(buf2, 16, 0xFFFF, fp)                    fixed scratch table
fread(&n2, 4, 1, fp)            n2 =   4,693   sense records
fread(buf3, 16, n2, fp)                        sense-record array
fread(&n3, 4, 1, fp)            n3 =  36,943   UTF-16 pool
fread(buf4, 2, n3, fp)                         character pool
```

Those sizes consume the file down to a **5-byte `HNCIT` trailer** and match
the real length exactly. That closure is the proof the segmentation is right;
`falunword.Layout.verify()` re-checks it on every run and the importer refuses
to proceed if it fails.

Chain nodes are four `uint32`:

```
[0] code      Unicode code point
[1] next      index of the following character IN THE SAME TERM, 0 = end
[2] children  number of branching continuations, 0 = leaf
[3] sentinel  0xFFFFFFFE index node / 0xFFFFFFFF padding / else sense count
```

The crucial detail: **`next` is a character path, not a sibling list.** Walk
it from a chain start and you spell a whole term:

```
node 0 → 8 → 9 → 10 → 11 → 12      '6' '1' '0' '"' '歹' '徒'
```

Misreading `next` as a sibling pointer produces plausible-looking but wrong
output, which is exactly the trap this file sets. Two independent views —
the chain trie and the offset/length sense table — agree on terms such as
`610歹` and `610"歹徒`, and that cross-check is what settled it.

## Policy slots are not fully understood

`sys.dat` is 67 little-endian `uint32` slots and `xconfigs.dat` is a byte-for-byte
duplicate. Only **index 17** has a confirmed meaning (`allow auto shutdown`,
established from a shipped log sample). The rest are exported as a raw array
deliberately: the new implementation should assign meanings on purpose rather
than inherit guesses from the old UI. Do not treat unknown slots as settled.
