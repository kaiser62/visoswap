---
phase: 01-vendor-the-engine-strip-qt
plan: 01
subsystem: engine
tags: [vendoring, licensing, qt-strip, test-harness]
status: complete

requires: []
provides:
  - visoswap package skeleton (import-free __init__ files)
  - Qt-reachability probe with distinct exit codes (tests/_qt_guard_probe.py)
  - engine_python fixture + run_probe helper (tests/conftest.py)
  - canonical 4-line attribution header (tests/test_vendor_headers.py)
  - GPLv3 LICENSE, NOTICE, README
  - requirements-engine.txt (recipe for plan 01-04's clean-room build)
affects:
  - plans 01-02, 01-03, 01-04 all vendor through this harness

tech-stack:
  added: [pytest, setuptools]
  patterns:
    - "gate runs on a second interpreter via subprocess, not in-process"
    - "sys.meta_path finder raising ImportError, installed at index 0"
    - "failure modes separated by exit code so a missing dep is never a pass"
    - "byte-identical attribution header so the test matches exactly, not by pattern"

key-files:
  created:
    - pyproject.toml
    - requirements-engine.txt
    - LICENSE
    - NOTICE
    - README.md
    - visoswap/__init__.py
    - visoswap/processors/__init__.py
    - visoswap/processors/utils/__init__.py
    - visoswap/processors/utils/faceutil.py
    - tests/__init__.py
    - tests/conftest.py
    - tests/_qt_guard_probe.py
    - tests/test_qt_free.py
    - tests/test_vendor_headers.py
  modified: []

decisions:
  - "Block all seven Qt roots, not just PySide6 — qtpy raises QtBindingsNotFoundError independently of any direct binding import."
  - "Run the probe as a subprocess on the engine interpreter (which has PySide6 installed) — proving unreachability where Qt IS installed is strictly stronger than where it is absent."
  - "Check sys.modules for leaked Qt roots after EACH import, not only after all of them, so a leak is attributed to the module that caused it."
  - "A Qt root already in sys.modules at probe start reports IMPORT_ERROR (3), not QT_REACHED (1) — otherwise an environment fault is blamed on the module under test."
  - "Attribution header carries no per-file origin line, so it stays byte-identical and the gate matches exactly."
  - "NOTICE records the upstream revision as explicitly undeterminable, with the git error verbatim, rather than leaving provenance blank."

metrics:
  duration: ~25 min
  completed: 2026-08-21
  tasks: 2
  commits: 2
  files: 14

actuals:
  tokens: 37771
  tasks: 2
  commits: 2
---

# Phase 01 Plan 01: Package Skeleton, GPLv3, and the Qt-Reachability Gate Summary

The `visoswap` package now exists with GPLv3 licensing and an automated pytest gate that
proves no Qt binding is reachable from vendored engine code — demonstrated end to end on
one 2,389-line tracer module before plans 02-04 push ~10,000 more lines through it.

## What Was Built

**The gate (`tests/_qt_guard_probe.py`).** A standalone script, run as a subprocess and
never imported by pytest. It installs a `sys.meta_path` finder at index 0 that raises
`ImportError` for all seven Qt binding roots — `PySide6`, `PySide2`, `PyQt5`, `PyQt6`,
`qtpy`, `shiboken6`, `shiboken2` — then imports the modules named on argv and reports:

| Exit | Label | Meaning |
|------|-------|---------|
| 0 | `CLEAN` | every module imported, no blocked root in `sys.modules` |
| 1 | `QT_REACHED` | the block tripped, or a blocked root leaked into `sys.modules` |
| 2 | `DEPS_MISSING` | a `ModuleNotFoundError` named a root that is not blocked |
| 3 | `IMPORT_ERROR` | anything else, including a Qt root present before the probe armed |

Separating 0 from 2 is the whole point: an engine dependency dying on `import torch`
must never read as Qt cleanliness.

**The tracer.** `D:/Visomaster/app/processors/utils/faceutil.py` vendored byte for byte
(92,005 bytes, verified identical) to `visoswap/processors/utils/faceutil.py`, with the
four-line attribution header prepended. It has zero `app` imports and zero Qt references,
which is exactly why it was chosen — it isolates the harness from the import-rewriting
that plan 01-02 introduces.

**Licensing.** `LICENSE` is `D:/Visomaster/LICENSE` copied byte for byte (35,149 bytes,
GPLv3). `NOTICE` attributes VisoMaster, records provenance, and discloses the insightface
non-commercial restriction naming `genderage.onnx`. `README.md` repeats GPLv3 and the
insightface statement inline, so Phase 6 criterion 5 verifies rather than discovers.

**The header gate.** `tests/test_vendor_headers.py` walks every `.py` under `visoswap/`
and asserts an exact four-line header match, with a narrow exemption for `__init__.py`
files containing nothing but docstrings, comments and dunder assignments.

## Verification Results (verbatim)

Task 1:

```
$ python -m pytest tests/test_qt_free.py -x -q
...                                                                      [100%]
3 passed in 5.03s

$ "D:/Visomaster/dependencies/Python/python.exe" tests/_qt_guard_probe.py visoswap.processors.utils.faceutil
CLEAN
exit=0

$ "D:/Visomaster/dependencies/Python/python.exe" tests/_qt_guard_probe.py PySide6.QtCore
QT_REACHED:PySide6.QtCore:blocked Qt binding import: PySide6
exit=1

$ python tests/_qt_guard_probe.py visoswap.processors.utils.faceutil   # Miniconda 3.13, no torch
DEPS_MISSING:visoswap.processors.utils.faceutil:torch
exit=2
```

Task 2:

```
$ python -m pytest tests/test_vendor_headers.py -x -q
..                                                                       [100%]
2 passed in 0.02s

VERIFY LICENSE-header PASS      (head -3 LICENSE | grep "GNU GENERAL PUBLIC LICENSE")
VERIFY genderage PASS           (grep -i genderage NOTICE && README.md)
VERIFY attribution PASS         (grep -i visomaster NOTICE && grep -i GPL README.md)
```

Plan-level:

```
$ python -m pytest -q
.....                                                                    [100%]
5 passed in 8.69s

$ find D:/Visomaster/app -type f -name '*.py' -newermt '-1 day'
(no output)
```

Negative test — the header gate proven capable of failing:

```
exit code with header stripped: 1
E   AssertionError: vendored files missing the attribution header:
E       visoswap\processors\utils\faceutil.py
1 failed, 1 passed in 0.10s
restored byte-identical: True
```

## Deviations from Plan

### Auto-added functionality

**1. [Rule 2 - Missing critical functionality] Third test: `test_probe_blocker_is_armed`**
- **Found during:** Task 1
- **Issue:** The plan specifies two tests in `test_qt_free.py`, and covers "the blocker is
  armed, not inert" only as a one-off shell command in `<verify>`. That check therefore
  runs once, at execution time, and never again. If a future edit made the blocker inert —
  a typo in `BLOCKED_ROOTS`, a `find_spec` that returns instead of raises — every remaining
  test would still pass while testing nothing, and `pytest` would report green.
- **Why critical:** must_haves truth #1 is "an automated pytest gate fails if any Qt binding
  becomes reachable." A gate that cannot be shown capable of failing does not establish that
  truth. This is the same false-green class the plan's exit-code design exists to prevent.
- **Fix:** Added a third test running the probe against `PySide6.QtCore` and asserting exit 1
  plus a `QT_REACHED:PySide6.QtCore:` report.
- **Files modified:** `tests/test_qt_free.py`
- **Commit:** 8d0d4d9

**2. [Rule 2 - Missing critical functionality] `test_vendored_tree_is_not_empty`**
- **Found during:** Task 2
- **Issue:** The header test iterates `visoswap/**/*.py`. On an empty or mis-rooted tree it
  passes vacuously — `assert not missing` on an empty list is green.
- **Fix:** Added a guard asserting the package root exists and the file list is non-empty.
- **Files modified:** `tests/test_vendor_headers.py`
- **Commit:** 662d57c

### Design choices made within the plan's latitude

**3. Per-import leak check rather than only after all imports.** The plan says exit 1 when a
blocked root is in `sys.modules` "after all imports succeed." The probe checks after *each*
import instead. Strict superset of the specified behaviour, and it attributes a leak to the
module that caused it rather than to the batch.

**4. A pre-existing Qt root reports `IMPORT_ERROR`, not `QT_REACHED`.** If a `sitecustomize`
or `.pth` file imported Qt before the probe armed, reporting `QT_REACHED` would blame the
module under test for an environment fault. The probe detects this at startup and exits 3.

**5. `pyproject.toml` omits `readme`.** Adding it in Task 1 would have referenced a file that
does not land until Task 2, leaving commit 8d0d4d9 internally inconsistent. `license-files`
has the same shape and was kept because the plan assigns LICENSE/NOTICE to Task 2 by design;
the end state is correct either way.

**6. `from tests.conftest import ...` rather than `from conftest import ...`.** The plan's
file list includes `tests/__init__.py`, which makes `tests` a package, so pytest's prepend
import mode puts the repo root on `sys.path` and registers conftest as `tests.conftest`. The
bare import would have failed collection.

### Infrastructure

**7. [Rule 3 - Blocking] Created `.planning/STATE.md`.**
- **Found during:** post-execution state update
- **Issue:** The project was initialized without a `STATE.md`, so `state.advance-plan`,
  `state.update-progress`, `state.record-metric` and `state.rebuild` all returned
  `{"error": "STATE.md not found"}`. `gsd-tools` exposes no `state.init` verb, and
  `state.rebuild` cannot bootstrap from nothing.
- **Fix:** Created `STATE.md` from `templates/state.md` seeded with the real position,
  metrics and blockers, then let the SDK normalise it. `state.validate` now returns
  `{"valid": true, "warnings": [], "drift": {}}` and the record verbs work.
- **Files modified:** `.planning/STATE.md`
- **Commit:** (docs commit below)

No Rule 1 (bug) or Rule 4 (architectural) deviations. No auth gates. No package-manager
installs were performed — `requirements-engine.txt` is committed as a recipe only, per the
plan's T-01-03 disposition.

## What Did Not Survive Contact

Nothing in the plan was wrong. Two things worth recording for plans 02-04:

- **The measured facts held exactly.** The engine interpreter is 3.10.13 with torch 2.4.1+cu124
  *and* PySide6 6.7.2; `D:/Visomaster/.git/config` fails with `fatal: bad config line 1 in file
  .git/config` verbatim as predicted; `D:/Visomaster/LICENSE` is the GPLv3 text at 35,149 bytes.
- **Vendored files are CRLF.** `faceutil.py` is 2,389 CRLF lines with no BOM. The header is
  written with matching CRLF, and `test_vendor_headers.py` reads with universal newlines so a
  future LF-normalised vendored file still matches the same header text. Plan 01-02 vendors
  ~20 more files from the same tree and will hit the same line endings.

## Notes for Plans 02-04

- `visoswap/processors/__init__.py` is deliberately import-free. Adding an eager import before
  plan 01-04 de-Qt's `models_processor` and `frame_worker` would drag them into the gate early.
- Extend the gate by adding module names to the probe's argv — `run_probe(engine_python, [...])`
  takes a list. The plan's measured baseline says `models_data`, `face_detectors`, `face_masks`,
  `face_editors`, `face_swappers`, `utils.dfm_model` and `external.clipseg` should already come
  back `CLEAN`; only `models_processor` and `workers/frame_worker` should not.
- If a vendored file legitimately needs no header, do not widen the `__init__.py` exemption —
  it is narrow on purpose so vendored code cannot be smuggled past the gate by filename.

## Known Stubs

None. No placeholder values, no TODO/FIXME markers, and no unwired components were introduced.

## Self-Check: PASSED

All 14 created files verified present on disk. Both commits verified in `git log`.
Working tree clean, no untracked files. `D:/Visomaster/app` and the `config-parallel-setup`
worktree both verified unmodified.
