---
status: complete
phase: 03-settings-schema-three-tier-resolution
source: 03-01-SUMMARY.md, 03-02-SUMMARY.md, 03-03-SUMMARY.md
started: 2026-08-23T15:32:49Z
updated: 2026-08-23T15:34:52Z
---

## Current Test

[testing complete]

## Tests

### 1. Settings schema typed resolution
expected: Every settings key (201 entries) has a typed representation and a default in schema.json. Resolving a key applies face → project → global → default tier order; resolve_parameters/resolve_control return whole tiers with every key present.
result: pass

### 2. Strict write validation
expected: Setting an invalid value (out of bounds, wrong type, or a non-option value) is rejected on write; option-membership is enforced only on write, not read.
result: pass

### 3. Preset apply swaps at 128
expected: Applying preset "A" or "with AUD" changes settings; the swap resolution (SwapperResSelection) resolves to 128, not 256. Overrides only — a preset does not rewrite defaults or drop tiers.
result: pass

### 4. Playback FPS derivation
expected: effective_playback_fps returns the custom slider value when the toggle is on, the clip's own rate when it is off, and None when neither is set.
result: pass

### 5. Migration is clean
expected: Running migrate_profiles against the source profiles reports zero unknown keys, zero missing keys, and zero validation failures across all 402 values.
result: pass

### 6. Face identity threshold matching
expected: A face is matched by a similarity threshold against its stored embedding; the same face keys identically across runs (embedding-digest id), and matching is injectable/testable.
result: pass

## Summary

total: 6
passed: 6
issues: 0
pending: 0
skipped: 0

## Gaps

[none yet]
