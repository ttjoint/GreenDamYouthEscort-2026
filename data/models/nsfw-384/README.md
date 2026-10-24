# nsfw-384 — image classifier weights

## What this file is

`model.onnx` is the ONNX export of **Marqo/nsfw-image-detection-384**, used by
`host/imaging/` to judge whether an image is sexual content. It is the only
machine-learning model in the product.

| | |
|---|---|
| Base model | `Marqo/nsfw-image-detection-384` |
| Upstream licence | **Apache-2.0** |
| ONNX export | `KanariKanaru/nsfw-image-detection-384-onnx` |
| Opset | 17, float32 |
| Size | 22,450,633 bytes |
| SHA-256 | `e9350e576608afe4b57a089ffeb0ebafa1389cdcea4882dd61df28c45f1c24d2` |
| Input | `input` — `[1, 3, 384, 384]` float32 |
| Output | `logits` — `[1, 2]` float32 |

The upstream repository states the ONNX file is a *format conversion, not a
newly trained model*, so the licence chain is unbroken: Apache-2.0 all the way
down. That matters because this file is redistributed inside the installer.

## Why it is in the repository

The product is offline by design: a parent may install it on a machine that
never reaches the internet, and image filtering that requires a download is
image filtering that does not work. So the weights ship inside the deployment.

They are **not committed to git** — 22 MB in a source tree is a mistake, and git
is not a distribution channel. `data/models/` is in `.gitignore`. The packer
treats a missing model as a build error rather than a warning, because a
deployment without it silently enforces nothing for images.

To place the file on a build machine, download it once from the upstream ONNX
export and verify the hash above:

```bash
mkdir -p data/models/nsfw-384
# place model.onnx here, then:
python -c "import hashlib;print(hashlib.sha256(open('data/models/nsfw-384/model.onnx','rb').read()).hexdigest())"
```

## Why this model and not another

Full comparison in `docs/image-backend.md` §1. The short version — every
candidate was filtered on licence first, then size:

| Model | Licence | Size | Outcome |
|---|---|---|---|
| Falconsai/nsfw_image_detection | Apache-2.0 | 327 MB | too large |
| AdamCodd/vit-base-nsfw-detector | Apache-2.0 | 328 MB (int8 84 MB) | too large |
| Freepik/nsfw_image_detector | MIT | 165 MB | too large |
| giacomoarienti/nsfw-classifier | CC-BY-NC-ND-4.0 | — | **licence: no commercial use, no modification** |
| NudeNet v3.4 | AGPL-3.0 | 11.6 MB | **licence: copyleft** |
| **Marqo/nsfw-image-detection-384** | **Apache-2.0** | **21.4 MB** | **chosen** |

## Measured behaviour

From `docs/image-backend.md` §2, measured on real photographs rather than
synthetic frames:

- Real photographs score 0.046–0.078 — well below the 0.5 threshold.
- A flat black frame scores 0.36; skin-tone fills scored 0.54. This is why
  `host/imaging/score_image` refuses low-entropy frames instead of scoring them:
  a nearly uniform image carries no information, and the model's answer on one
  is noise, not evidence.

The refusal is deliberate and is the project's rule throughout: a frame that
cannot be judged reports `assessed: false`, never a zero. "Not looked at" must
never read as "clean".

## Licence text

Apache License 2.0. The full text is available at
<https://www.apache.org/licenses/LICENSE-2.0> and is included with the
deployment as `docs/dependency-audit.md` records the dependency set. Apache-2.0
requires that this notice and the licence travel with the redistributed weights,
which is why this file is shipped beside the model rather than kept in `docs/`.
