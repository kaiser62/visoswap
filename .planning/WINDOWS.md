---
schema_version: 1
open_count: 1
waived_count: 0
fixed_count: 0
total_count: 1
last_updated: 2026-08-21T04:21:56.815Z
---

# Broken Windows Ledger

> Cross-phase defect register. With `workflow.windows_enforce` enabled, `/gsd-ship` blocks while `open_count > 0`.
> Waive with `gsd-tools windows waive <id> "<reason>"` (reason required).
> Mark fixed with `gsd-tools windows fixed <id>`.

| id | phase | kind | file | line | description | status | reason | recorded_at | resolved_at |
|----|-------|------|------|------|-------------|--------|--------|-------------|-------------|
| 1 | 1 | unrun-verify | visoswap/processors/external/clipseg.py |  | CLIPseg, DFM and LivePortrait paths are import-proven only; nothing in Phase 1 executes them. Phase 2 exercises all three. | open |  | 2026-08-21T04:21:56.815Z |  |

````json
[
  {
    "id": 1,
    "kind": "unrun-verify",
    "phase": "1",
    "file": "visoswap/processors/external/clipseg.py",
    "line": null,
    "description": "CLIPseg, DFM and LivePortrait paths are import-proven only; nothing in Phase 1 executes them. Phase 2 exercises all three.",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-08-21T04:21:56.815Z",
    "resolved_at": null
  }
]
````
