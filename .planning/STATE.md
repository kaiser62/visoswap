---
gsd_state_version: 1.0
milestone: v1.0
current_phase: 04
current_phase_name: Backend Integration & Model Bootstrap
status: executing
stopped_at: "Paused mid-Phase 4: plans 04-01, 04-02 complete"
last_updated: "2026-08-23T13:02:06.776Z"
last_activity: 2026-08-23
last_activity_desc: Phase 04 execution started
state_head: 05746414bf222a94d0ca6da7959528f06fb1b41f
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 20
  completed_plans: 13
milestone_name: milestone
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-21)

**Core value:** Playback never blocks on generation — a missing generated frame shows the original video frame rather than pausing the video.
**Current focus:** Phase 04 — Backend Integration & Model Bootstrap

## Current Position

Phase: 04 (Backend Integration & Model Bootstrap) — EXECUTING (paused)
Plan: 2 of 4 (04-01, 04-02 complete; 04-03, 04-04 remaining)
Status: Paused — user elected to stop here
Last activity: 2026-08-23 — plans 04-01 and 04-02 executed and verified (310 tests green, 0 skips)

Progress: [███████░░░] 73% (11/15 planned items summarized; Phase 4 has 4 plans remaining)

## Performance Metrics

**Velocity:**

- Total plans completed: 11
- Average duration: historical data incomplete
- Total execution time: historical data incomplete

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01–03 | 11 | historical data incomplete | historical data incomplete |

**Recent Trend:**

- Historical timing data is incomplete; no trend is reported.

*Updated after each plan completion*
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 01 P01 | 25 | 2 tasks | 14 files |
| Phase 01 P02 | 30 min | 3 tasks | 27 files |
| Phase 01 P03 | ~25 min | 2 tasks | 6 files |
| Phase 01 P04 | ~40 min | 3 tasks | 6 files |
| Phase 02 P01 | 55 min | 3 tasks | 10 files |

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
- [Phase 2]: TensorRT is REFUSED with a ValueError in Engine's constructor, not merely deprioritised -- its provider options carry a relative `tensorrt-engines` cache path. Only CUDA and CPU are accepted.
- [Phase 2]: FaceCard derives face_id by digesting its own recognition embedding, so no identifier originates in a widget and the same face keys identically across runs.
- [Phase 2]: A THIRD torch.load existed at cliplib/clip.py:141, reached BEFORE the missing rd64-uni-refined.pth and operating on a URL-downloaded file. Descoping CLIPseg did NOT make it safe. Hardened with weights_only=True. What keeps the path inert is ClipEnableToggle=false, not the absent weights file.
- [Phase 2]: ENGINE-01 stays Pending. Clause 1 (no PySide6) is proven; clause 2 (no VisoMaster install) is false while ./model_assets is a junction into D:/Visomaster. Phase 4 closes it.
- [Phase 3]: Settings type comes from WIDGET SHAPE, never the key name; the rule has five branches and `step` is the slider signal. ClipText has min/max but no step and default '' -- a four-branch rule crashes on int(float('')).
- [Phase 3]: Upstream gate semantics preserved exactly, counterintuitive as they are: 'A|B' is AND (starts True, clears on any unchecked parent), 'A, B' is LAST-PARENT-WINS (plain assignment in the loop), and the parent lookup is a substring test. Recorded as all/last; never "fixed".
- [Phase 3]: Option-membership is enforced on WRITE only. Enforcing it on read meant one deleted model file would take all 201 settings down as corruption, because resolution reads a whole tier at once.
- [Phase 3]: profiles.json values are NOT all strings -- 30 bools per profile and 2 ints in one. coerce types by shape, which is why it survives the wrong premise.
- [Phase 3]: Both presets are ~99% schema defaults; preset A differs in exactly ONE setting. Preset tests need an explicit non-vacuity guard or they assert nothing.
- [Phase 4]: ruff is not installed on any interpreter here and the repo carries no ruff config, so `ruff check` in the stated workflow has never run. Settle this in Phase 4.
- [Phase 2]: Five widget shapes exist in the layout dicts, not four: ClipText carries min/max with no step (character bounds on a line edit) and types as an empty string, so there are 93 int keys, not 94
- [Phase 2]: The seal block lists live in tests/_blocked_roots.py (import-free) and are re-exported by conftest.py, because both subprocess consumers run on the engine interpreter, which has no pytest

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

Last session: 2026-08-23T13:02:06.747Z
Stopped at: Paused mid-Phase 4: plans 04-01, 04-02 complete
Resume file: .planning/phases/04-backend-integration-model-bootstrap/04-02-SUMMARY.md
