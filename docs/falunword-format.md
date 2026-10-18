# `FalunWord.lib`: full structural decode

This note records how the semantic lexicon container was actually decoded, so
the result can be checked rather than trusted. It also records what the file
does **not** contain, which matters more than what it does.

## Method: decode from the consumer, not from the bytes

Pattern-matching the blob produced several plausible but wrong layouts. The
layout below came from disassembling the code that reads it, in
`Windows/SentenceObj.dll` (`CFWordLib`):

| Function | Address | Role |
|---|---|---|
| `CFWordLib::Ini` | `0x10004BE0` | decides which files to load |
| `BuildWordIndexFromFile` | `0x100049C0` | text-list index builder (not our path) |
| `CFWordLib::LoadLib` | `0x100022D0` | **reads `FalunWord.lib`** |
| `CFWordLib::SaveLib` | `0x100022D0` | writes the same layout |
| `CFWordLib::SearchChar` | `0x10002500` | binary search, `shl ecx,4` → 16-byte stride |
| `CFWordLib::GetWordFanDong` | `0x10002D80` | returns `sense[i*16 + 0x08]` |
| `CFWordLib::GetWordHongHei` | `0x10002D50` | returns `sense[i*16 + 0x0C]` |

`LoadLib` performs this `fread` sequence (CRT wrapper at `0x1003719d`):

```
fread(&n1, 4, 1, fp)          -> +0x148 : chain-node count
fread(buf1, 16, n1, fp)       -> +0x140 : chain-node array
fread(buf2, 16, 0xFFFF, fp)   -> +0x14c : fixed scratch table
fread(&n2, 4, 1, fp)          -> +0x130 : sense-record count
fread(buf3, 16, n2, fp)       -> +0x128 : sense-record array
fread(&n3, 4, 1, fp)          -> +0x13c : char-pool count
fread(buf4, 2, n3, fp)        -> +0x134 : UTF-16 char pool
```

`SaveLib` mirrors it field for field, so this is a read/write pair, not a
one-off import format.

## Why the segmentation is certain

With `n1 = 272,920`, `n2 = 4,693`, `n3 = 36,943` the sections consume
**5,564,266 of 5,564,271 bytes**. The remaining **5 bytes are `HNCIT`**.
Nothing is left over and nothing is guessed:

```
4 + 272920*16 + 65535*16 + 4 + 4693*16 + 4 + 36943*2 = 5,564,266
5,564,271 - 5,564,266 = 5            == len("HNCIT")
```

`falunword.Layout.verify()` re-checks this every run and the importer refuses
to continue if it fails, so a change in interpretation cannot silently produce
wrong terms.

## Chain nodes: `next` is a character path

Each 16-byte node is four little-endian `uint32`:

| Offset | Field | Meaning |
|---|---|---|
| `+0x00` | `code` | Unicode code point |
| `+0x04` | `next` | index of the next character **in the same term**, 0 = end |
| `+0x08` | `children` | number of branching continuations, 0 = leaf |
| `+0x0C` | `sense_id` | index into the sense table, or `0xFFFFFFFE` / `0xFFFFFFFF` |

The trap is `next`. Reading it as a sibling list also produces
plausible-looking Chinese output, which is why two earlier attempts looked
right and were wrong. It is a path: walking it spells the whole term.

```
node 0 -> 8 -> 9 -> 10 -> 11 -> 12   ==   '6' '1' '0' '"' '歹' '徒'
```

`SearchChar` confirms the 16-byte stride via `shl ecx, 4`.

## Sense records

```
[0] char_offset   index into the UTF-16 pool
[1] fan_dong      **garbage** - see below
[2] hong_hei      0..7 severity class
[3] char_count    length in pool
```

`GetWordFanDong` / `GetWordHongHei` index this table by `i * 16`, so the
layout is confirmed from both directions.

### `hong_hei` is real data

Grouping terms by `hong_hei` yields thematically coherent buckets, which is
the evidence that the field is meaningful:

| `hong_hei` | entries | content |
|---|---|---|
| 0 | 4,177 | general corpus |
| 1 | 347 | dates and codes (`4.25`, `5.13`, `FA--`) |
| 2 | 4 | `大法弟子`, `非法判刑`, `洪法教功` |
| 3 | 45 | `610基`, `[江泽`, `龚家湾洗` |
| 4 | 41 | `X教组织`, `[法轮功`, `falu` |
| 5 | 21 | `《法轮功` |
| 6 | 53 | `大法弟子`, `大法学员`, `澳州学员` |
| 7 | 5 | `明慧网消`, `大纪元新`, `纪元文化` |

### `fan_dong` is uninitialised memory - do not use it

All 4,693 entries hold `0xCDCD00xx`. `0xCDCDCDCD` is the MSVC debug
uninitialised-fill pattern, and the low byte merely increments per record:
it is leftover stack content, not a classification.

The reason is in `CFWordLib::Ini`:

```
sprintf(p, "%s\\FWL.txt", dir);         if (!exists) return -1;
sprintf(p, "%s\\FWL.tmp", dir);
BuildWordIndexFromFile(FWL.tmp);
sprintf(p, "%s\\FMean.tmp", dir);       if (!exists) return -1;   <-- required
BuildWordMeanFromFile(FMean.tmp);       <-- fills fan_dong via InsertWordMean
sprintf(p, "%s\\FalunWord.lib", dir);
SaveLib(); LoadLib();
```

`FWL.txt`, `FWL.tmp` and `FMean.tmp` **are not in the shipped package**.
`FMean.tmp` is the only source that populates the per-word judgement field
(`InsertWordMean` has exactly one caller, and `BuildWordMeanFromFile` exactly
one). Without it, `fan_dong` was never written.

**Consequence for the rewrite:** the shipped `FalunWord.lib` gives us terms,
a sense table, and a 0..7 severity class - but **not** the 1/2 normal/blocked
flag the runtime decision uses at `[item+0x28]`. That mapping has to be
rebuilt from the term semantics rather than recovered from this file.

## Two independent views cross-check

The chain trie and the sense table are populated from the same source but read
by different code paths. They agree:

| `sense_id` | sense text | chain term(s) |
|---|---|---|
| 0 | `恶党书` | `书记`, `書記` |
| 1 | `我坐在` | `坐在他的小腿上` |
| 3 | `"610` | `610"歹徒` |
| 4 | `"` | `大法弟子"` |

Every recovered `sense_id` lies in `[0, n2)`; 4,509 of the 4,693 sense
records are referenced by at least one chain. That agreement is what settled
the `next` interpretation.

## Result

- 142,742 terms recovered from the chain trie
- 4,693 sense records, each with `hong_hei` and pool offsets
- Simplified and traditional forms appear as sibling terms (`法轮功` / `法輪功`)
- 14 category ids survive as `sense_id` on terminal nodes
