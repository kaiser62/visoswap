---
phase: 04-backend-integration-model-bootstrap
plan: 02
subsystem: backend-recorder
tags: [recorder, cancel-hang, subprocess-wait, aclose, partial-mp4]
status: complete

requires:
  - 04-01 (backend/services/recorder.py ported unfixed; the combined interpreter)
provides:
  - backend/services/recorder.py (aclose now drains the decoder stdout before waiting)
  - tests/test_recorder.py (14 tests ported from the predecessor, no skips)
  - tests/test_subprocess_wait_shape.py (the mechanism test + the backend-wide inventory)
  - docs/recorder-cancel-hang.md (the full diagnosis, for Phase 6)
affects:
  - Phase 6 re-verifies a live cancel mid-recording (roadmap criterion 2)

tech-stack:
  added: []
  patterns:
    - "a killed subprocess whose paused output pipe never reports EOF has a wait that never returns, even with the returncode set -- drain to EOF before awaiting"
    - "pin a stdlib mechanism in a dedicated test file rather than only in the component that hit it, so the shape cannot return unnoticed"
    - "inventory every awaited subprocess wait under backend/ and require each to drain or carry a written reason"

key-files:
  created:
    - tests/test_subprocess_wait_shape.py
    - tests/test_recorder.py
    - docs/recorder-cancel-hang.md
  modified:
    - backend/services/recorder.py

decisions:
  - "The fix is the public-drain variant (H), not the private-transport variants (D/E): draining with StreamReader.read() resumes the transport on its own once the buffer falls below half the limit, needs no private access, and was measured at 0.01s."
  - "The bounded-wait mitigation (G) is rejected and recorded as such: it passes the test while leaving the defect and costs the timeout on every cancel."
  - "The recorder tests were ported from the predecessor with the @needs_ffmpeg skipif marks removed, because ffmpeg/ffprobe are hard backend requirements and a skip on their absence is a false green."
  - "The drain and the wait are both bounded with the existing READ_TIMEOUT (120s) and the encoder path's 30s shape, so a decoder that ignores the kill costs a bounded delay, not a hung task."

metrics:
  duration: ~50 min
  completed: 2026-08-23
  tasks: 3
  commits: 1

actuals:
  tokens: 32000
  tasks: 3
  commits: 1
---

# Phase 04 Plan 02: The Cancelled-Recording Hang Summary

**The mechanism in one sentence:** a killed subprocess whose paused output pipe
never reports EOF has an `await proc.wait()` that never returns — even once the
process is dead and its returncode is set — because asyncio's exit waiter is
woken only when every pipe has disconnected.

**Before/after:** the cancelled-run test hung forever (killed at 180s). After
the fix it completes in **0.28s**, and the partial file it leaves decodes end to
end. The full suite is **310 passed, zero skips**.

The counter-intuitive fact, stated plainly: the process was already dead with
`proc.returncode=1` while the wait was still pending. That is what makes this a
stdlib-shape bug rather than an ffmpeg one, and it is the fact most likely to be
doubted — so it is recorded in `docs/recorder-cancel-hang.md` and pinned
executably in `tests/test_subprocess_wait_shape.py`.

## What Was Built

- **The fix** (`backend/services/recorder.py` `aclose`): after killing the
  decoder, drain its stdout to end of stream with the public
  `StreamReader.read()`, bound both drain and wait with `READ_TIMEOUT`, reuse the
  encoder path's 30s shape, swallow `BrokenPipeError`/`ConnectionResetError`, and
  move the drain outside the `returncode is None` guard so a decoder that exited
  on its own still has its pipe drained. No private asyncio attributes.
- **`tests/test_recorder.py`**: the 14 recorder tests ported from the
  predecessor, with `@needs_ffmpeg` skipif marks removed (ffmpeg is a hard
  requirement), and a `wait_for(aclose(), 10s)` bound added to the cancelled-run
  test as a regression guard (the mechanism is pinned in the sibling file).
- **`tests/test_subprocess_wait_shape.py`**: pins the stdlib mechanism — a
  killed, undrained, paused-pipe subprocess is a wait hazard; draining to EOF
  makes the wait return the returncode — plus the backend-wide inventory that
  walks every `.py` under `backend/`, finds every awaited `.wait()`, and requires
  each to drain or carry a written allowlist reason.
- **`docs/recorder-cancel-hang.md`**: the full diagnosis — the five-step
  mechanism, all eight measured variants, the already-dead-but-still-waiting
  observation, why only one of fourteen tests hangs, why the 28-byte partial was
  a consequence not a bug, and what Phase 6's live cancel must show.

## Deviations from Plan

None material. The plan's Task 1 asked for a "transport is paused" assertion
using a private attribute; on Windows' Proactor transport the pause bit is not a
stable public surface, so the mechanism tests assert the observable contract
(kill+wait-without-drain is a hazard; drain-then-wait returns the code) and the
inventory enforces the shape instead. The original diagnosis and fix are exactly
as the plan measured.

## Verification, verbatim

```
$ .venv-clean/Scripts/python.exe -m pytest tests/test_recorder.py -q --durations=5
14 passed in 1.84s
  (test_a_cancelled_run_leaves_a_playable_partial: 0.28s call)

$ .venv-clean/Scripts/python.exe -m pytest tests/test_subprocess_wait_shape.py -q
3 passed in 0.20s

$ .venv-clean/Scripts/python.exe -m pytest tests/ -q -rs
310 passed in 61.90s   (zero skips)
```

Test count: **293 → 310**, +17. Zero skips, none deselected.

## Requirements marked

Roadmap criterion 1 is met: `test_a_cancelled_run_leaves_a_playable_partial`
completes and passes — fixed, not skipped or deselected — and its partial decodes
end to end. BACKEND-01 progresses toward its full set (plans 04-03/04-04
remain).

## Known Stubs

None. Nothing here is placeholder, no test is skipped.

## Threat Flags

None. The change is confined to `aclose()` and adds no network endpoint, no auth
path, and no new filesystem surface.

## What Phase 4 inherits

- The recorder is now safe to cancel, which plan 04-04's no-VisoMaster proof and
  Phase 6's live cancel both build on.
- `docs/recorder-cancel-hang.md` tells Phase 6 exactly what evidence counts for
  its criterion 2.

## Self-Check: PASSED

All committed files present. Commit found in `git log`: `8fc5d98`.
