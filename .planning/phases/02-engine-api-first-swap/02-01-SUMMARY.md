---
phase: 02-engine-api-first-swap
plan: 01
subsystem: engine-harness
tags: [test-harness, assets, settings, seal, verification]
status: complete

requires:
  - 01-01 (attribution header, Qt-reachability probe, conftest harness)
  - 01-02 (the vendored Qt-free modules and the import rewrite map)
  - 01-04 (PENDING_QT_STRIP emptied; the `self.frame` seam; the not-inert lesson)
provides:
  - tools/link_model_assets.py (12GB reachable by junction/symlink, zero bytes copied)
  - tests/fixtures/engine_settings.json (168 project + 33 global keys, typed)
  - tools/dump_engine_settings.py (the one-shot offline generator, Qt required)
  - tests/_blocked_roots.py (the single definition of every sealed package root)
  - tests/_engine_runner.py (the sealed subprocess; every later plan runs inside it)
  - tests/conftest.py::run_engine_runner (the pytest-side driver)
  - docs/engine-test-assets.md (where each asset comes from and what breaks if wrong)
affects:
  - 02-02 pins the ONNX Runtime provider to CUDA and runs inside this runner
  - 02-03 exercises LivePortrait through this runner and this fixture
  - 02-04 needs DFMModelSelection to start empty, which the fixture now records
  - Phase 3 replaces the fixture with the generated visoswap/schema/schema.json
  - Phase 4 makes models_dir env-driven, retiring the CWD dependency documented here

tech-stack:
  added: []
  patterns:
    - "prove the seal is not inert: put the blocked package on sys.path before arming, measure reachability first, and report it -- an inert blocker returns the same CLEAN as a working one"
    - "one dependency-free module holds a block list that both a pytest-side and a no-pytest-side consumer read, rather than a second copy or a conftest that pretends pytest is optional"
    - "type by widget shape, never by the type of `default` -- reading the type of a value that is a string 138 times out of 201 just returns str"
    - "exercise every exit code: an exit code nobody has watched fire is an exit code nobody knows the meaning of"

key-files:
  created:
    - tools/link_model_assets.py
    - tools/dump_engine_settings.py
    - tests/fixtures/engine_settings.json
    - tests/_blocked_roots.py
    - tests/_engine_runner.py
    - tests/test_engine_settings_fixture.py
    - tests/test_engine_seal.py
    - docs/engine-test-assets.md
  modified:
    - tests/conftest.py
    - tests/_qt_guard_probe.py

decisions:
  - "Five widget shapes exist, not four, and there are 93 int keys, not 94. ClipText carries min/max with no step -- character bounds on a line edit -- and its default is ''. The planned rule does not mistype it, it crashes the generator on int(float(''))."
  - "The block lists live in tests/_blocked_roots.py rather than inline in conftest.py: both subprocess consumers run on the engine interpreter, which has no pytest. conftest re-exports them, so there is still exactly one definition."
  - "The backend scan uses ast rather than the tokenize comment-stripping of test_no_qt_source.py. The question is 'is this an import statement', which is exactly what the grammar answers, so a removal note is a non-hit by construction rather than by filtering."
  - "The runner puts the VisoMaster checkout on sys.path before arming the seal, so the visomaster group is proven against a package that genuinely resolves. backend=no is reported honestly -- that group cannot be shown non-inert until Phase 5."

metrics:
  duration: ~55 minutes
  completed: 2026-08-21
  tasks: 3
  commits: 4

actuals:
  tokens: 41000
  tasks: 3
  commits: 4
---

# Phase 02 Plan 01: The Phase 2 Harness Summary

Three things Phase 2's first real swap cannot run without, each proven on its own
before any engine code exists to blame: 12GB of weights reachable without a byte
copied, 201 settings extracted once and typed by widget shape, and a runner that
executes with Qt, VisoMaster and the backend all made unimportable — the last one
proven against packages that genuinely resolve rather than against packages that
were never there.

## What Was Built

**Task 1 — the weight link** (commit `fa0fc8e`). `tools/link_model_assets.py`,
standard library only. A Windows junction (`mklink /J`, no administrator rights,
paths passed as argv rather than interpolated into a shell string) or a POSIX
symlink, chosen from `os.name`. `--check` opens all nine probe files rather than
`stat`ing them, because a junction into a tree with a denied ACL lists fine and
reads nothing.

The three refusals matter more than the creation: it refuses an existing real
directory (T-02-01 — overwriting weights is the one unrecoverable mistake here),
refuses an existing link pointing somewhere else, and is a no-op success over a
link already pointing at the resolved source. All four failure paths were
exercised against a temporary tree, not assumed.

**Task 2 — the typed settings pair** (commits `08171e8` RED, `1b70d6c` GREEN).
`tools/dump_engine_settings.py` runs on the Qt interpreter, flattens the four
layout dicts and writes `tests/fixtures/engine_settings.json`: 168 project keys,
33 global, zero overlap, sorted with a trailing newline so a regeneration diffs
as the one value that moved.

The typing is the whole point. Every layout `default` is a string —
`ClipAmountSlider` is `'50'`, `FaceEditorCropScaleDecimalSlider` is `'2.50'` —
and Qt widgets coerce internally, so nothing upstream had to care. The engine
fails deep inside a tensor op instead. Values are coerced by **widget shape**,
never by the Python type of `default`, and `int` goes through `float()` first so
`'2.50'` survives.

`tests/test_engine_settings_fixture.py` runs on the plain 3.13 developer
interpreter with neither torch nor Qt. It cannot re-derive the shape rule — that
needs the layout dicts, which need Qt — so it asserts the rule's *checkable
shadow*: a full value-type census, plus the sharpest statement of the failure
mode, "no string in this file parses as a number", with four named dropdowns as
the only exemption.

**Task 3 — the seal** (commit `6a1c9f0`). `tests/_engine_runner.py` installs a
`sys.meta_path` finder at index 0 refusing the seven Qt roots, VisoMaster's `app`
and VisoSwap's `backend`, before anything else is imported. Exit vocabulary
0/1/2/3/4 so a harness fault is never readable as an engine pass, and exactly one
`LABEL:mode:detail` line printed before exit.

The block lists were lifted into `tests/_blocked_roots.py` and the probe's own
copy deleted, so the two gates now cannot diverge (T-02-05).

## The Part That Needed More Than the Plan Asked

The plan's self-test is "arm the seals, assert each of the three groups actually
raises when provoked". A `meta_path` finder at index 0 raises on the *name*,
before any path search — so that assertion passes identically whether the blocked
package is installed or has never existed on the machine. It is the exact failure
plan 01-04 caught in the Qt gate: **an inert blocker reports the same `CLEAN` as
a working one.**

So the runner measures reachability *before* arming (`find_spec` afterwards would
trip the seal), appends the VisoMaster checkout to `sys.path` first so `app`
genuinely resolves, provokes each group with a root that actually exists where one
does, and reports the whole picture:

```
reachable_before_seal=PyQt5=no,PyQt6=no,PySide2=no,PySide6=yes,app=yes,backend=no,qtpy=yes,shiboken2=no,shiboken6=yes
```

`PySide6=yes` and `app=yes` are the seal refusing packages that really were there.
`backend=no` is stated rather than hidden: that package does not exist until Phase
5, so its seal is armed and provably fires on the name but cannot yet be shown
non-inert. `test_the_seal_is_not_inert_against_visomaster` asserts `app=yes` and
says in its own docstring which of the three groups is which.

## Verification Results (verbatim)

```
$ python tools/link_model_assets.py --check
source: D:\Visomaster\model_assets (from default)
OK: D:\Dev\visoswap\model_assets -> D:\VisoMaster\model_assets (9 probe files readable)
exit=0

$ python -m pytest tests/ -q
45 passed

$ VISOSWAP_ENGINE_PYTHON=.../.venv-clean/Scripts/python.exe python -m pytest tests/ -q
45 passed

$ "D:/Visomaster/dependencies/Python/python.exe" -B tests/_engine_runner.py --selftest
CLEAN:selftest:groups=qt+visomaster+backend settings=project:168,global:33 reachable_before_seal=PyQt5=no,PyQt6=no,PySide2=no,PySide6=yes,app=yes,backend=no,qtpy=yes,shiboken2=no,shiboken6=yes
exit=0

$ "D:/Visomaster/dependencies/Python/python.exe" -B tools/dump_engine_settings.py \
    && git diff --exit-code tests/fixtures/engine_settings.json
visomaster: D:\Visomaster (from default)
WROTE: D:\Dev\visoswap\tests\fixtures\engine_settings.json (project=168 global=33 types={'bool': 43, 'str': 23, 'float': 42, 'int': 93})
callable defaults resolved to empty string: DFMModelSelection
exit=0
```

Every runner exit code, by hand:

```
--import PySide6.QtCore                     SEAL_BREACHED:import:PySide6.QtCore:sealed import refused: PySide6           exit=1
--import app.ui.widgets.common_layout_data  SEAL_BREACHED:import:...:sealed import refused: app                          exit=1
--import backend                            SEAL_BREACHED:import:backend:sealed import refused: backend                  exit=1
--import json                               CLEAN:import:json:imported                                                   exit=0
--import definitely_not_a_module            DEPS_MISSING:import:definitely_not_a_module:No module named '...'            exit=2
(no arguments)                              ENGINE_ERROR:-:usage: ...                                                    exit=4
```

`ASSET_MISSING` (3) fires in `test_a_missing_fixture_reports_asset_missing_not_clean`
via the `VISOSWAP_SETTINGS_FIXTURE` override.

### Read-only source trees

The plan's step 5 asks that
`find "D:/Visomaster" -newermt '-1 day' -not -path '*/dependencies/*'` print
nothing. **It does not**, and the reason is worth recording rather than papering
over: twenty-one paths under `D:/Visomaster/tensorrt-engines/` carry mtimes
between 00:19 and 00:32 on 2026-08-21, hours before this plan began. They are
TensorRT provider cache artefacts from an earlier VisoMaster run, and the nested
`tensorrt-engines/tensorrt-engines/` among them is the signature of exactly the
wrong-CWD hazard this plan documents. Not caused here, and not repaired — that
tree is read-only.

The tighter assertion, which is the one that actually concerns this plan, holds:

```
$ find "D:/Visomaster" -newer <plan's first commit> -not -path '*/dependencies/*'
(nothing)

$ find "D:/Visomaster/app" -name "__pycache__" -newermt '-1 day'
(nothing)

$ git -C "C:/Users/Sakat/.devswarm/repos/1/ef7f9c3d/config-parallel-setup" status --short
(nothing)
```

Nothing under `D:/Visomaster` is newer than this plan's first commit. Every
invocation of the portable interpreter used `-B`, so no bytecode was written into
that tree.

## Deviations from Plan

### [Rule 1 — measured fact wrong] Five widget shapes, and 93 int keys, not 94

**Found during:** Task 2.
**Issue:** `<measured_facts>` records four shapes and 94 int keys, with the int
rule stated as "min/max/step without `decimals`". Both cannot be true at once: 93
keys match that rule. The 94th is `ClipText`, which carries `min_value: '0'` and
`max_value: '1000'` but **no** `step` and no `decimals`, and whose default is the
empty string. Those bounds are character-count limits on a line edit, not slider
bounds.
**Why it is not cosmetic:** typing it as an int does not merely mislabel it —
`int(float(''))` raises, so the generator dies. The distinguishing signal is
`step`: a slider has one, a text box does not.
**Fix:** the shape rule has a fifth branch (`min/max` without `step` → `str`),
`tests/test_engine_settings_fixture.py::test_text_keys_are_empty_strings` pins
it, and the correction is argued in `docs/engine-test-assets.md`.
**Commit:** `1b70d6c`.

### [Rule 3 — blocking] The block list cannot live in `conftest.py`

**Found during:** Task 3.
**Issue:** the plan says to lift the Qt root list into `tests/conftest.py` and
have the runner read it from there. `conftest.py` imports pytest at module scope,
and pytest is installed on **neither** engine interpreter — measured on
`D:/Visomaster/dependencies/Python/python.exe` and on `.venv-clean`. The runner
and the probe therefore cannot import it.
**Fix:** the definitions moved to `tests/_blocked_roots.py`, a module that imports
nothing; `conftest.py` imports and re-exports them so the pytest side still has
the single obvious import site the plan wanted. There is still exactly one
definition — and one *fewer* than before, since `_qt_guard_probe.py`'s private
copy was deleted in the same change. T-02-05 is satisfied more completely than
the plan's own wording would have achieved.
**Rejected alternative:** teaching `conftest.py` to treat pytest as optional. A
conftest that pretends it can run without pytest is a lie about what a conftest
is.
**Files:** `tests/_blocked_roots.py` (new), `tests/conftest.py`,
`tests/_qt_guard_probe.py`. **Commit:** `6a1c9f0`.

### [Rule 2 — stronger check] `ast` instead of comment-stripping, and reachability reporting

Two places where the implementation is deliberately stronger than the plan:

1. The backend scan uses `ast` rather than the `tokenize` comment-stripping of
   `test_no_qt_source.py`. That file strips comments because it must reason about
   *text* — a Qt name inside a string literal is reachable via
   `importlib.import_module`, so it cannot simply parse imports. Here the question
   is narrower and is exactly what the grammar answers, so a removal note
   mentioning `backend` is a non-hit by construction rather than by filtering, and
   so is a variable named `backend_url`. `vendored_sources()` is reused as the
   plan asked, the non-vacuity guard is present, and
   `test_the_backend_scanner_is_not_inert` shows the scanner capable of failing.
2. The runner's reachability measurement and `VISOMASTER_DIR` path insertion, and
   the `VISOSWAP_SETTINGS_FIXTURE` override that makes `ASSET_MISSING` provable.
   Argued above.

### Not a deviation, but noted

`.gitignore` already carried `model_assets/` and `tensorrt-engines/`. Left
untouched, per the plan.

## Threat Flags

None. This plan adds no network endpoint, no auth path, no schema at a trust
boundary, and **no third-party package** (T-02-SC): `tools/` is standard library
only, and the generator and runner use nothing the engine interpreter did not
already have.

T-02-02 (writes through the link into the read-only tree) is re-verified above
and holds.

## Known Stubs

None. `tests/_engine_runner.py` has two modes and no placeholder for the modes
later plans add — a mode that does not exist yet returns the usage line and exit
4, rather than a stub that returns `CLEAN`.

## Housekeeping

`.planning/phases/03-settings-schema-three-tier-resolution/` (`03-01-PLAN.md`,
`03-02-PLAN.md`) appeared untracked in the working tree at 20:41–20:42 during
this plan's execution. It was not written by this plan and was deliberately left
uncommitted.

## What 02-02 Inherits

`model_assets` resolves, so `ModelsProcessor` can be constructed without
`FaceEditors` silently leaving `lp_lip_array` as `None`. `run_engine_runner()` in
`tests/conftest.py` drives the sealed subprocess from the repository root, which
is load-bearing rather than tidy — `models_dir` is relative until Phase 4, and
TensorRT's provider options write a cache to a relative path. Pinning the
provider to CUDA is 02-02's first job.

## Self-Check: PASSED

All nine created artifacts exist on disk; all four commits
(`fa0fc8e`, `08171e8`, `1b70d6c`, `6a1c9f0`) are present in the log.
