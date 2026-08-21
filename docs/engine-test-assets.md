# Engine test assets

Where every asset the Phase 2 engine tests depend on comes from, and what breaks
if it is wrong. Three assets, three mechanisms: a link for the weights, a
checked-in JSON file for the settings, a sealed subprocess for the runtime.

---

## 1. The weight set: a link, not a copy

`visoswap/models/models_data.py` line 6:

```python
models_dir = './model_assets'
```

That string is vendored verbatim and stays **hardcoded until Phase 4**, whose
model bootstrap makes it env-driven. Every asset path in the engine is built
from it by f-string, at import or at call time. There is no override before then.

Two consequences, both load-bearing rather than incidental:

- **The working directory of whatever runs engine code is part of the
  contract.** A relative `./model_assets` resolves against the process CWD. Run
  engine code from anywhere but the repository root and the engine looks for
  weights somewhere else entirely — and, worse, `ModelsProcessor` defaults to
  TensorRT, whose execution-provider options write a cache into a *relative*
  `tensorrt-engines/` directory. From the wrong CWD that writes into the
  read-only source tree. Always run from the repository root.
- **The directory must resolve when `ModelsProcessor` is *constructed*, not when
  a frame is swapped.** `ModelsProcessor.__init__` builds all seven
  sub-processors, and `FaceEditors.__init__` opens
  `liveportrait_onnx/lip_array.pkl` inside a `try`/`except FileNotFoundError`
  that leaves `lp_lip_array` as `None`. A missing models directory therefore does
  not raise at construction — it silently produces a degraded processor. That
  silent `None` is the failure mode of getting this wrong.

`tools/link_model_assets.py` creates a directory link at the repository root
named `model_assets`, pointing at the real weight set. This is the mechanism by
which ~12GB is reached with **zero bytes copied and nothing downloaded**.

Copying would be wrong for a second reason too: `ModelsProcessor.load_model`
does **not** auto-download. The download call is commented out in the vendored
file, so a missing weight raises out of `onnxruntime.InferenceSession` rather
than starting a 12GB fetch. There is no self-healing path; the link is the path.

### Source resolution

| Order | Source |
|-------|--------|
| 1 | `VISOSWAP_MODEL_ASSETS_SOURCE` environment variable |
| 2 | `D:/Visomaster/model_assets` |

The resolved source is printed on every run, before anything can fail, so a link
pointed at the wrong tree is diagnosable from the log alone.

### Usage

```
python tools/link_model_assets.py           # create the link
python tools/link_model_assets.py --check   # verify link + probe set
```

On Windows the link is a **junction** (`mklink /J`), not a symbolic link:
junctions require no administrator rights and no developer-mode opt-in. On POSIX
it is `os.symlink`. The mechanism is chosen from `os.name`, never guessed from
the shape of a path string.

Creating over an existing link that already points at the resolved source is a
no-op success. Creating over an existing **real directory** refuses and exits
non-zero — overwriting a real directory of weights is the one unrecoverable
mistake this script can make, so it never deletes, never overwrites and never
merges.

### The link is read-only

It points into `D:/Visomaster`, which is **read-only source material** for this
project. Anything written through the link lands in that tree. Nothing in Phase 2
writes through it; plan 02-02 pins the ONNX Runtime provider to CUDA specifically
so the TensorRT engine cache never materialises next to the weights.

`model_assets/` and `tensorrt-engines/` are already in `.gitignore`, so the link
is never committed and the cache directory can never be either.

### The probe set

`--check` verifies these nine files are readable *through the link* — opened and
read, not merely `stat`ed, because a junction into a tree with a denied ACL lists
fine and reads nothing. They are named explicitly rather than globbed so a later
phase can widen or shrink the set deliberately and see it in a diff.

| File | Read by |
|------|---------|
| `det_10g.onnx` | RetinaFace detector |
| `w600k_r50.onnx` | Inswapper128ArcFace recogniser |
| `inswapper_128.fp16.onnx` | the swapper itself |
| `occluder.onnx` | occlusion mask |
| `XSeg_model.onnx` | XSeg mask |
| `faceparser_resnet34.onnx` | face parser |
| `meanshape_68.pkl` | 3d68 landmark mean shape |
| `liveportrait_onnx/lip_array.pkl` | `FaceEditors.__init__` (the silent-`None` file above) |
| `liveportrait_onnx/motion_extractor.onnx` | LivePortrait entry model |

**`rd64-uni-refined.pth` is deliberately excluded.** CLIPseg's mask path loads
it, it exists nowhere under `D:/Visomaster`, and it is absent from the 56-entry
model manifest — upstream VisoMaster's text-masking control is non-functional on
this install too, so this is not something the vendoring broke. Plan 02-04 owns
that gap; see `.planning/phases/02-engine-api-first-swap/02-DECISION-deferred-paths.md`.
A check that fails from day one teaches everyone to ignore the check.
