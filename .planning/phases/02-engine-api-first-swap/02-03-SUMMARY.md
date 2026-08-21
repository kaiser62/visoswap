---
phase: 02-engine-api-first-swap
plan: 03
subsystem: engine
tags: [torch-load, weights-only, deserialization, liveportrait, face-editor, seal, ast-gate]
status: complete

requires:
  - 01-02 (the vendored external tree, the attribution header, threat T-01-06 deferred)
  - 02-01 (the model_assets link, the sealed runner, the exit vocabulary, the typed fixture)
  - 02-02 (Engine.load/detect_faces/swap, the CUDA provider lock, the swap-only frame)
provides:
  - tests/test_torch_load_hardened.py (a syntax-tree gate over every torch.load under visoswap/)
  - weights_only=True at BOTH unhardened call sites, not the one the plan named
  - tests/_engine_runner.py --faceedit (the first LivePortrait execution in this project)
  - tests/test_engine_liveportrait.py (four failure modes asserted separately)
  - docs/engine-path-coverage.md (which risk paths are proven, which are blocked, on what)
affects:
  - 02-04 records the two still-deferred paths and disposes of ledger item 1
  - Phase 4's model bootstrap owns sourcing rd64-uni-refined.pth and any .dfm file
  - Phase 4 makes models_dir env-driven, retiring the CWD dependency the lip array exposes

tech-stack:
  added: []
  patterns:
    - "resolve the FULL dotted chain when gating a call, never the trailing attribute: matching `.load` would demand weights_only of torch.jit.load and json.load, and a gate that cannot go green is a gate that gets deleted"
    - "treat zero bytes as absent for any regenerable artifact -- an empty file passes isfile and cv2.imread answers it with None, so an existence check silently becomes a comparison against nothing"
    - "make the test force the recovery branch every run (truncate the baseline) rather than trusting a clean checkout nobody runs to exercise it"
    - "record the deserializers you did NOT gate, with their reason, in the same file as the gate -- otherwise a green suite reads as 'nothing here unpickles unsafely', which is false"
    - "propagate a child process's exit code rather than flattening it: a seal breach in the nested regeneration is a seal breach, not an asset problem in the parent"

key-files:
  created:
    - tests/test_torch_load_hardened.py
    - tests/test_engine_liveportrait.py
    - docs/engine-path-coverage.md
  modified:
    - visoswap/processors/external/clipseg.py
    - visoswap/processors/external/cliplib/clip.py
    - tests/_engine_runner.py

key-decisions:
  - "There are THREE torch.load call sites under visoswap/, not the two the plan measured, and the third -- cliplib/clip.py:141 -- was the only genuinely reachable unsafe one. Hardened it too; the plan's own gate could not have gone green otherwise."
  - "The gate matches the full dotted name `torch.load`, so torch.jit.load, json.load, clip.load and pickle.load are non-matches by construction. pickle.load has no safe mode at all, so its three sites are inventoried by an explicit test rather than left to read as absent."
  - "The face-editor test truncates the swap-only baseline to zero bytes before every run, so the regeneration branch is exercised every time. Zero bytes is the sharper case than deletion."
  - "lp_lip_array is asserted populated, and the test says in its own docstring that the array feeds apply_face_expression_restorer -- a DIFFERENT path from the face editor. Proving it loaded is not proving it was used, and conflating the two would have been the easier and wronger claim."

metrics:
  duration: ~70 minutes
  completed: 2026-08-22
  tasks: 2
  commits: 3

actuals:
  tokens: 14255
  tasks: 2
  commits: 3
---

# Phase 02 Plan 03: Hardened `torch.load`, and LivePortrait's First Frame Summary

The project has run a face editor for the first time: **236,996 pixels changed
against the swap-only frame**, lip array loaded from disk, provider CUDA, no Qt,
in 10.5 seconds. And the deserialization item Phase 1 deferred turned out to be
two items, one of which nobody had noticed and which was the only one that was
actually reachable.

## What Was Built

**Task 1 — the `torch.load` gate** (commits `b72c78e` RED, `c391be7` GREEN).
`tests/test_torch_load_hardened.py` parses every `.py` under `visoswap/` and
walks it for calls whose **full dotted chain** is `torch.load`, then inspects that
call's keyword arguments. Eight tests: non-vacuity of the tree, non-vacuity of the
hit count, the gate itself, a capable-of-failing proof, a proof it does not match
`torch.jit.load`, a proof it rejects `weights_only=flag` and `**kwargs` as well as
absence, an anti-aliasing guard, and a `pickle.load` inventory.

The production change is one keyword and one trailing provenance comment, at each
of two sites.

**Task 2 — LivePortrait** (commit `bf4fe37`). `tests/_engine_runner.py --faceedit`
opens both editor gates — the `FaceEditorEnableToggle` project-tier key and
`EngineContext.edit_faces_enabled`, the plain field that replaced a Qt toggle
button — and edits **on top of a swapped face**, which is the combination the
application will actually run.

`tests/test_engine_liveportrait.py` is 18 tests over one shared run, with the four
failure modes asserted separately because roadmap criterion 4 distinguishes a Qt
import error from a missing-attribute error and one `exit == 0` cannot say which
happened.

## What the Plan Got Wrong

Four things, all measured. The first is the one that mattered.

### 1. There are three `torch.load` sites, not two, and the third was live

`<measured_facts>` states the tree holds exactly two: the dead one at
`clipseg.py:305` and the already-hardened one at `face_masks.py:256`, and the
plan's behavior block requires "at least two call sites, finding fewer fails".
The scan's first run, on the unmodified tree:

```
UNHARDENED visoswap/processors/external/cliplib/clip.py:141: torch.load(weights_only=<absent>)
UNHARDENED visoswap/processors/external/clipseg.py:305:      torch.load(weights_only=<absent>)
HARDENED   visoswap/processors/face_masks.py:256:            torch.load(weights_only=True)
```

`cliplib/clip.py:141` is not dead. The chain is `face_masks.py:254` constructs
`CLIPDensePredT` → `clipseg.py:91` calls `clip.load(version, device='cpu',
jit=False)` → `clip.py:134` tries `torch.jit.load` and, on `RuntimeError`, falls
through to `torch.load(opened_file, map_location="cpu")` at line 141. The file it
opens is **downloaded from a URL** into `~/.cache/clip` on first run. So the tree
carried one dead unsafe deserializer that the plan wrote a whole task about, and
one live unsafe deserializer reading network-sourced bytes that the plan did not
know existed.

It is hardened. This is deviation Rule 2 and it was not optional: the plan's own
success criterion is "*every* deserialization call under `visoswap/` passes the
safe-loading keyword as a literal", and the only alternatives were an exemption
carved into a security gate for its single most reachable site, or a gate that
could never go green.

Both diffs against upstream stay minimal. `clipseg.py` is still the two import
rewrites plus one line; `clip.py` was byte-identical to upstream and is now one
line.

**Descoping CLIPseg did not make this safe.** The path is unreachable today
because `rd64-uni-refined.pth` is absent — but that file is what `face_masks.py`
loads *after* the CLIP backbone has already been fetched and deserialized. The
ordering is worth stating plainly: turning `ClipEnableToggle` on with the `.pth`
still missing would have run `clip.py:141` first and failed afterwards.

### 2. The verification block's diff command shows the entire file

Step 2 asks for
`diff <(sed -n '6,$p' visoswap/.../clipseg.py) "D:/Visomaster/app/.../clipseg.py"`.
Run as written it reports `1,538c1,538` — every line of a 538-line file. The
vendored copies are LF and upstream is CRLF, so every line differs by one byte.
`diff --strip-trailing-cr` is the command that answers the question the step is
asking. Corrected output is quoted below.

### 3. The lip array is not on the face-editor path

The plan's `key_links` says asserting `lp_lip_array` is populated "is the only way
this is caught before it produces a wrong frame", framed as if the editor consumed
it. It does not. `lp_lip_array` is read at `frame_worker.py:1046`, `1050` and
`1058`, all inside `apply_face_expression_restorer`, which is gated on
`FaceExpressionEnableToggle` — a different toggle and a different path from
`swap_edit_face_core`, which this run exercises and which never touches it.

The assertion is still worth making and is still exactly the must-have truth the
plan wrote ("loaded from disk rather than silently defaulting to nothing"):
`FaceEditors.__init__` swallows `FileNotFoundError` and leaves the array `None`,
so a wrong working directory yields a **fully constructed engine** with the lip
retarget silently off. What changed is the claim attached to it. The test's own
docstring and `docs/engine-path-coverage.md` both say the array was proven
*loaded*, not proven *used*, because the easier sentence would have been the wrong
one and would have been believed.

### 4. `FaceEditorCropScaleDecimalSlider` is a poor choice of "something to do"

The plan offers "the crop scale and the several expression sliders" as
interchangeable. The crop scale feeds `warp_face_by_face_landmark_x`, so it
changes how much of the face the warp *sees*; the expression sliders feed
`update_delta_new_*` into the expression delta, so they change the face.
`MouthSmileDecimalSlider = 0.60` was chosen (upstream range -0.30 to 1.30) and is
named in the runner's output line, and a test asserts the chosen value differs
from the fixture's default — because LivePortrait's warp-and-decode roundtrip
moves pixels on its own, so a non-zero diff with every slider at rest would have
been a real result about the *path* and no result at all about the *controls*.

## Deviations from Plan

### [Rule 2 — missing critical functionality] Hardening a second `torch.load`

**Found during:** Task 1, RED. **Issue and evidence:** §1 above.
**Fix:** `weights_only=True` plus a trailing provenance comment at
`cliplib/clip.py:141`. Safe by inspection: the branch is only reached when
`torch.jit.load` has already refused the file, i.e. when it is a plain tensor
state dict, which is exactly what the restricted unpickler handles.
**Files:** `visoswap/processors/external/cliplib/clip.py`. **Commit:** `c391be7`.

### [Rule 2 — stronger check] Four places the implementation exceeds the plan

1. **The gate matches the full dotted chain.** The plan says "calls whose
   attribute chain ends in the deserialization function name". Implemented that
   way, the gate matches `torch.jit.load` (line 134 of the same `clip.py`),
   `json.load`, `clip.load` and `pickle.load`, none of which take `weights_only`
   — so it could never have gone green.
   `test_the_scanner_does_not_mistake_torch_jit_load_for_torch_load` pins both
   directions.
2. **`pickle.load` is inventoried rather than ignored.** Three sites, all reading
   the same model set the project already trusts wholesale (T-02-13, accepted).
   Without the inventory a green suite reads as "nothing in this tree unpickles
   unsafely", which is false; with it, a fourth site fails and gets its own
   decision.
3. **The capable-of-failing proof mutates a copy in memory**, carrying the real
   file's bytes, rather than editing the tracked file and restoring it. A test
   that edits the working tree leaves it broken if it dies mid-way, and the
   byte-identity-to-upstream constraint makes that an expensive way to fail. Both
   halves are asserted — mutated fails, original passes — since a scanner that
   flagged every input would pass the first half alone.
4. **An anti-aliasing guard.** `from torch import load` would turn every call site
   into a bare `load(...)`, the one shape an attribute-chain resolver cannot see.
   Rather than build a symbol table inside a test, the alias is forbidden;
   nothing in the tree wants it.

### [Rule 2 — stronger check] The baseline regeneration branch runs every time

The plan asks the runner to stat the swap-only baseline and regenerate it if
missing or empty. Implemented, with two sharpenings:

- **Zero bytes counts as absent**, and that is the case worth having: an empty
  file satisfies `isfile`, and `cv2.imread` answers a zero-byte PNG with `None`
  rather than raising, so a naive existence check silently degrades into a
  comparison against nothing.
- **The test truncates the baseline before every run** and asserts the runner
  reported `baseline=regenerated`. A recovery branch that only fires in a clean
  checkout is a branch nobody runs. Regeneration shells out to this file's own
  `--smoke` mode in a fresh process rather than calling `mode_smoke()` in-process,
  which would arm the seal twice and hold two decoders and two model sets open at
  once.

The child's exit code is propagated rather than flattened, so a seal breach in
the nested run reports as a seal breach.

## Verification Results (verbatim)

```
$ python -m pytest tests/ -q
87 passed

$ VISOSWAP_ENGINE_PYTHON=D:/Dev/visoswap/.venv-clean/Scripts/python.exe python -m pytest tests/ -q
87 passed

$ python -m pytest tests/test_torch_load_hardened.py -q
8 passed

$ python -m pytest tests/test_engine_liveportrait.py -q
18 passed
```

The face-editor run on the **Qt-carrying** interpreter — the strong version, since
four sealed roots genuinely resolve before the seal arms:

```
$ "D:/Visomaster/dependencies/Python/python.exe" -B tests/_engine_runner.py --faceedit
CLEAN:faceedit:faces=2 frames=24 provider=CUDA editor_model=Human-Face
lip_array=populated lip_array_shape=1x21x3 control=MouthSmileDecimalSlider=0.6
baseline=regenerated input_shape=1080x1920x3 output_shape=1080x1920x3
diff_vs_swap_only=236994 diff_vs_source=238988 elapsed=10.8s
artifacts=liveportrait_frame.png
reachable_before_seal=PyQt5=no,PyQt6=no,PySide2=no,PySide6=yes,app=yes,
backend=no,qtpy=yes,shiboken2=no,shiboken6=yes
exit=0
```

And on the clean venv, where no Qt is installed at all:

```
$ .venv-clean/Scripts/python.exe -B tests/_engine_runner.py --faceedit
CLEAN:faceedit:faces=2 ... diff_vs_swap_only=236996 diff_vs_source=238981
elapsed=10.5s ... PySide6=no,app=yes,...
exit=0
```

The two-pixel disagreement between the runs (236994 / 236996) is ONNX Runtime
kernel non-determinism across interpreters. Nothing asserts an exact count; the
assertion is `> 0`, and zero is the only value with a meaning.

**The scan's RED, before either fix — the evidence for §1:**

```
UNHARDENED visoswap/processors/external/cliplib/clip.py:141: torch.load(weights_only=<absent>)
UNHARDENED visoswap/processors/external/clipseg.py:305:      torch.load(weights_only=<absent>)
HARDENED   visoswap/processors/face_masks.py:256:            torch.load(weights_only=True)
```

**Verification step 2, with the command corrected per §2:**

```
$ diff --strip-trailing-cr <(sed -n '6,$p' visoswap/processors/external/clipseg.py) \
      "D:/Visomaster/app/processors/external/clipseg.py"
83c83
<         from visoswap.processors.external.cliplib import clip
83>        from app.processors.external.cliplib import clip
226c226   (the same import rewrite)
300c300
<   ... torch.load(join(dirname(basename(__file__)), 'shift_text_to_vis.pth'), weights_only=True) ...
      # VisoSwap: weights_only added (T-02-12); not upstream text.
>   ... torch.load(join(dirname(basename(__file__)), 'shift_text_to_vis.pth')) ...

$ diff --strip-trailing-cr <(sed -n '6,$p' visoswap/processors/external/cliplib/clip.py) \
      "D:/Visomaster/app/processors/external/cliplib/clip.py"
136c136
<             state_dict = torch.load(opened_file, map_location="cpu", weights_only=True)
      # VisoSwap: weights_only added (T-02-12); not upstream text.
>             state_dict = torch.load(opened_file, map_location="cpu")
```

Header plus import rewrites plus one keyword, in both files, exactly as required.

**Every new exit code, watched firing** (02-01's rule: an exit code nobody has
watched fire is an exit code nobody knows the meaning of):

```
VISOSWAP_SETTINGS_FIXTURE=/nope.json --faceedit
  ASSET_MISSING:faceedit:settings fixture not found at ...            exit=3
VISOSWAP_TEST_VIDEO=/nope.mp4 --faceedit
  ASSET_MISSING:faceedit:target video not found at ...                exit=3
--faceedit --nope
  ENGINE_ERROR:-:usage: ... (--selftest | --import ... | --smoke | --faceedit)  exit=4
```

and the four nested-regeneration branches, provoked by substituting the child
call (scratch script, not committed):

```
child exited 1 -> SEAL_BREACHED:faceedit:the swap-only baseline ... exited 1   exit=1
child exited 3 -> ASSET_MISSING:faceedit:the swap-only baseline ... exited 3   exit=3
child exited 4 -> ENGINE_ERROR:faceedit:the swap-only baseline ... exited 4    exit=4
child exited 0 but wrote nothing
             -> ASSET_MISSING:faceedit:--smoke exited CLEAN but did not leave
                a non-empty ...                                                exit=3
```

**Ledger item 1 is still open, as required** (verification step 5):

```
$ grep -c '"status": "open"' .planning/WINDOWS.md
1
```

### Read-only source trees

```
$ find "D:/Visomaster" -newermt "2026-08-22 01:13:05" -not -path '*/dependencies/*'
(nothing)

$ find "D:/Visomaster/app" -name "__pycache__" -newermt '-1 day'
(nothing)
```

Nothing under `D:/Visomaster` is newer than this plan's first commit. Every
invocation of the portable interpreter used `-B`, and the runner passes `-B` to
the child it spawns for baseline regeneration, so no bytecode was written into
that tree.

**`C:/Users/Sakat/.devswarm/repos/1/ef7f9c3d/config-parallel-setup` carries one
uncommitted modification, and it is not from this plan.** `git status --short`
reports `M .gitignore`, a single added line `tests/artifacts/`. Its mtime is
**2026-08-21 21:02:26**, three and a half minutes before plan 02-02's commit
`e1fd91b` (21:05:55) and four hours before this plan's first commit (2026-08-22
01:13:05). It is a stray duplicate of the `.gitignore` line 02-02 added to
`visoswap/.gitignore` — the 02-02 executor wrote it into both trees. **Left
untouched**: that tree is read-only, and reverting a modification is still
modifying it. Recorded here so it is not later attributed to this plan.

## Roadmap Criteria

**Criterion 4 is met**, as amended by `02-DECISION-deferred-paths.md`: the
criterion names the LivePortrait editor path alone, and it has now executed
against real weights and produced a frame differing from the swap-only frame by
237k pixels. DFM and CLIPseg are import-proven only, by owner decision, and are
**not** outstanding criterion-4 work — the phase verifier must not read them as a
failed criterion. Both are additionally documented in
`docs/engine-path-coverage.md` with the specific missing files, so the gap stays
visible without being mistaken for a failure.

`ENGINE-01` stays **Pending**, unchanged from 02-02. It means "swap a frame with
no PySide6 installed **and no VisoMaster install present**", and the second clause
is still not true: `model_assets` is a junction into `D:/Visomaster`. It closes in
02-04, not here.

## Threat Flags

None new. This plan adds no network endpoint, no auth path, no schema at a trust
boundary and **no third-party package** (T-02-SC): the additions are `ast` and
`subprocess`, both standard library, on interpreters that already had them.

Dispositions touched:

- **T-02-12** (`clipseg.py:305`) — mitigated, and the register's framing is
  correct: dead on the live path, hardened anyway. **The register is incomplete,
  not wrong**: it does not name `cliplib/clip.py:141`, which is the same threat
  category on a genuinely reachable path. Now mitigated identically.
- **T-02-13** (`lip_array.pkl` unpickled) — accepted, unchanged, and now
  inventoried by `test_the_pickle_load_inventory_has_not_grown` alongside its two
  siblings so a fourth cannot appear unnoticed.
- **T-02-15** (TensorRT plugin) — mitigated and now *observed*: the editor test
  asserts `provider=CUDA`, so a silent provider change fails rather than quietly
  loading a platform plugin.
- **T-02-16** (edited frame on disk) — mitigated: `liveportrait_frame.png` goes
  only under the already-ignored `tests/artifacts/`, and no image payload is
  logged.

## Known Stubs

None. `--faceedit` is a complete mode; an unknown argument still returns the usage
line and exit 4 rather than a stub that reports `CLEAN`.

## What 02-04 Inherits

`docs/engine-path-coverage.md` already carries the two blocked paths, their
specific missing filenames, their re-enablement steps and the ownership split
(Phase 4's model bootstrap closes the gap; 02-04 records it). 02-04's remaining
work on this front is disposing of Broken Windows item 1, which is deliberately
left open here.

One thing 02-04 should know before it closes `ENGINE-01`: the lip array proves
the `models_dir` CWD dependency is still live. `FaceEditors.__init__` resolves
`f'{models_dir}/liveportrait_onnx/lip_array.pkl'` against a relative
`./model_assets` and swallows the failure, so the wrong working directory
produces a constructed engine with a silently disabled retarget. The runner now
checks the file before the model load and reports `ASSET_MISSING` naming the
resolved path and the CWD, which is a guard rail rather than a fix. Phase 4's
env-driven `models_dir` is the fix.

## Self-Check: PASSED

Created files, all present on disk:

```
FOUND: tests/test_torch_load_hardened.py
FOUND: tests/test_engine_liveportrait.py
FOUND: docs/engine-path-coverage.md
FOUND: tests/artifacts/liveportrait_frame.png (1.9M, gitignored)
```

Commits, all present in the log:

```
FOUND: b72c78e  test(02-03): add a failing gate over every torch.load under visoswap/
FOUND: c391be7  fix(02-03): refuse to execute code from a weights file at both torch.load sites
FOUND: bf4fe37  feat(02-03): run LivePortrait for the first time and prove it moved the frame
```
