---
schema_version: 1
open_count: 1
waived_count: 1
fixed_count: 0
total_count: 2
last_updated: 2026-08-23T23:27:56.223Z
---

# Broken Windows Ledger

> Cross-phase defect register. With `workflow.windows_enforce` enabled, `/gsd-ship` blocks while `open_count > 0`.
> Waive with `gsd-tools windows waive <id> "<reason>"` (reason required).
> Mark fixed with `gsd-tools windows fixed <id>`.

| id | phase | kind | file | line | description | status | reason | recorded_at | resolved_at |
|----|-------|------|------|------|-------------|--------|--------|-------------|-------------|
| 1 | 1 | unrun-verify | visoswap/processors/external/clipseg.py |  | CLIPseg, DFM and LivePortrait paths are import-proven only; nothing in Phase 1 executes them. Phase 2 exercises all three. | waived | Waived, not fixed: this item's own claim that 'Phase 2 exercises all three' is false as of 02-DECISION-deferred-paths.md. Phase 2 exercises LivePortrait only (plan 02-03). The CLIPseg text-masking path is deferred because rd64-uni-refined.pth exists nowhere on this machine and is absent from upstream VisoMaster's own 62-entry model manifest; the DFM path is deferred because no .dfm files exist anywhere and EngineContext.dfm_models_data has no populator. Both paths stay vendored and import-proven, not ripped out. Phase 4's model bootstrap is the owning phase for re-enabling either. Recorded in docs/engine-path-coverage.md and docs/engine-extra-assets.md, pinned by tests/test_engine_deferred_paths.py. | 2026-08-21T04:21:56.815Z | 2026-08-21T19:49:07.823Z |
| 2 | 05.1 | deviation | playwright.config.ts | 17 | Playwright webServer relative command cannot spawn its backend on this Windows setup; executor ran the identical uvicorn command manually (see 05.1-04-SUMMARY Deviation 2) | open |  | 2026-08-23T23:27:56.223Z |  |

````json
[
  {
    "id": 1,
    "kind": "unrun-verify",
    "phase": "1",
    "file": "visoswap/processors/external/clipseg.py",
    "line": null,
    "description": "CLIPseg, DFM and LivePortrait paths are import-proven only; nothing in Phase 1 executes them. Phase 2 exercises all three.",
    "status": "waived",
    "reason": "Waived, not fixed: this item's own claim that 'Phase 2 exercises all three' is false as of 02-DECISION-deferred-paths.md. Phase 2 exercises LivePortrait only (plan 02-03). The CLIPseg text-masking path is deferred because rd64-uni-refined.pth exists nowhere on this machine and is absent from upstream VisoMaster's own 62-entry model manifest; the DFM path is deferred because no .dfm files exist anywhere and EngineContext.dfm_models_data has no populator. Both paths stay vendored and import-proven, not ripped out. Phase 4's model bootstrap is the owning phase for re-enabling either. Recorded in docs/engine-path-coverage.md and docs/engine-extra-assets.md, pinned by tests/test_engine_deferred_paths.py.",
    "recorded_at": "2026-08-21T04:21:56.815Z",
    "resolved_at": "2026-08-21T19:49:07.823Z"
  },
  {
    "id": 2,
    "kind": "deviation",
    "phase": "05.1",
    "file": "playwright.config.ts",
    "line": 17,
    "description": "Playwright webServer relative command cannot spawn its backend on this Windows setup; executor ran the identical uvicorn command manually (see 05.1-04-SUMMARY Deviation 2)",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-08-23T23:27:56.223Z",
    "resolved_at": null
  }
]
````
