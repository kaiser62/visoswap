# Decision: DFM and CLIPseg are descoped for Phase 2

*Decided 2026-08-21 by the project owner. Supersedes plan `02-04`'s
`checkpoint:decision` task, which asked this question.*

## The decision

The **DFM** and **CLIPseg text-masking** paths stay vendored and import-proven.
Neither gets exercised in Phase 2. Both are recorded as known gaps in the engine
path coverage doc and carried to Phase 4's model bootstrap.

They are **not** ripped out. Re-adding them later must not mean redoing the
vendoring.

**LivePortrait is unaffected** — it has weights present, and plan `02-03`
exercises it for real. It is the one deferred path Phase 2 actually proves.

## Why

Verified by direct filesystem check, not inference:

- `rd64-uni-refined.pth`, which `face_masks.py:251` loads for CLIPseg, **does not
  exist anywhere on this machine** outside `site-packages`. A whole-tree search
  for `*.pth` returns nothing.
- It is **absent from VisoMaster's own model manifest** — 62 entries across
  `models_list` and `models_trt_list`, zero `.pth` references. So upstream
  VisoMaster's text-masking control is non-functional on this install too. This
  is not something the vendoring broke.
- `model_assets/dfm_models/` contains only `.gitkeep`, and **no `.dfm` file
  exists anywhere on the machine**.
- `EngineContext.dfm_models_data` has no populator. Phase 1 deferred "who fills
  it" to Phase 2; with no models to enumerate, inventing a scan for an empty
  directory would be inventing it blind.

Exercising either path would have required sourcing external weights — ~1.1GB
for CLIPseg, plus an undocumented 335MB `ViT-B/16` download into `~/.cache/clip`
on first run — to test features not currently in use.

## What this changes

**Plan `02-04` collapses to documentation.** Its `checkpoint:decision` task is
answered by this file. Its remaining work is recording the two gaps and their
re-enablement path, not exercising anything.

**The roadmap's Phase 2 criterion needs amending.** "Exercises the DFM,
LivePortrait and CLIPseg paths at least once each" becomes: LivePortrait is
exercised; DFM and CLIPseg remain import-proven only, by decision.

## Carried forward, unchanged

The security item stands, but state it accurately: `clipseg.py:305`'s
unguarded `torch.load` is **dead on the live path** — it sits behind
`fix_shift=False`, no construction site passes it, and the file it references
does not exist. `face_masks.py:251` **already** passes `weights_only=True`.

Fix the keyword anyway, because a dead path is one refactor away from a live
one. Do not describe it as closing a live hole.

## Re-enablement

Whoever picks these up needs, for CLIPseg: `rd64-uni-refined.pth` in the models
directory, plus network access for the CLIP backbone on first run. For DFM: at
least one `.dfm` file in `model_assets/dfm_models/`, and a populator for
`EngineContext.dfm_models_data`.

Phase 4's model bootstrap is where both belong, since that is where the model
inventory becomes explicit and verifiable.
