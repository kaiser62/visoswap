---
phase: 04-backend-integration-model-bootstrap
plan: 04
subsystem: backend
tags: [sever-visomaster, engine-01, model-ownership, open-guard, no-visomaster]
status: complete

requires:
  - 04-01 (backend port; source_face_path column; backend runs on the engine path)
  - 04-03 (manifest + verify machinery, used to verify the owned copy)
  - 02-04 (ENGINE-01 clause 1 evidence; models_data.py lists; TensorRT refusal)
provides:
  - visoswap/models/models_data.py -- env-driven models_dir with project-owned default (one edit site)
  - visoswap/schema/__init__.py -- DEFAULT_MODELS_DIR -> model_assets_owned
  - tools/copy_model_assets.py -- verified project-owned copy (56 files, 12.23 GB)
  - tests/_open_guard.py + --no-visomaster modes on both runners (runtime open guard)
  - tests/test_no_visomaster_install.py -- defaults + literal scan + guard non-vacuity
  - tests/conftest.py -- engine interpreter default -> .venv-clean
  - docs/no-visomaster-install.md
affects:
  - ENGINE-01 closes in BOTH clauses (owner to mark in REQUIREMENTS.md)
  - 04-04's owned copy makes 04-03's criterion-3 "real tree" = model_assets_owned
  - 05 (frontend) builds against a backend that never reads a VisoMaster folder
  - 02-UAT's deferred TensorRT follow-up unaffected (TRT still refused)

tech-stack:
  added: []
  patterns:
    - "the models directory resolves via MODELS_DIR with a project-owned default; exactly one definition"
    - "the seal's non-inertness proof builds its own app subject instead of requiring a VisoMaster checkout"
    - "a runtime open guard watches real opens (resolved paths) rather than link-ness, which is False for a junction"
    - "every severance is a default change plus an env override; nothing deleted or moved under the read-only tree"

key-files:
  created:
    - tools/copy_model_assets.py
    - tests/_open_guard.py
    - tests/test_no_visomaster_install.py
    - docs/no-visomaster-install.md
  modified:
    - visoswap/models/models_data.py
    - visoswap/schema/__init__.py
    - tests/conftest.py
    - tests/_engine_runner.py
    - tests/_backend_runner.py
    - tests/test_engine_seal.py
    - tests/test_model_manifest.py
    - docs/engine-test-assets.md
    - .gitignore

requirements-completed: [ENGINE-01, BACKEND-01]

coverage:
  - id: D1
    description: "The project owns its model set; the default models directory resolves outside any VisoMaster install and MODELS_DIR relocates every tracked path."
    requirement: ENGINE-01
    verification:
      - kind: unit
        ref: "tests/test_no_visomaster_install.py#test_default_models_dir_resolves_outside_any_visomaster_install"
        status: pass
      - kind: unit
        ref: "tests/test_no_visomaster_install.py#test_every_tracked_path_default_resolves_outside_visomaster"
        status: pass
    human_judgment: false
  - id: D2
    description: "A real swap and a full generation request complete with an open guard armed against every VisoMaster root, having observed real opens and having been shown capable of firing."
    requirement: ENGINE-01
    verification:
      - kind: e2e
        ref: "tests/test_no_visomaster_install.py#test_the_open_guard_observed_real_opens_and_fires"
        status: pass
      - kind: e2e
        ref: "tests/test_no_visomaster_install.py#test_the_backend_tracer_runs_clean_under_the_open_guard"
        status: pass
      - kind: unit
        ref: "tests/test_no_visomaster_install.py#test_the_open_guard_is_capable_of_firing"
        status: pass
    human_judgment: false
  - id: D3
    description: "ENGINE-01 clause 1 re-measured: the swap runs on the combined interpreter with PySide6 genuinely absent (pip show PySide6 -> not found)."
    requirement: ENGINE-01
    verification:
      - kind: e2e
        ref: "tests/test_no_visomaster_install.py#test_the_open_guard_observed_real_opens_and_fires (PySide6=no)"
        status: pass
    human_judgment: false
  - id: D4
    description: "No VisoMaster install path literal on the product path (backend/ + visoswap/), scanned over a non-zero examined-file count."
    requirement: ENGINE-01
    verification:
      - kind: unit
        ref: "tests/test_no_visomaster_install.py#test_no_visomaster_literal_on_the_product_path"
        status: pass
    human_judgment: false

metrics:
  duration: ~2h (12 GB verified copy + 3 tasks + engine E2E runs)
  completed: 2026-08-23
  tasks: 3
  commits: 1

actuals:
  tokens: 35000
  tasks: 3
  commits: 1
---

# Phase 04 Plan 04: Sever VisoMaster Dependency Summary

**The project owns its weights, its engine interpreter default, its test media and
its seal's proof subject; a runtime open guard proves no opened path resolves
inside a VisoMaster install -- closing ENGINE-01 in both clauses.**

## The fifth dependency (lead with this)

The phase brief named three dependencies and the roadmap named none; measurement
found **five**. The one the brief missed is the **face-source picker on the
product path**: the reference `config-parallel-setup` listed face sources from
`<visomaster_dir>/inputt` and served thumbnails from the same folder — a shipped
feature reading someone else's install, live. A summary reporting "the three
severances are done" against five would leave a shipped feature reading someone
else's install. The 04-01 port had already replaced it with a **server-assigned
`source_face_path` column** (stripped from the public API), so this plan verified
rather than re-fixed it: no `inputt` and no `visomaster_dir` reference remains on
the product path, asserted by `test_no_visomaster_literal_on_the_product_path`.

The other four, each changed here:

1. **The weights** — `models_data.py:6` now reads `MODELS_DIR` with a project-owned
   default (`model_assets_owned/`). `tools/copy_model_assets.py` made a verified
   copy (56 files, 12.23 GB, all matching their manifest digests; `guard_opens`
   comes from the swap reading the owned copy). The junction is left in place.
2. **The engine interpreter default** — `conftest.DEFAULT_ENGINE_PYTHON` -> the
   project's own `.venv-clean/Scripts/python.exe` (no PySide6, so the default run
   proves clause 1). The Qt-carrying run remains reachable by override.
3. **The seal's proof subject** — the runner builds a minimal self-contained `app`
   package, so `test_the_seal_is_not_inert_against_visomaster` holds on any
   machine with no VisoMaster install.
4. **The test media** — `DEFAULT_TEST_VIDEO`/`DEFAULT_TEST_SOURCE` move to
   `tests/media/` (gitignored), so the smoke fixtures no longer default into a
   borrowed tree. A missing file stays `ASSET_MISSING (3)`, never a skip.

## ENGINE-01, both clauses, stated together

**Clause 1, re-measured not inherited:** on `.venv-clean`,
`pip show PySide6` reports `Package(s) not found: PySide6`, and the no-visomaster
swap completes with `reachable_before_seal` showing `PySide6=no` and `app=yes`
(from the self-built subject).

**Clause 2, newly true:** the same swap and a full generation request complete
with the open guard armed against every VisoMaster root, having observed real
opens (`guard_opens=11` engine / `guard_opens=67` tracer) and having been shown
capable of firing against a planted forbidden root.

```
$ .venv-clean/Scripts/python.exe -m pip show PySide6        # "not found" -> clause 1
$ .venv-clean/Scripts/python.exe -B tests/_engine_runner.py --no-visomaster
  CLEAN:smoke:... provider=CUDA ... PySide6=no,app=yes ... guard_opens=11
$ .venv-clean/Scripts/python.exe -B tests/_backend_runner.py --tracer --no-visomaster
  CLEAN:tracer:frame=000000.000.jpg ... PySide6=no ... guard_opens=67
```

The `islink`-versus-`realpath` trap is recorded: `os.path.islink('model_assets')`
is `False` for the junction while `realpath` resolves it into `D:/Visomaster`, so
every severance assertion is written against the resolved path. The junction is
deliberately left in place (removing it is the one action that cannot be undone by
hand); the *default* pointing at it was what made clause 2 false, and that is gone.

## Task Commits

1. **Tasks 1-3 (one atomic implementation commit)** - `ffc85e0` (feat)

## Files Created/Modified

- `tools/copy_model_assets.py` - verified project-owned copy (refuses any
  destination inside a VisoMaster install; copy-not-move; verifies against the
  manifest; copies the extra `.pkl` runtime assets)
- `tests/_open_guard.py` - runtime open guard (wraps `open`/`os.open`, resolves
  paths, raises inside a forbidden root, counts opens, proves it can fire)
- `tests/test_no_visomaster_install.py` - defaults resolve outside VisoMaster,
  MODELS_DIR relocates paths, literal scan, guard non-vacuity (both halves)
- `docs/no-visomaster-install.md` - the five severances, the junction decision,
  the islink trap, the open guard, both clause measurements
- `visoswap/models/models_data.py` - env-driven `models_dir` (one edit site)
- `visoswap/schema/__init__.py` - `DEFAULT_MODELS_DIR` -> `model_assets_owned`
- `tests/conftest.py` - engine interpreter default -> `.venv-clean`
- `tests/_engine_runner.py`, `tests/_backend_runner.py` - project-owned media
  defaults, self-built seal subject, `--no-visomaster` modes, `resolve_models_dir`
- `tests/test_engine_seal.py` - self-built-subject non-inertness proof
- `tests/test_model_manifest.py` - traversal test follows the new models_dir default
- `docs/engine-test-assets.md`, `.gitignore` - updated defaults and edit sites

## Decisions Made

- The project owns its weights by a **verified copy** (never a move, never touching
  the junction); the junction stays because removing it is irreversible.
- The engine interpreter default flips to `.venv-clean` (no PySide6), making clause
  1 the default proof; the Qt-carrying seal run stays reachable by override.
- The seal's non-inertness proof builds its own `app` subject — strictly stronger
  than relying on a VisoMaster checkout, and holds on any machine.
- The face-source picker (dependency #5) needed no code change because the 04-01
  port already replaced `visomaster_dir/inputt` with `source_face_path`.

## Deviations from Plan

None materially. The face-source picker (#5) required verification rather than a
code change, since the 04-01 port had already severed it.

## Issues Encountered

- The owned copy initially omitted the extra `.pkl` runtime assets (`lip_array.pkl`,
  `meanshape_68.pkl`), breaking the LivePortrait face editor; the copy tool was
  extended to copy them.
- The runners hardcoded `model_assets` in several places; replaced with
  `resolve_models_dir()` so they follow `MODELS_DIR`/the owned default.
- `os.statvfs` is Unix-only; the copy tool's free-space check uses
  `shutil.disk_usage` on Windows.

## Next Phase Readiness

- ENGINE-01 is satisfied in both clauses; marking it in REQUIREMENTS.md is left to
  the owner (per the convention plans 02-04/03-02 followed).
- Phase 4 (all four plans) is complete; Phase 5 (frontend) builds against a backend
  that owns its models and never reads a VisoMaster folder.
- 04-03's criterion-3 "real tree" is now `model_assets_owned` (verified by hash).
