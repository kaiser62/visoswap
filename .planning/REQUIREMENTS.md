# Requirements: VisoSwap

**Defined:** 2026-08-21
**Core Value:** Playback never blocks on generation. If a generated frame is missing at its timestamp, the original video frame is shown — the video does not pause, wait, or stutter.

## v1 Requirements

Scoped directly from PROJECT.md's Active section. Each maps to exactly one roadmap phase.

### Engine

- [ ] **ENGINE-01**: Swap a frame with no PySide6 installed and no VisoMaster install present

### Schema & Settings

- [ ] **SCHEMA-01**: Generated settings schema with explicit types and typed defaults
- [ ] **SCHEMA-02**: Three-tier settings resolution: global, project, per-face
- [ ] **SCHEMA-03**: Per-face settings persisted, keyed by recognition embedding
- [ ] **SCHEMA-04**: Both existing profiles migrated as seeded presets

### Backend

- [ ] **BACKEND-01**: Model bootstrap: verify, download missing by hash, refuse to start if incomplete

### Frontend

- [ ] **FRONTEND-01**: Frontend renders all controls from the schema

### Licensing

- [ ] **LICENSE-01**: GPLv3 licensing with VisoMaster attribution, non-commercial weights documented

## v2 Requirements

None currently deferred. ComfyUI support is not a v2 requirement — it is fully covered by
"Out of Scope" below; the `FrameGenerator` seam is kept specifically so it can return without a
requirements change.

## Out of Scope

Carried directly from PROJECT.md.

| Feature | Reason |
|---------|--------|
| ComfyUI backend | Deferred, not abandoned. The `FrameGenerator` interface stays as the seam it returns through, so removing it would only have to be undone. |
| VisoMaster's Qt UI | The whole point of the project is not needing it. |
| `video_processor.py` (VisoMaster's Qt playback loop) | The scheduler already owns playback; keeping both would mean two schedulers. |
| Multi-face swapping | Replaced by single-face targeting, deliberately, and the old behaviour is not coming back as a toggle. |
| Multi-user, auth, hosting | Single user on their own machine. |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| ENGINE-01 | Phase 2 | Pending |
| LICENSE-01 | Phase 1 | Pending |
| SCHEMA-01 | Phase 3 | Pending |
| SCHEMA-02 | Phase 3 | Pending |
| SCHEMA-03 | Phase 3 | Pending |
| SCHEMA-04 | Phase 3 | Pending |
| BACKEND-01 | Phase 4 | Pending |
| FRONTEND-01 | Phase 5 | Pending |

Phase 1 and Phase 6 carry no v1 requirement of their own: Phase 1 is the precursor that
ENGINE-01 depends on (import-clean vendoring, proven before the first actual swap), and Phase 6
is end-to-end regression coverage over capabilities already in PROJECT.md's Validated section
(recorder, launcher, userscript, deterministic playback) re-proven on the new engine rather than
new scope.

**Coverage:**
- v1 requirements: 8 total
- Mapped to phases: 8
- Unmapped: 0 ✓

---
*Requirements defined: 2026-08-21*
*Last updated: 2026-08-21 after initial roadmap creation*
