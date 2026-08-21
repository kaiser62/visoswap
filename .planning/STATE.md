---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
current_phase: 2
current_phase_name: Engine API & First Swap
status: in-progress
stopped_at: Completed Phase 1
last_updated: "2026-08-21T00:00:00.000Z"
last_activity: 2026-08-21
last_activity_desc: "Completed 01-04 and closed Phase 1: frame_worker.py de-Qt'd, PENDING_QT_STRIP emptied, and all 30 vendored modules import on an interpreter with no Qt installed"
progress:
  total_phases: 1
  completed_phases: 1
  total_plans: 4
  completed_plans: 4
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-21)

**Core value:** Playback never blocks on generation — a missing generated frame shows the original video frame rather than pausing the video.
**Current focus:** Phase 2 — Engine API & First Swap

## Current Position

Phase: 2 of 6 (Engine API & First Swap)
Plan: 0 of 4 in current phase
Status: Plans written and checked; 02-01 ready to execute
Last activity: 2026-08-21 — Closed Phase 1: all 30 vendored modules import on an interpreter where Qt is genuinely absent, confirmed at the human checkpoint

Progress: [██████████] 100% (Phase 1 complete, 4/4 plans)

## Performance Metrics

**Velocity:**

- Total plans completed: 1
- Average duration: 25 min
- Total execution time: 0.4 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 1 | 25 min | 25 min |

**Recent Trend:**

- Last 5 plans: 25 min
- Trend: Stable

*Updated after each plan completion*
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 01 P01 | 25 | 2 tasks | 14 files |
| Phase 01 P02 | 30 min | 3 tasks | 27 files |
| Phase 01 P03 | ~25 min | 2 tasks | 6 files |
| Phase 01 P04 | ~40 min | 3 tasks | 6 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Phase 1]: Block all seven Qt roots, not just PySide6 — qtpy raises QtBindingsNotFoundError independently of any direct binding import.
- [Phase 1]: Run the Qt probe as a subprocess on the engine interpreter, which has PySide6 installed — proving unreachability where Qt IS installed is strictly stronger.
- [Phase 1]: Probe exit codes separate CLEAN / QT_REACHED / DEPS_MISSING / IMPORT_ERROR so a missing engine dependency can never read as a Qt-cleanliness pass.
- [Phase 1]: The vendored-file attribution header is byte-identical across files (no per-file origin line) so the header gate matches exactly rather than pattern-guessing.
- [Phase 1]: NOTICE records the upstream revision as explicitly undeterminable, quoting the git error, rather than leaving provenance blank.
- [Phase 1]: Vendor at the byte level so CRLF survives and each file is provably upstream-plus-rewrite-map and nothing else
- [Phase 1]: PENDING_QT_STRIP lives in conftest.py so the import gate and the static Qt scan read one exclusion set and cannot diverge
- [Phase 1]: Static Qt scan strips COMMENT tokens only, never STRING tokens: a Qt name in a string is reachable via importlib
- [Phase 1]: EngineContext replaces the main_window god object with exactly 7 fields; the 15-attribute enumeration with line evidence lives in 01-CONTEXT-SURFACE.md
- [Phase 1]: The engine's only model-load progress signalling was deleted outright, not stubbed; the re-add path is a Qt-free on_model_load callback on EngineContext, with anchors recorded in 01-CONTEXT-SURFACE.md Note 3
- [Phase 1]: self.context is ambiguous in the vendored tree: models_processor holds an EngineContext, tensorrt_predictor holds a TensorRT execution context
- [Phase 1]: The clean-room check imports with NO blocker armed, not just with the probe. Where Qt is genuinely absent the meta_path blocker is inert, so `CLEAN` alone would be weaker there than on the Qt-bearing interpreter; a module swallowing an ImportError passes both gates and runs degraded.
- [Phase 2]: DFM and CLIPseg are descoped -- vendored and import-proven, never exercised. No weights exist on any reachable machine. See 02-DECISION-deferred-paths.md.
- [Phase 2]: Phase 4's model-file criterion is derived from the manifest at runtime, never a hardcoded count: models_list is 56 entries and models_trt_list is 6 or 0 depending on whether `import tensorrt` succeeds.

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 1] `D:/Visomaster/.git/config` is malformed upstream (`fatal: bad config line 1 in file .git/config`). Every `git -C D:/Visomaster ...` fails. Pre-existing; the tree is read-only source material and is deliberately not repaired. No upstream commit SHA can be recorded for any vendored file in plans 02-04.
- [RESOLVED, Phase 1] The clean-room virtualenv now exists at `.venv-clean/` (Python 3.10.19, gitignored), built from `requirements-engine.txt`. All 30 vendored modules import there with no Qt installed. Procedure recorded in `docs/verifying-qt-free.md`; Phase 6 re-runs it.
- [Phase 4] visomaster_live's test_recorder.py::test_a_cancelled_run_leaves_a_playable_partial hangs indefinitely. Pre-existing on that repo's master and currently deselected there. Phase 4 is scheduled to fix it rather than carry the deselect forward. Carried over from the 01-03 pause handoff, which is now deleted.
- [Phase 1] The 01-03 pause handoff warned that plan line numbers 'run one low'. That held for plan 01-02's file lengths but NOT for plan 01-03's edit sites, which matched upstream exactly. Plan 01-04 should assert its line numbers against frame_worker.py rather than pre-emptively offsetting them.

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-08-21
Stopped at: Completed Phase 1; Phase 2 plans revised after plan-check
Resume file: None
