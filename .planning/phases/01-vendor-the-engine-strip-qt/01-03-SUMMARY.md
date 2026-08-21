---
phase: 01-vendor-the-engine-strip-qt
plan: 03
subsystem: engine
tags: [context-object, qt-strip, vendoring, boundary, god-object]
status: complete

requires:
  - 01-01 (attribution header, Qt-reachability probe, conftest harness, vendor-header gate)
  - 01-02 (the 26 vendored Qt-free modules and the import rewrite map)
provides:
  - .planning/phases/01-vendor-the-engine-strip-qt/01-CONTEXT-SURFACE.md (the 15-attribute enumeration, the contract Phase 2 and Phase 3 build against)
  - visoswap/processors/context.py (EngineContext, 7 fields, no Qt)
  - visoswap/processors/models_processor.py (vendored and de-Qt'd)
  - tests/test_context_surface.py (field-surface pin + consumer-side read check)
  - PENDING_QT_STRIP narrowed to one entry
affects:
  - plan 01-04 de-Qt's frame_worker.py against this same EngineContext and empties PENDING_QT_STRIP
  - Phase 2's Engine API is built on the 7-field surface enumerated here
  - Phase 3's three-tier settings model maps onto context.control and context.parameters

tech-stack:
  added: []
  patterns:
    - "vendor-and-edit in one scripted pass: upstream bytes -> import map -> line edits, with every edit site asserted against the copy before it is touched"
    - "delete dead Qt notification outright rather than stubbing it, and record the re-add path in a doc instead"
    - "pin both sides of a contract: what the context offers (dataclasses.fields) and what the vendored code asks for (ast scan of self.context reads)"

key-files:
  created:
    - visoswap/processors/context.py
    - visoswap/processors/models_processor.py
    - .planning/phases/01-vendor-the-engine-strip-qt/01-CONTEXT-SURFACE.md
    - tests/test_context_surface.py
  modified:
    - tests/conftest.py
    - tests/test_vendor_headers.py

decisions:
  - "Enumerate the main_window surface mechanically (regex + line numbers per file) rather than by reading, and keep the per-file breakdown -- the union alone loses which attributes die with video_processor.py, which is the whole reason the surface shrinks from 15 to 7."
  - "Keep the two Qt toggle buttons as plain booleans. Every read of them upstream is .isChecked() and nothing else, which is what makes bool a faithful substitution rather than a lossy one."
  - "Delete the eight model-load emissions and both progress-dialog methods outright -- no print, no logger, no no-op stub. A no-op stub reads like a thing that works. The re-add path is recorded in 01-CONTEXT-SURFACE.md as a Qt-free EngineContext callback."
  - "Point models_processor.py's existing `if TYPE_CHECKING:` block at EngineContext instead of deleting it, as the plan literally said. Deleting it would leave `context: 'EngineContext'` as an unresolvable forward reference, and the swap is a strictly smaller diff against upstream than a deletion."
  - "Vendor by script, not by hand: assert all 24 edit sites against the copy before editing, then delete by upstream line number in reverse. A stale offset silently edits the wrong line, and this file is 410 lines of inference plumbing that must keep diffing clean."
  - "Scope the new consumer-side context scan to files that name EngineContext. `self.context` is not a unique name in the vendored tree -- utils/tensorrt_predictor.py uses it for a TensorRT execution context."

metrics:
  duration: ~25 min (task 2; task 1 ran in a prior session)
  completed: 2026-08-21
  tasks: 2
  commits: 2
  files: 6

actuals:
  tokens: 14556
  tasks: 2
  commits: 2
---

# Phase 01 Plan 03: The Context Surface and a Qt-Free `models_processor.py` Summary

The `main_window` god object is now a written contract of 15 attributes with line evidence,
a 7-field dataclass, and one vendored file proving the reduction actually holds.

**The enumeration agreed with the design exactly: 15 distinct attributes, and all three
per-file breakdowns matched.** The plan asked for that disagreement to lead the summary if it
occurred. It did not occur.

## What Was Built

**Task 1 — the enumeration and `EngineContext`** (commit `2bf871d`, prior session).
`01-CONTEXT-SURFACE.md` records all 15 attributes as a table: which files read each, the line
numbers, what it holds, and a **kept**/**dropped** verdict with a reason. Kept 7, dropped 8.
`visoswap/processors/context.py` defines `EngineContext` with exactly those seven fields, each
carrying a comment naming the `main_window.<attr>` read it replaces.
`tests/test_context_surface.py` pins the field set at seven names in both directions.

**Task 2 — `models_processor.py`, vendored and de-Qt'd in one step** (commit `9e859cf`).
410 upstream lines in, 397 vendored lines out. This is the first vendored file with intended
edits, so byte-identity to upstream ends here by design — the replacement property is that the
diff is small, enumerated, and reviewable, and it is: **the header, the import map, and
fourteen edit sites. Nothing else.**

## What Changed in `models_processor.py`

Derived by script, not by hand. Every one of the 24 sites below was asserted to match its
expected content *in the copy* before anything was edited, then deletions were applied in
reverse line order so no offset could go stale. Upstream line numbers:

| Upstream | Change |
|---|---|
| — | 5-line attribution header prepended (4 comment lines + blank) |
| 14 | `from PySide6 import QtCore` — **deleted** |
| 22-35 | 13 `app.*` imports rewritten per plan 01-02's map |
| 37 | `from app.ui.main_ui import MainWindow` → `from visoswap.processors.context import EngineContext` (still inside the existing `if TYPE_CHECKING:`) |
| 43 | `class ModelsProcessor(QtCore.QObject):` → `class ModelsProcessor:` |
| 44 | `processing_complete = QtCore.Signal()` — **deleted** |
| 45 | `model_loaded = QtCore.Signal()` — **deleted** |
| 47 | `main_window: 'MainWindow'` → `context: 'EngineContext'` |
| 49 | `self.main_window = main_window` → `self.context = context` |
| 128, 149, 170, 280 | `self.main_window.model_loading_signal.emit()` — **deleted**, 4 sites |
| 142, 163, 181, 283 | `self.main_window.model_loaded_signal.emit()` — **deleted**, 4 sites |
| 150 | `self.main_window.control[...]` → `self.context.control[...]` |
| 159 | `self.main_window.dfm_models_data[...]` → `self.context.dfm_models_data[...]` |
| 214-220 | `showModelLoadingProgressBar` and `hideModelLoadProgressBar` — **both methods deleted whole**, with the trailing blank line |

Three things deliberately left alone, all of which would have been easy to "improve" in passing
and all of which would have widened the diff:

- **`super().__init__()` at line 48 stays.** It is valid against `object`. The plan says leave
  it rather than churn the line, and it does not depend on the base class it used to have.
- **`# self.showModelLoadingProgressBar()` at upstream 168 stays**, now a commented-out call to
  a method that no longer exists. It is upstream's own dead comment, not a stub this project
  introduced, and the static Qt scan blanks comment tokens so it costs nothing. Recorded in
  `01-CONTEXT-SURFACE.md`.
- **`# QApplication.processEvents()` at upstream 129 stays**, for the same reason.

The methods were deleted rather than stubbed because they have **zero callers** — verified
across the whole of `D:/Visomaster`, not just the vendored subset. The only reference anywhere
is the commented-out one above. The two class-level Signals likewise have zero `.connect()`
sites upstream: declared, never used.

`01-CONTEXT-SURFACE.md` gained a table mapping each of the eight deleted emissions to the
surviving method that contained it, with vendored line numbers for the anchors. That closes the
one gap in the re-add path: Note 1 said *how* to add progress back (a Qt-free `on_model_load`
callback on `EngineContext`), but until the file existed there was nowhere to say *where*.

## Verification Results (verbatim)

Task 2, the plan's three checks:

```
=== VERIFY 2a: probe ===
$ "D:/Visomaster/dependencies/Python/python.exe" tests/_qt_guard_probe.py visoswap.processors.models_processor | grep -qx CLEAN
exit=0 (CLEAN)
=== VERIFY 2b: full suite ===
$ python -m pytest tests/ -x -q
.................                                                        [100%]
17 passed in 7.45s
=== VERIFY 2c: AST ===
plain class, no signals
```

Task 1's checks, re-run against the tree as it now stands:

```
=== T1-VERIFY-a ===
$ python -m pytest tests/test_context_surface.py tests/test_vendor_headers.py -x -q
..........                                                               [100%]
10 passed in 0.14s
=== T1-VERIFY-b ===
exit=0 rows=18
=== T1-VERIFY-c ===
['control', 'dfm_models_data', 'edit_faces_enabled', 'models_processor', 'parameters', 'swap_faces_enabled', 'target_faces']
```

The vendoring script's own gate, which ran before any edit landed:

```
upstream lines: 410
all 24 edit sites verified against the copy
deleted upstream lines: [14, 44, 45, 128, 142, 149, 163, 170, 181, 214, 215, 216, 217, 218, 219, 220, 280, 283]
wrote D:/Dev/visoswap/visoswap/processors/models_processor.py 19701 bytes, 397 lines
CRLF preserved
```

Residual Qt or host-window references in the vendored file — the whole grep:

```
$ grep -n "main_window\|QtCore\|PySide\|Signal\|Slot\|QObject\|app\.ui" visoswap/processors/models_processor.py
grep-exit=1 (1 = no matches, good)
```

Gate coverage after narrowing `PENDING_QT_STRIP`:

```
modules probed: 22
sources scanned: 28
models_processor covered by import gate: True
PENDING_QT_STRIP: ['visoswap.processors.workers.frame_worker']
```

The new consumer-side test proven capable of failing, against the real vendored file rather
than a fixture — a read of a *dropped* attribute was injected, then reverted:

```
=== negative test ===
E  AssertionError: Vendored code reads context attributes that EngineContext does not define:
   {'visoswap\\processors\\models_processor.py': ['videoSeekSlider']}. ...
1 failed, 6 passed in 0.15s
=== restored ===
.......                                                                  [100%]
7 passed in 0.04s
restored byte-identical
```

Source material untouched:

```
$ find "D:/Visomaster/app" -newermt '-1 day' | wc -l
0
$ cd C:/Users/Sakat/.devswarm/repos/1/ef7f9c3d/config-parallel-setup && git diff --stat
(no output — no tracked file modified)
```

## Deviations from Plan

### 1. [Rule 3 - Blocking issue] The `TYPE_CHECKING` block was repointed, not deleted

- **Found during:** Task 2
- **Issue:** The plan says "remove the Qt import at line 14 **and the `if TYPE_CHECKING:` block
  at lines 36-37** that imports `MainWindow`", and then says to annotate the constructor
  `context: 'EngineContext'`. Doing both literally leaves `'EngineContext'` as a string
  annotation with no import anywhere that resolves it, and leaves `TYPE_CHECKING` imported from
  `typing` and unused.
- **Fix:** kept the block, replaced its one line with
  `from visoswap.processors.context import EngineContext`. No runtime import in either
  direction, so no cycle — `context.py` guards its `ModelsProcessor` import the same way. This
  is also a strictly smaller diff against upstream than deleting the block would have been,
  which is the property Task 2 exists to protect.
- **Files modified:** `visoswap/processors/models_processor.py`
- **Commit:** `9e859cf`

### 2. [Rule 2 - Missing critical functionality] A consumer-side check on the context contract

- **Found during:** Task 2
- **Issue:** `test_context_surface.py` pinned what `EngineContext` *offers*. Nothing pinned what
  the vendored code *asks for*. A read of a dropped attribute — `self.context.videoSeekSlider`,
  say — raises `AttributeError` at first call, deep inside model loading, with no import-time
  warning. Phase 1 executes none of these paths, so the Qt gate, the static scan and the AST
  check would all pass over it. The plan's own `key_links` names exactly this risk: "a field
  this file needs but the context lacks fails at import or first call."
- **Why critical:** it is the failure mode the plan predicted, in the phase where the code is
  never run, and it stays invisible until Phase 2 exercises the load path.
- **Fix:** `test_every_context_read_in_the_vendored_tree_resolves_to_a_field` — an `ast` scan
  (not an import, so it still runs with no engine dependencies) collecting every
  `<x>.context.<attr>` read and asserting each resolves to a real field, plus a non-vacuity
  guard. It covers `frame_worker.py`'s ~15 reads the moment plan 01-04 lands them, with no edit.
- **Files modified:** `tests/test_context_surface.py`
- **Commit:** `9e859cf`

### 3. [Rule 1 - Bug] The first version of that scan had a false positive

- **Found during:** Task 2, immediately on running it
- **Issue:** it flagged `utils/tensorrt_predictor.py` for reading `set_tensor_address`,
  `execute_v2`, `execute_async_v3`, `set_input_shape` and `set_input_consumed_event` off
  `self.context`. Those are correct: **`self.context` is not a unique name in the vendored
  tree** — TensorRT's execution context is also called `context`.
- **Fix:** scope the scan to files that name `EngineContext`. Keying on the import is what
  tells the two apart, and it is self-maintaining.
- **Worth carrying forward:** anyone reading `self.context` in this codebase must check which
  object they are looking at. Recorded here because it will not be obvious in Phase 2.
- **Files modified:** `tests/test_context_surface.py`
- **Commit:** `9e859cf`

### 4. A stale comment in `conftest.py` was corrected

`PENDING_QT_STRIP`'s docstring said plan 01-04's gate work is "deleting these two lines". After
this plan it is one line. Corrected in the same commit; the set itself is the plan's change.

## What Did Not Survive Contact

Three things. Two are the deviations above (the `TYPE_CHECKING` block, the `self.context` name
collision). The third:

- **The plan's line numbers were exactly right.** The pause handoff warned that plan line
  numbers "run one low" because the planner counted content lines. That was true of plan 01-02's
  *file lengths*, but not of plan 01-03's *edit sites*: all 24 — the import, the base class, the
  two Signals, the constructor, the store, eight emissions, two kept reads and the seven dialog
  lines — matched the upstream file exactly as stated. Verified by assertion, not by eye. Noted
  because plan 01-04 inherits the same warning and should not over-correct for it.

Also worth recording, since it looks like a failure and is not: the widened
`find "D:/Visomaster" -newermt '-1 day'` lists ~19 files under `D:/Visomaster/tensorrt-engines/`.
Their mtimes are all 00:19-00:32 local, roughly eighteen hours before this session, and they are
TensorRT engine-cache artifacts from a user-run of VisoMaster. `D:/Visomaster/app` — the tree
actually being vendored — lists nothing. Nothing in Phase 1 loads a model or builds an engine.

## What This Does NOT Prove

`ModelsProcessor` **imports** with every Qt binding root blocked. It has never been
**constructed**. `__init__` builds seven sub-processors, assembles model paths from
`models_dir`, and reads TensorRT provider options; none of that has run, and no model weight has
been loaded. The two surviving `self.context` reads —
`context.control['MaxDFMModelsSlider']` and `context.dfm_models_data[dfm_model]` — are on the
DFM load path, which Phase 1 does not execute. They are proven to *name real fields* (deviation
2) and nothing more.

`context.dfm_models_data` is additionally still empty by construction: nothing populates it,
because `get_dfm_models_data()` was deliberately not vendored. That is Note 2 of
`01-CONTEXT-SURFACE.md` and a Phase 2 decision, not a defect here.

## Deferred Deliberately (not oversights)

| Item | Location | Deferred to |
|------|----------|-------------|
| `models_dir = './model_assets'` hardcoded | `visoswap/models/models_data.py:6` | Phase 4 |
| `torch.load` without `weights_only=True` | `visoswap/processors/external/clipseg.py:305` | Phase 2, per T-01-06 |
| Model-load progress reporting | deleted from `models_processor.py` | any phase that wants it, via the `on_model_load` callback recorded in `01-CONTEXT-SURFACE.md` Note 1 + Note 3 |
| `visoswap.processors.workers.frame_worker` in `PENDING_QT_STRIP` | `tests/conftest.py:33` | plan 01-04, which empties the set |

## Threat Register Outcomes

| Threat ID | Disposition | Outcome |
|-----------|-------------|---------|
| T-01-09 | mitigate | `test_context_surface.py` asserts exactly seven fields in both directions, and now also asserts the consumer side. Widening the surface costs two reviewed test edits. |
| T-01-10 | accept | `context.control['MaxDFMModelsSlider']` and the DFM name keys are untyped dict reads, carried over verbatim. Typing is SCHEMA-01's job in Phase 3. Nothing in Phase 1 executes them. |
| T-01-11 | accept | Mitigated as designed: the deleted progress signalling is documented with a re-add path and, as of this plan, with the exact anchors to re-add it at. No silent no-op stub was left. |
| T-01-12 | accept | No package-manager install was performed. |

No new threat surface. `models_processor.py` opens no socket and reads no user-supplied path;
its filesystem reach is `models_dir`-relative, which is the Phase 4 item above.

## Known Stubs

None. The two zero-caller progress-bar methods were deleted rather than stubbed, which was the
explicit point of the plan's instruction not to leave a no-op. The commented-out
`# self.showModelLoadingProgressBar()` is upstream's own line, preserved for diff fidelity and
documented in `01-CONTEXT-SURFACE.md`, not a placeholder this project introduced.

## Self-Check: PASSED

All four created files verified present on disk. Both modified test files verified changed.
Both commits (`2bf871d`, `9e859cf`) verified in `git log`. Working tree clean. The vendored file
verified 397 lines, CRLF preserved, and its full diff against upstream verified to contain only
the header, the import map and the fourteen enumerated edit sites. `D:/Visomaster/app` verified
unmodified; `config-parallel-setup` verified to have no tracked modification.
