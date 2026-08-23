---
status: complete
phase: 02-engine-api-first-swap
source: 02-01-SUMMARY.md, 02-02-SUMMARY.md, 02-03-SUMMARY.md, 02-04-SUMMARY.md
started: 2026-08-23T15:36:00Z
updated: 2026-08-23T15:38:35Z
---

## Current Test

[testing complete]

## Tests

### 1. First swap produces output
expected: Running the engine smoke detects both faces in the clip, swaps them with a source face, and writes an output frame with a measurable pixel change — not a byte-identical copy.
result: pass

### 2. Qt-free seal holds
expected: The swap runs on an interpreter with no Qt installed; PySide6/PyQt/qtpy resolve before the block arms but are unimportable inside it, so the seal is not passing vacuously.
result: pass

### 3. Provider lock
expected: The engine runs on the CUDA ONNX provider; TensorRT / TensorRT-Engine are refused outright with a ValueError (a relative cache-path hazard), not merely deprioritised.
result: pass

### 4. Face identity is stable
expected: FaceCard derives face_id by digesting its own recognition embedding, so the same face keys identically across runs and no identifier originates in a widget.
result: pass

### 5. LivePortrait face editor runs
expected: The face-editor path executes LivePortrait through the sealed runner and produces an output; its distinct failure modes (missing assets, zero-byte baseline) are asserted separately.
result: pass

### 6. torch.load hardened
expected: Every torch.load call under visoswap/ passes weights_only=True (a syntax-tree gate pins the full dotted name); the gate provably covers the genuinely reachable unsafe site and does not silently no-op.
result: pass

## Summary

total: 6
passed: 6
issues: 0
pending: 0
skipped: 0

## Gaps

[none yet]

## Deferred Follow-Ups

- test: 3
  idea: "Re-enable TensorRT / TensorRT-Engine as a selectable execution provider for production throughput (measured 27.8 vs 21.2 fps @1080p on CUDA). Currently refused outright (threat T-02-09) because its engine/timing cache writes to a relative tensorrt-engines/ path that would land in the read-only source tree. Would need a scoped change to pin an explicit absolute cache path outside the source tree, then re-enable the provider."
  deferred_at: 2026-08-23
