# Engine path coverage

Which of the three risk paths Phase 1 vendored have actually been *run*, which
have only been shown to import, and what stands between the second group and the
first.

A vendored module that imports cleanly has been proven to parse and to reach none
of its Qt ancestry. It has not been proven to work. Phase 1 was explicit that
three paths — DFM, LivePortrait and CLIPseg — were vendored on that weaker
footing, and the Broken Windows ledger carried an open item saying so. This file
is the record of which third of that item has since been retired, which two
thirds have not, and — as of plan 02-04 — how the item was disposed of.

**All three verdicts below are final for Phase 2.** None is pending further work
inside this phase.

## Status

| Path | Executed? | Weights it needs | Present on this machine? | Verdict (final) | Owner |
|------|-----------|------------------|--------------------------|-----------------|-------|
| **LivePortrait** (face editor) | **Yes** — plan 02-03, `tests/test_engine_liveportrait.py` | `model_assets/liveportrait_onnx/`: `motion_extractor.onnx` (107 MB), `warping_spade-fix.onnx` (402 MB), `warping_spade.onnx` (402 MB), `appearance_feature_extractor.onnx` (3.2 MB), `stitching.onnx`, `stitching_eye.onnx`, `stitching_lip.onnx`, `lip_array.pkl` (658 B) | **Yes, all eight** | **PROVEN.** Executed against real weights; roadmap criterion 4 satisfied in full. | Closed by plan 02-03 |
| **CLIPseg** (text masking) | No — import-proven only, **deferred by decision** | `rd64-uni-refined.pth` in the models directory, plus a `ViT-B/16` CLIP backbone (~335 MB) fetched into `~/.cache/clip` on first run | **No.** `rd64-uni-refined.pth` exists nowhere on this machine outside `site-packages`, and it is absent from VisoMaster's own 62-entry model manifest — so upstream's text-masking control is non-functional on this install too. The vendoring did not break it. | **DEFERRED by decision**, per [`02-DECISION-deferred-paths.md`](../.planning/phases/02-engine-api-first-swap/02-DECISION-deferred-paths.md). Absent asset: **`rd64-uni-refined.pth`**. The path stays **vendored and left intact** — re-enabling it is a matter of supplying assets, never of re-vendoring. **Not** a failed roadmap criterion: criterion 4 names the LivePortrait editor path alone. | **Phase 4's model bootstrap** owns re-enablement. Plan 02-04 only records it. |
| **DFM** (DeepFaceLive models) | No — import-proven only, **deferred by decision** | At least one `.dfm` file in `model_assets/dfm_models/`, plus a populator for `EngineContext.dfm_models_data` | **No.** `model_assets/dfm_models/` contains only `.gitkeep`, and no `.dfm` file exists anywhere on the machine. | **DEFERRED by decision**, per [`02-DECISION-deferred-paths.md`](../.planning/phases/02-engine-api-first-swap/02-DECISION-deferred-paths.md). Absent assets: any **`.dfm`** file under `model_assets/dfm_models/`, plus a populator for `EngineContext.dfm_models_data`. The path stays **vendored and left intact** — re-enabling it is a matter of supplying assets, never of re-vendoring. **Not** a failed roadmap criterion. | **Phase 4's model bootstrap** owns re-enablement. Plan 02-04 only records it. |

Every asset named in the two deferred rows is inventoried, with its
re-enablement path and with no fabricated digest, in
[`engine-extra-assets.md`](engine-extra-assets.md).

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

Full detail, including what must be supplied and where it must land, is in
[`engine-extra-assets.md`](engine-extra-assets.md). In brief:

**CLIPseg.** Put `rd64-uni-refined.pth` in the models directory and allow network
access for the CLIP backbone on first run, then turn `ClipEnableToggle` on in the
project tier. Two `torch.load` calls sit on that path — `face_masks.py:256` and
`cliplib/clip.py:141` — and both pass `weights_only=True` as of plan 02-03, so a
crafted checkpoint cannot execute code at load time.

> **The missing `.pth` is not what makes this path inert.** `run_CLIPs`
> constructs `CLIPDensePredT` at `face_masks.py:254`, which reaches
> `clipseg.py:91` → `clip.load` → the ~335 MB `ViT-B/16` download into
> `~/.cache/clip` and then a deserializer, all **before** line 256 ever looks for
> `rd64-uni-refined.pth`. What keeps it inert in Phase 2 is
> `frame_worker.py:654`'s `ClipEnableToggle` gate, which the fixture sets to
> `false`. See the ordering walkthrough in
> [`engine-extra-assets.md`](engine-extra-assets.md).

**DFM.** Put at least one `.dfm` file in `model_assets/dfm_models/` and write a
populator for `EngineContext.dfm_models_data`. Phase 1 deferred "who fills it" to
Phase 2; with no models to enumerate, inventing a directory scan for an empty
directory would have been inventing it blind.

> `model_assets/` is a junction into the read-only `D:/Visomaster` source tree on
> this machine, so "put the file in the models directory" is not literally
> actionable until Phase 4 gives `models_dir` a project-owned location.

Both belong to Phase 4's model bootstrap, which is where the model inventory
becomes explicit and hash-verifiable.

## Ledger

Broken Windows item 1 — "CLIPseg, DFM and LivePortrait paths are import-proven
only; nothing in Phase 1 executes them. Phase 2 exercises all three." — is
**waived** as of plan 02-04.

It was **waived, not fixed**, deliberately. Plan 02-03 retired the LivePortrait
third of it, but the item's own second sentence became false the moment the
descoping decision was taken: Phase 2 exercises LivePortrait **only**. Marking it
`fixed` would assert something untrue about the other two thirds; leaving it
`open` would block ship on work nobody intends to do in this phase. The waiver
reason names `rd64-uni-refined.pth`, the absent `.dfm` files and Phase 4's model
bootstrap as the owning phase.

`tests/test_engine_deferred_paths.py` asserts that this waiver, and both
deferrals behind it, survive in all three places that record them — this file,
`engine-extra-assets.md`, and `.planning/WINDOWS.md`.
