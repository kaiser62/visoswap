# The `main_window` attribute surface

**Measured:** 2026-08-21, plan 01-03, against `D:/Visomaster` at the revision vendored by
plans 01-01 and 01-02 (upstream revision explicitly undeterminable — see `NOTICE`).

**Result: 15 distinct attributes, matching the design's stated count exactly.**

This document is the contract that Phase 2's `Engine` API and Phase 3's three-tier settings
model are both built against. It exists as an artifact with line evidence rather than a number
in a design doc so that neither phase has to reconstruct it from memory, and so that a later
disagreement is a diff against something measured rather than an argument.

## How it was derived

Mechanically, not by reading. For each of the three upstream files that couple to the host
window, every `main_window.<attr>` occurrence was collected with its line number and the
attribute names unioned:

```python
re.compile(r"main_window\.([A-Za-z_][A-Za-z0-9_]*)")
```

The three files, all read-only source under `D:/Visomaster/app/processors/`:

| File | Lines | Distinct attributes | Fate |
|------|-------|---------------------|------|
| `workers/frame_worker.py` | 1307 | 7 | vendored and de-Qt'd in plan 01-04 |
| `models_processor.py` | 410 | 5 | vendored and de-Qt'd in plan 01-03 (this plan) |
| `video_processor.py` | 422 | 6 | **dropped whole** — never vendored |

Line counts are `grep -c ""`, which counts the trailing newline; the design doc's 1306 / 421
are content-line counts. Same files.

A second pass confirmed there is no other route to the host window: no `getattr(main_window, …)`
anywhere in `app/`, and in `models_processor.py` the bare name `main_window` appears only at the
constructor signature (line 47) and the store (line 49). In `video_processor.py` and
`frame_worker.py` the bare name is additionally passed whole into `app/ui` action helpers — but
every one of those call sites is on the display or timeline path, which is dropped, so no
attribute enters the surface through them.

The per-file breakdown is kept deliberately. The union alone loses the information about which
attributes die with `video_processor.py`, which is the entire reason the surface shrinks from 15
to 7.

## The surface

`fw` = `workers/frame_worker.py`, `mp` = `models_processor.py`, `vp` = `video_processor.py`.

| Attribute | Read by (line evidence) | What it holds | Verdict |
|-----------|-------------------------|---------------|---------|
| `control` | mp:150 · vp:170,171,339,342 · fw:48,129 | The global-tier settings dict. | **kept** — Phase 3's global tier. |
| `parameters` | fw:44 | The per-project settings dict, keyed by face id. | **kept** — Phase 3's project tier. |
| `target_faces` | fw:36,85,160,241,292 | The detected-face store the swap pipeline iterates. | **kept** — the swap path's primary input. |
| `models_processor` | vp:136 · fw:32,42 | The engine's own model registry. | **kept** — the engine reaching itself; `EngineContext` carries the reference. |
| `dfm_models_data` | mp:159 | DFM model metadata dict, name → model descriptor. | **kept** — the only DFM lookup on the load path. |
| `swapfacesButton` | fw:48,163,170 | A Qt toggle button; read only as `.isChecked()`. | **kept as a boolean** — becomes `swap_faces_enabled: bool`. |
| `editFacesButton` | fw:48,137,163,183 | A Qt toggle button; read only as `.isChecked()`. | **kept as a boolean** — becomes `edit_faces_enabled: bool`. |
| `video_processor` | fw:33 | The Qt playback loop object. | **dropped** — dropped whole; the scheduler owns playback. |
| `model_load_dialog` | mp:215,218,219 | A Qt progress dialog widget. | **dropped** — Qt widget; both methods reaching it have zero callers. |
| `model_loaded_signal` | mp:142,163,181,283 | Qt Signal on the host window, emitted on load completion. | **dropped** — no consumer on the swap path. |
| `model_loading_signal` | mp:128,149,170,280 | Qt Signal on the host window, emitted on load start. | **dropped** — no consumer on the swap path. |
| `display_messagebox_signal` | vp:231,259 | Qt Signal driving a modal error box. | **dropped** — read only by `video_processor.py`; UI notification. |
| `loading_new_media` | vp:93,95 | Playback-state flag. | **dropped** — read only by `video_processor.py`, dies with it. |
| `processed_frames` | vp:245 | Playback frame buffer. | **dropped** — read only by `video_processor.py`, dies with it. |
| `videoSeekSlider` | vp:328 | Qt seek-bar widget. | **dropped** — read only by `video_processor.py`, dies with it. |

**Kept 7 + dropped 8 = 15.** No attribute is unaccounted for.

The two button attributes are the only ones whose *type* changes. All seven occurrences across
the five `frame_worker.py` lines are `main_window.<button>.isChecked()` and nothing else — never
`.setChecked()`, never `.text()`, never passed as a widget. That is what makes replacing a widget
reference with a plain `bool` a faithful substitution rather than a lossy one.

## `EngineContext`

`visoswap/processors/context.py` defines the seven kept fields and nothing else:

```python
control: dict
parameters: dict
target_faces: dict
models_processor: "ModelsProcessor" | None
dfm_models_data: dict
swap_faces_enabled: bool
edit_faces_enabled: bool
```

`tests/test_context_surface.py` asserts exactly these seven field names — no more, no fewer.
Widening the surface is therefore a deliberate, reviewed edit to a test, not a quiet addition to
a dataclass. That test is the mitigation for threat **T-01-09**: an open-ended context is how
the god object returns, and with it the hidden-state problem this project exists to fix.

`models_processor` is type-hinted under `if TYPE_CHECKING:` with a string annotation, because
`models_processor.py` imports `EngineContext` and a runtime import in the other direction is a
cycle.

## Note 1 — the deleted model-load progress signalling, and how to add it back

`model_loading_signal`, `model_loaded_signal` and `model_load_dialog` were the engine's **only**
model-load progress reporting. Deleting them removes that capability entirely. Nothing on the
swap path consumed it — that was measured, not assumed:

- `showModelLoadingProgressBar` and `hideModelLoadProgressBar` (upstream lines 214-219) have
  **zero callers** anywhere in `D:/Visomaster`. The only reference is a commented-out call at
  `models_processor.py:168`.
- The class-level `processing_complete` and `model_loaded` Signals (upstream lines 44-45) have
  **zero** `.connect()` sites anywhere in `D:/Visomaster`. They are declared and never used.
- The eight emissions on `main_window` drove a Qt progress bar owned by the UI, which VisoSwap
  does not have.

They were deleted outright rather than replaced with a `print`, a logger call, or a no-op stub.
A no-op stub reads like a thing that works; the next person to touch `models_processor.py` should
see that the notification is genuinely gone and land here.

**The re-add path, if a later phase wants it.** Loading a DFM or TensorRT model against a
multi-gigabyte weight set is slow enough that a caller may legitimately need progress. Do not
reintroduce a signal. Add a Qt-free callback field to `EngineContext`:

```python
on_model_load: Callable[[str, str], None] | None = None   # (model_name, "start" | "done")
```

and call it at the eight sites recorded in the table above (upstream lines 128, 142, 149, 163,
170, 181, 280, 283 — see the "Emission sites" note below for where they sit in the vendored
file). This keeps the engine free of any notification framework and leaves the caller — CLI,
web backend, or test — to decide what progress means. It also requires widening
`EngineContext` from seven fields to eight, which means editing `tests/test_context_surface.py`.
That is intentional friction, not an obstacle: it forces the addition to be reviewed against
this document.

This is recorded as a **decision** so a future regression report finds a choice rather than an
absence. It is the mitigation for threat **T-01-11**.

## Note 2 — who fills `dfm_models_data`

`EngineContext` carries `dfm_models_data` as a dict. **It does not populate it.**

Upstream, it is filled at `app/ui/main_ui.py:78` from `DFM_MODELS_DATA`, which comes from
`get_dfm_models_data()` in `app/helpers/miscellaneous.py:69`. Plan 01-02 vendored only the
six-symbol subset of `miscellaneous.py` that the processor tree imports at module scope, and
`get_dfm_models_data()` is **deliberately not among them** — it scans a directory for `.dfm`
files, which is a filesystem-discovery concern, not an inference concern.

So in Phase 1 the field defaults to an empty dict and the DFM load path at
`models_processor.py` would raise `KeyError` if exercised. **Nothing in Phase 1 exercises it.**

Phase 2 owns the decision of who fills it — most likely the `Engine` constructor, scanning a
models directory. Flagged here so Phase 2 finds an empty dict and reads it as an open decision
rather than as a bug introduced by this phase.

## Note 3 — emission sites in the vendored file

Every line number in this document is an **upstream** line number, in `D:/Visomaster/app/`.
The vendored copies carry a 5-line attribution header (4 comment lines + 1 blank), so a vendored
line number is the upstream number **+5** — and for `models_processor.py` the de-Qting deletions
then shift everything below line 14 further. Read this table as evidence against upstream, which
is the stable reference, and re-derive against the vendored file when editing it.

**Resolved for `models_processor.py` (plan 01-03 Task 2, 2026-08-21).** The file is now
vendored and de-Qt'd, so the eight emission sites no longer exist as lines. What survives is the
method that contained each one. Anyone re-adding progress via the `on_model_load` callback above
should reinstate the calls at these positions in `visoswap/processors/models_processor.py`:

| Upstream line | Was | Vendored anchor |
|---|---|---|
| 128 | `model_loading_signal` | first statement inside `with self.model_lock:` in `load_model` (def at vendored :128) |
| 142 | `model_loaded_signal` | just before `return model_instance` in `load_model` |
| 149 | `model_loading_signal` | first statement inside `if not self.dfm_models.get(...)` in `load_dfm_model` (def at vendored :146) |
| 163 | `model_loaded_signal` | after the `try/except`, before `return self.dfm_models[dfm_model]` |
| 170 | `model_loading_signal` | top of `load_model_trt` (def at vendored :165) |
| 181 | `model_loaded_signal` | just before `return model_instance` in `load_model_trt` |
| 280 | `model_loading_signal` | first statement inside `if not self.models[model_name]:` in `load_inswapper_iss_emap` (def at vendored :266) |
| 283 | `model_loaded_signal` | after `self.emap = ...` in `load_inswapper_iss_emap` |

`showModelLoadingProgressBar` and `hideModelLoadProgressBar` (upstream 214-219) were deleted
outright; they sat between `delete_models_trt` and `switch_providers_priority` (vendored :210).
The commented-out `# self.showModelLoadingProgressBar()` at vendored :166 was left exactly as
upstream wrote it, so the file keeps diffing clean -- it is upstream's own dead comment, not a
stub this project introduced.

## What disagreed with the plan

One thing, minor and in the plan's own prose rather than in the measurement:

- Plan 01-03's Task 1 text says the button attributes are read "at four sites in
  `frame_worker.py`". The measurement is **five distinct lines** (48, 137, 163, 170, 183) and
  **seven occurrences**, because lines 48 and 163 each read both buttons. The design doc
  (`docs/specs/2026-08-21-visoswap-standalone-design.md`, "Engine boundary") already says five
  and is correct. No consequence for the surface — both attributes are kept as booleans either
  way — but plan 01-04 rewrites all seven occurrences, not four, and should be read with that
  number.

Everything else the plan predicted held exactly: the 15-attribute total, all three per-file
breakdowns, the eight emission lines, the three dialog lines, and the two kept reads at 150
and 159.
