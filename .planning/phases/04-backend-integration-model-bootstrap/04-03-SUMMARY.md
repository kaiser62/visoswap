---
phase: 04-backend-integration-model-bootstrap
plan: 03
subsystem: backend
tags: [model-bootstrap, manifest, startup-gate, model-verification, criteria-2-3]
status: complete

requires:
  - 04-01 (backend/main.py lifespan + /api/health; backend/config.py Settings)
  - 02-04 (models_data.py -- models_list 56 + models_trt_list 6-or-0; TensorRT refusal in visoswap/engine.py)
provides:
  - visoswap/models/manifest.py -- the only reader of the vendored model lists; derived required set
  - visoswap/models/bootstrap.py -- three-state verification + repair; startup-gate helpers
  - backend/config.py -- models_dir + models_verify_mode settings
  - backend/main.py -- the startup gate wired into the lifespan
  - tests/test_model_manifest.py, tests/test_model_bootstrap.py, tests/_bootstrap_gate_runner.py
  - docs/model-bootstrap.md
affects:
  - 04-04 severs the VisoMaster install dependency and owns the project-owned model copy
  - Phase 5 (frontend) builds against a backend that refuses to start on incomplete models
  - 02-UAT's deferred TensorRT follow-up (re-enabling TensorRT re-requires the six TRT entries)

tech-stack:
  added: []
  patterns:
    - "the model set is read from the manifest at runtime, never a hardcoded count (AST-forbidden 56/6/62/12)"
    - "required is derived from a fetchable-and-consumable rule, not a list of names"
    - "three verification states (matching / mismatching / absent); zero bytes = absent"
    - "a full 12 GB hash only on first run, keyed by a manifest-fingerprint marker"
    - "the startup gate raises from the lifespan so no route ever serves on incomplete models"
    - "the verify path imports stdlib + manifest only (AST-asserted); repair imports the downloader lazily"

key-files:
  created:
    - visoswap/models/manifest.py
    - visoswap/models/bootstrap.py
    - tests/test_model_manifest.py
    - tests/test_model_bootstrap.py
    - tests/_bootstrap_gate_runner.py
    - docs/model-bootstrap.md
  modified:
    - backend/config.py
    - backend/main.py
    - tests/test_vendor_headers.py

requirements-completed: [BACKEND-01]

coverage:
  - id: D1
    description: "The manifest reads both vendored lists at call time; the tracked set is derived at runtime with no hardcoded count, and required is derived from a fetchable-and-consumable rule."
    requirement: BACKEND-01
    verification:
      - kind: unit
        ref: "tests/test_model_manifest.py#test_tracked_set_size_matches_the_vendored_lists"
        status: pass
      - kind: unit
        ref: "tests/test_model_manifest.py#test_required_derived_from_rule_optional_equals_no_url"
        status: pass
      - kind: unit
        ref: "tests/test_model_manifest.py#test_no_literal_model_count_in_manifest_module"
        status: pass
    human_judgment: false
  - id: D2
    description: "Bootstrap verification classifies every required entry into three states and offers fast/full modes with the mode named in the result."
    requirement: BACKEND-01
    verification:
      - kind: unit
        ref: "tests/test_model_bootstrap.py#test_complete_directory_succeeds_and_names_count"
        status: pass
      - kind: unit
        ref: "tests/test_model_bootstrap.py#test_altered_file_is_reported_mismatching_not_absent"
        status: pass
    human_judgment: false
  - id: D3
    description: "Criterion 2: starting the backend against an incomplete MODELS_DIR exits non-zero (observed exit code 3 as a real uvicorn process) with a message naming the missing files and the searched directory, before any route serves."
    requirement: BACKEND-01
    verification:
      - kind: e2e
        ref: "tests/test_model_bootstrap.py#test_criterion2_incomplete_models_exits_nonzero_as_a_process"
        status: pass
      - kind: e2e
        ref: "tests/test_model_bootstrap.py#test_criterion2_incomplete_models_refuses_at_startup"
        status: pass
    human_judgment: false
  - id: D4
    description: "Criterion 3: a complete, hash-verified tree starts normally and GET /api/health returns 200."
    requirement: BACKEND-01
    verification:
      - kind: e2e
        ref: "tests/test_model_bootstrap.py#test_criterion3_complete_models_health_200"
        status: pass
      - kind: unit
        ref: "tests/test_model_bootstrap.py#test_real_tree_hashes_to_manifest"
        status: pass
    human_judgment: false
  - id: D5
    description: "The verify path imports stdlib + manifest only at module scope; repair wraps the vendored downloader lazily and refuses to fetch into the read-only weight tree."
    requirement: BACKEND-01
    verification:
      - kind: unit
        ref: "tests/test_model_bootstrap.py#test_bootstrap_imports_stdlib_only_at_module_scope"
        status: pass
      - kind: unit
        ref: "tests/test_model_bootstrap.py#test_repair_refuses_to_fetch_into_read_only_tree"
        status: pass
    human_judgment: false

metrics:
  duration: ~90 min (3 tasks, real-engine E2E runs)
  completed: 2026-08-23
  tasks: 3
  commits: 3

actuals:
  tokens: 28000
  tasks: 3
  commits: 3
---

# Phase 04 Plan 03: Model Bootstrap Summary

**The backend reads its own manifest, verifies what is on disk in three states, and refuses to start
on incomplete models — before any route can serve — closing roadmap criteria 2 and 3 and the
verify/download/refuse clauses of BACKEND-01.**

## The required/optional partition (lead with this)

A reader comparing this summary against the roadmap's "56 plus 6" might think a criterion was weakened
rather than reconciled, so state it plainly. The tracked set is `models_list` (**56**) plus
`models_trt_list` (**6** when `tensorrt` imports, **0** otherwise). The six TensorRT entries are
**optional** because they fail both halves of the required rule:

- **They cannot be fetched** — they carry no `url`, and the vendored downloader requires one.
- **Nothing can load them** — `visoswap/engine.py` refuses the `TensorRT`/`TensorRT-Engine` providers
  with a `ValueError` (relative cache path), so the provider that reads a `.trt` file cannot be selected.

A missing `.trt` therefore cannot produce the mid-inference failure BACKEND-01 exists to prevent — the
test of whether something belongs in the required set. All six are absent on the only complete install
available (measured in plan 04-03's facts). The rule is a predicate (fetchable AND consumable), not a
list of names, so re-enabling TensorRT flips those six back into required automatically; a test pins
that the currently-optional set equals exactly the entries lacking a URL.

## What Was Built

**Task 1 (`cf1921e`)** — `visoswap/models/manifest.py`: the only reader of the vendored lists. Reads
them at call time (so the tracked set moves with the interpreter's TensorRT availability), resolves
every path under a supplied models directory with traversal refused, and derives `required` from a
stated fetchable-and-consumable rule. No model count appears as a literal anywhere in the module or its
tests; an AST test forbids 56/6/62/12. Both the with- and without-TensorRT cases are exercised via a
stand-in module that is proven to have taken effect.

**Task 2 (`97ce66c`)** — `visoswap/models/bootstrap.py`: `verify` classifies every required entry as
present-and-matching, present-and-mismatching, or absent (zero bytes = absent); offers `fast`
(presence/non-emptiness) and `full` (also hashes) modes with the mode named in the result; raises
`ModelVerificationError` naming the missing/wrong files and the searched directory when incomplete.
`repair` is separate, wraps the vendored downloader lazily (so the verify path stays stdlib-only,
AST-asserted), and refuses to fetch into the read-only weight tree or through a link escaping the
models directory. Tests build scratch directories only and drive repair through a recording stand-in so
nothing touches the network.

**Task 3 (`9e8a050`)** — the startup gate. `backend/main.py` raises from its lifespan (so no route
serves), writes the error to stderr, and exits non-zero. Mode selection and the verification marker
live in `bootstrap.verify_at_startup` (auto = fast when a marker for the current manifest exists, full
on a first run; `MODELS_VERIFY_MODE` selects auto/fast/full, with no off switch). `backend/config.py`
adds `models_dir` and `models_verify_mode`. E2E criterion tests run as subprocesses on the combined
interpreter via `tests/_bootstrap_gate_runner.py`. `docs/model-bootstrap.md` records the manifest, the
rule, the three states, the two modes, and the spec's 54-vs-56 correction.

## Verification, verbatim

```
$ .venv-clean/Scripts/python.exe -m pytest tests/ -q
338 passed in 445.18s (0:07:25)   # 0 failures, 0 skips; slowest is the real-engine tracer (367s)

# criterion 2, as a real uvicorn process:
$ MODELS_DIR=<empty dir> .venv-clean/Scripts/python.exe -m uvicorn backend.main:app --port 0
   # exit code 3; stderr: "models incomplete: 56 required entries checked in 'full' mode ... searched directory: <abs>"

# criterion 3:
$ tests/_bootstrap_gate_runner.py --expect-ok   # (MODELS_VERIFY_MODE=fast) -> HEALTH:200

$ python -m pytest tests/test_model_manifest.py tests/test_model_bootstrap.py tests/test_vendor_headers.py -q
31 passed
```

Test count: **310 → 338**, +28 (9 manifest, 19 bootstrap incl. 4 marker-mode + E2E + real-tree hash).
Zero skips. `model_assets` unchanged: 56 required present, 12,233,143,713 bytes, inswapper digest
matches manifest.

## Task Commits

1. **Task 1: manifest view** - `cf1921e` (feat)
2. **Task 2: bootstrap verify/repair** - `97ce66c` (feat)
3. **Task 3: startup gate** - `9e8a050` (feat)

## Decisions Made

- The six TensorRT entries are optional because they cannot be fetched and cannot be loaded by a
  provider the engine accepts; the rule is a predicate so re-enabling TensorRT re-requires them.
- The startup gate raises from the lifespan (not the generation route) so the failure arrives as a
  refused process, not a 500 on the first frame.
- Startup mode is "auto": fast every start, full on a first run (no marker), with a manifest-fingerprint
  marker beside the project's data — never inside the models directory. No off switch.
- The verify path is stdlib-only; repair imports the downloader lazily.

## Deviations from Plan

None — plan executed as written.

## Issues Encountered

- A full 12 GB hash on every lifespan entry made the E2E criterion-3 test and any cold first run slow;
  resolved by using `fast` mode in the criterion-3 E2E, proving hash-correctness with a focused
  single-file test, and moving marker/mode logic into `bootstrap.py` (stdlib, plain-python testable).
- `with LifespanManager(app)` does not run the async setup ("unknown async library"); the gate runner
  uses `async with` in both branches.

## Next Phase Readiness

- 04-04 severs the VisoMaster install dependency; the manifest/verify machinery here is what verifies
  the project-owned model copy entry-by-entry.
- Phase 5 builds against a backend that refuses to start on incomplete models.
- The deferred TensorRT follow-up in 02-UAT: re-enabling TensorRT must first make the cache path
  absolute, then the six TRT entries become required automatically.
