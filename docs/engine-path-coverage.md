# Engine path coverage

Which of the three risk paths Phase 1 vendored have actually been *run*, which
have only been shown to import, and what stands between the second group and the
first.

A vendored module that imports cleanly has been proven to parse and to reach none
of its Qt ancestry. It has not been proven to work. Phase 1 was explicit that
three paths — DFM, LivePortrait and CLIPseg — were vendored on that weaker
footing, and the Broken Windows ledger carries an open item saying so. This file
is the record of which third of that item has since been retired and which two
thirds have not.

## Status

| Path | Executed? | Weights it needs | Present on this machine? | Owner |
|------|-----------|------------------|--------------------------|-------|
| **LivePortrait** (face editor) | **Yes** — plan 02-03, `tests/test_engine_liveportrait.py` | `model_assets/liveportrait_onnx/`: `motion_extractor.onnx` (107 MB), `warping_spade-fix.onnx` (402 MB), `warping_spade.onnx` (402 MB), `appearance_feature_extractor.onnx` (3.2 MB), `stitching.onnx`, `stitching_eye.onnx`, `stitching_lip.onnx`, `lip_array.pkl` (658 B) | **Yes, all eight** | Closed by plan 02-03 |
| **CLIPseg** (text masking) | No — import-proven only, **deferred by decision** | `rd64-uni-refined.pth` in the models directory, plus a `ViT-B/16` CLIP backbone (~335 MB) fetched into `~/.cache/clip` on first run | **No.** `rd64-uni-refined.pth` exists nowhere on this machine outside `site-packages`, and it is absent from VisoMaster's own 62-entry model manifest — so upstream's text-masking control is non-functional on this install too. The vendoring did not break it. | **Phase 4's model bootstrap owns closing the gap.** Plan 02-04 only records it. |
| **DFM** (DeepFaceLive models) | No — import-proven only, **deferred by decision** | At least one `.dfm` file in `model_assets/dfm_models/`, plus a populator for `EngineContext.dfm_models_data` | **No.** `model_assets/dfm_models/` contains only `.gitkeep`, and no `.dfm` file exists anywhere on the machine. | **Phase 4's model bootstrap owns closing the gap.** Plan 02-04 only records it. |

The decision to descope the bottom two rows is
[`02-DECISION-deferred-paths.md`](../.planning/phases/02-engine-api-first-swap/02-DECISION-deferred-paths.md),
taken by the project owner on 2026-08-21. It supersedes plan 02-04's
`checkpoint:decision` task, which asked this question.

**They are not ripped out.** Re-adding either later must not mean redoing the
vendoring, which is why both stay in the tree with their attribution headers and
both stay covered by the Qt gates, the attribution gate and the `torch.load` gate
like every other vendored file.

## What "executed" means for LivePortrait

`tests/_engine_runner.py --faceedit` swaps a face and then runs the LivePortrait
editor over it, inside the same seal every other engine run uses — Qt,
VisoMaster's `app` package and the backend all made unimportable, with `app`
genuinely resolving before the seal arms so the refusal is the seal firing rather
than the module not being there.

Measured, on the clean engine interpreter, from the repository root:

```
CLEAN:faceedit:faces=2 frames=24 provider=CUDA editor_model=Human-Face
lip_array=populated lip_array_shape=1x21x3 control=MouthSmileDecimalSlider=0.6
baseline=regenerated input_shape=1080x1920x3 output_shape=1080x1920x3
diff_vs_swap_only=236996 diff_vs_source=238981 elapsed=10.5s
artifacts=liveportrait_frame.png
```

Three things in that line are the point:

- `diff_vs_swap_only=236996` — the edited frame differs from the swap-only frame
  by 237k pixels. Zero would mean the editor path did not run.
- `lip_array=populated` — `FaceEditors.__init__` opens
  `liveportrait_onnx/lip_array.pkl` inside a `try` that swallows
  `FileNotFoundError` and leaves `lp_lip_array` as `None`. A wrong working
  directory therefore produces a **fully constructed engine** with the lip
  retarget silently disabled. The array feeds
  `apply_face_expression_restorer`, which is a *different* path from the face
  editor, gated on `FaceExpressionEnableToggle`; this run proves the file was
  found and read, not that the editor consumed it.
- `provider=CUDA` — the warping path loads a platform-specific plugin library
  only under the `TensorRT-Engine` provider, which plan 02-02 refuses outright.
  Asserting the provider is what keeps T-02-15 an observation rather than an
  argument.

## Re-enablement

**CLIPseg.** Put `rd64-uni-refined.pth` in the models directory and allow network
access for the CLIP backbone on first run, then turn `ClipEnableToggle` on in the
project tier. Two `torch.load` calls sit on that path — `face_masks.py:256` and
`cliplib/clip.py:141` — and both pass `weights_only=True` as of plan 02-03, so a
crafted checkpoint cannot execute code at load time.

**DFM.** Put at least one `.dfm` file in `model_assets/dfm_models/` and write a
populator for `EngineContext.dfm_models_data`. Phase 1 deferred "who fills it" to
Phase 2; with no models to enumerate, inventing a directory scan for an empty
directory would have been inventing it blind.

Both belong to Phase 4's model bootstrap, which is where the model inventory
becomes explicit and hash-verifiable.

## Ledger

Broken Windows item 1 — "the DFM, LivePortrait and CLIPseg paths are
import-proven only" — is **still open**. Plan 02-03 retires the LivePortrait
third of it; plan 02-04 disposes of the item.
