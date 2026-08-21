---
phase: 01-vendor-the-engine-strip-qt
plan: 04
subsystem: engine
tags: [qt-strip, vendoring, frame-worker, verification, clean-room]
status: complete

requires:
  - 01-01 (attribution header, Qt-reachability probe, conftest harness, vendor-header gate)
  - 01-02 (the 26 vendored Qt-free modules and the import rewrite map)
  - 01-03 (EngineContext and the 7-field context surface)
provides:
  - visoswap/processors/workers/frame_worker.py (the swap pipeline, vendored and de-Qt'd)
  - tests/test_dropped_modules.py (video_processor absence, backend/app non-import, by ast)
  - tests/test_qt_free.py (the gate over packages and every submodule)
  - PENDING_QT_STRIP emptied -- the phase's closing condition
  - docs/verifying-qt-free.md (the repeatable clean-room procedure, for Phase 6)
  - .venv-clean/ (gitignored; the Qt-free interpreter the checkpoint was run on)
affects:
  - Phase 2 builds Engine.swap on the frame_worker seam this plan left at self.frame
  - Phase 6 re-runs the clean-room procedure recorded here as its final gate

tech-stack:
  added: []
  patterns:
    - "prove absence, not just blockage: a meta_path blocker is inert where Qt is genuinely uninstalled, so the clean-room check imports with no blocker at all and asserts sys.modules stays free of every Qt root"
    - "empty the pending-exclusion set and pin the emptiness, so re-opening it costs an argument rather than a quiet frozenset edit"

key-files:
  created:
    - visoswap/processors/workers/frame_worker.py
    - tests/test_dropped_modules.py
    - docs/verifying-qt-free.md
  modified:
    - tests/conftest.py
    - tests/test_qt_free.py
    - visoswap/processors/__init__.py
---

# Phase 01 Plan 04: De-Qt `frame_worker.py` and Close the Phase Summary

The vendored tree imports on an interpreter where Qt is not installed at all. Thirty modules,
no blocker armed, nothing from PySide6 in `sys.modules`. That was the phase goal and it holds
against measurement rather than against a mock.

## What Was Built

**Task 1 — `frame_worker.py`, vendored and de-Qt'd** (commit `54c7321`). 1307 upstream lines in,
1293 out. The diff is the attribution header, the import rewrite map, and 49 asserted edit
sites, and nothing else.

Both `app/ui` action imports went, along with their only two call sites: the timeline-marker
update and the display-path pixmap conversion. `main_window` became `EngineContext` throughout.
The five button reads at upstream lines 48, 137, 163, 170 and 183 became `swap_faces_enabled`
and `edit_faces_enabled` — they were only ever booleans. `video_processor` and its display path
(upstream 55-78) were dropped whole, leaving the processed frame on `self.frame` as the seam
Phase 2 picks up. The `frame_queue` constructor parameter went too: never read outside
`__init__`.

**Task 2 — close the gate** (commit `208e8f0`). `PENDING_QT_STRIP` is now `frozenset()`, and
`test_nothing_is_excluded_from_the_gate` pins it there. The probe is handed package names *and*
every submodule in one run, so the roadmap's literal `import visoswap.processors` is covered
without making an import-free `__init__.py` lie. `tests/test_dropped_modules.py` checks three
absences with `ast` rather than grep: no `video_processor` artefact on disk, no `video_processor`
reference in code, no `backend`/`app` import anywhere.

**Task 3 — the clean-room checkpoint.** Approved by the project owner 2026-08-21 on the evidence
below.

## The Checkpoint, and Why It Needed More Than the Plan Asked

The plan's step 4 was: run `tests/_qt_guard_probe.py` on the clean interpreter, expect `CLEAN`.

That is necessary but it does not prove what this checkpoint exists to prove. The probe works by
arming a `sys.meta_path` finder that refuses seven Qt roots. On VisoMaster's interpreter, where
PySide6 6.7.2 is installed, that blocker is doing real work. In a venv where Qt is *absent*, the
blocker never fires — there is nothing to block. `CLEAN` from the probe alone would therefore be
a weaker signal here than it is on the interpreter the plan was trying to improve on.

The specific failure it cannot see: a module doing `try: import PySide6 / except ImportError:
pass` passes the blocked gate (the block raises, the module swallows it) *and* passes on a
Qt-free machine (the import fails, the module swallows it), while silently running degraded. So
the check was run a second way, with **no blocker armed at all** — import all thirty modules on
an interpreter where Qt does not exist, then assert no Qt root appears in `sys.modules`. A
swallowed import shows up there as a module that imported "successfully" with Qt conspicuously
missing from the modules it should have loaded.

Both ways pass.

## Verification Results (verbatim)

```
$ .venv-clean/Scripts/python -V
Python 3.10.19

$ .venv-clean/Scripts/pip install -r requirements-engine.txt
exit 0   (47 packages; tensorrt built via setup.py)

$ .venv-clean/Scripts/pip show PySide6
WARNING: Package(s) not found: PySide6
```

`importlib.util.find_spec` reports `absent` for all seven roots — PySide6, PySide2, PyQt5,
PyQt6, qtpy, shiboken6, shiboken2.

```
$ .venv-clean/Scripts/python tests/_qt_guard_probe.py <30 modules>
CLEAN            exit=0

$ .venv-clean/Scripts/python bare_import.py          # no blocker armed
imported 30/30  failures=0  qt_in_sys_modules=[]   exit=0

$ VISOSWAP_ENGINE_PYTHON=.../.venv-clean/Scripts/python.exe python -m pytest tests/ -q
25 passed
```

The suite genuinely ran against the clean interpreter: `resolve_engine_python`
(`tests/conftest.py:117`) returns the `VISOSWAP_ENGINE_PYTHON` override verbatim with no
fallback, so a bad path would have errored rather than silently reverting to the Qt-bearing
default.

`bare_import.py` was a throwaway written to the scratchpad, not committed. Its procedure is
recorded in `docs/verifying-qt-free.md` so Phase 6 re-runs it rather than reconstructing it.

## Deviations

**One, and it widened the check rather than narrowing it.** The plan specified the probe run;
the blocker-free import was added on top for the reason argued above. Nothing the plan asked
for was skipped.

`pytest` is not in `requirements-engine.txt` — correct, it is a runtime file — so the suite runs
on the normal interpreter with `VISOSWAP_ENGINE_PYTHON` pointed at the clean venv, which is the
path the plan itself offered as the simplest.

## Phase Goal

Met. Every module under `visoswap/` imports with no Qt installed, `PENDING_QT_STRIP` is empty,
`video_processor.py` is gone with no residue, and nothing in the engine imports `backend` or
`app`.

## What Phase 2 Inherits

The processed frame sits on `self.frame` at the end of the worker's pipeline — that is the seam
`Engine.swap` builds on. `EngineContext.dfm_models_data` still has no populator; per
`02-DECISION-deferred-paths.md` that is Phase 4's, not Phase 2's. And `ModelsProcessor` still
defaults to TensorRT, whose execution-provider options write to a *relative* `tensorrt-engines/`
directory — on a wrong working directory that writes into the read-only source tree. Plan 02-02
locks it to CUDA.
