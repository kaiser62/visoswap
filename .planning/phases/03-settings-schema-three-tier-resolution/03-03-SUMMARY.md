---
phase: 03-settings-schema-three-tier-resolution
plan: 03
subsystem: settings-schema
tags: [presets, migration, handlers, gating, three-tier, sqlite]
status: complete

requires:
  - 03-01 (visoswap/schema/schema.json -- 201 typed entries; setting_presets DDL in settings/db.py)
  - 03-02 (visoswap/settings/validate.py -- coerce, the lenient migration-only converter)
  - 03-02 (visoswap/settings/store.py -- set_project/set_global/clear_*, tier-checked and validated)
  - 01-01 (attribution gate; the Qt-source scan that reads docstrings as source)
provides:
  - visoswap/settings/data/presets_seed.json (2 presets, 402 typed values, committed and shipped)
  - visoswap/settings/presets.py (seed, list, apply -- overrides only, through the store)
  - visoswap/settings/handlers.py (3 apply-handlers, 3 recorded non-handlers, validating dispatch)
  - tools/migrate_profiles.py (the one intended caller of validate.coerce)
  - tests/test_no_swap_model_overrides.py (the AST gate that closes roadmap criterion 5)
  - docs/settings-presets.md
affects:
  - 04 dispatches handlers against ModelsProcessor and reads handlers.effective_playback_fps for the scheduler
  - 04 seeds setting_presets at connect time; the reassignment gate starts scanning backend/ the moment it exists
  - 05 renders presets from list_presets and must not reintroduce a swapper assignment; the gate scans frontend/src/

tech-stack:
  added: []
  patterns:
    - "type the migrated value by shape through the schema, never by inspecting what type it already is -- the source file types the same key two different ways in its own two profiles"
    - "a gate must be proven in both directions and must report the file count it examined; a scanner pointed at nothing passes perfectly"
    - "parse, do not grep, when the thing being forbidden is also legitimate data -- AST discards comments and docstrings for free"
    - "record a deliberate non-handler by name with a reason: a silent absence and a decision look identical in a diff a year later"
    - "build committed generated data against a deliberately empty external directory, so the bytes are a function of the source and not of the generating machine"

key-files:
  created:
    - tools/migrate_profiles.py
    - visoswap/settings/presets.py
    - visoswap/settings/handlers.py
    - visoswap/settings/data/presets_seed.json
    - tests/test_presets_seed.py
    - tests/test_apply_handlers.py
    - tests/test_no_swap_model_overrides.py
    - docs/settings-presets.md
  modified:
    - .gitignore
    - pyproject.toml
    - visoswap/settings/__init__.py
    - tests/test_vendor_headers.py
    - docs/settings-schema.md
    - .planning/REQUIREMENTS.md

decisions:
  - "The plan's claim that every value in profiles.json is a string is false, and a converter written to it fails on 43 toggles at the first profile. Migration types by shape through validate.coerce, which mirrors the generator's rule and passes bools and ints through untouched."
  - "The reassignment is at five sites, not two, and it is wrong in a second way the plan did not name: it assigns into the options dict -- the global tier -- while both keys are project-tier keys living in parameters. It was writing where nothing reads."
  - "The seed is migrated against a deliberately empty models directory. DFMModelSelection's options are a directory listing, so otherwise the committed bytes would depend on which DFM files sit on the migrating developer's disk."
  - "A preset value that is not currently an available option is skipped, its stale override cleared, and its name returned in the report -- the same distinction validate_stored already draws between 'the file this names is not here' and 'this was never allowed'."
  - "dispatch validates before the target is reached rather than trusting the caller (T-03-17). The store already validates on write, but a handler can fire from a value that never went through the store."
  - ".gitignore's `data/` rule is anchored to `/data/`. Unanchored it also ignored the committed preset seed, which would have left the file present on exactly the machine that generated it."
  - "The frame-rate handler rounds halves up rather than using Python's round, which sends 30.5 to 30 and 31.5 to 32 -- a banker's rule nobody expects of a frame rate."

metrics:
  duration: ~65 minutes
  completed: 2026-08-22
  tasks: 3
  commits: 6

actuals:
  tokens: 35000
  tasks: 3
  commits: 6
---

# Phase 03 Plan 03: Presets, Handlers & the Removed Reassignment Summary

**Both presets now swap at resolution 128 rather than at 256.** Both saved
profiles store `"128"`; the old web UI forced `"256"` on every load, in the wrong
tier. With the reassignment gone and provably unable to return, what the profile
stored is what resolves. That is the one user-visible rendering change in the
phase.

The migration reported **zero unknown keys, zero missing keys and zero
validation failures across all 402 values**, so the saved profiles and the frozen
schema agree completely about what a setting is — which is the second thing the
plan asked to be led with, and the good version of it.

## Three things in the plan that are factually wrong

**1. "Every value in the source profiles is a string."** The plan says this
twice, once in `<measured_facts>` and once in `key_links` as the justification
for using the lenient converter. Measured directly before writing anything:

| Profile | `options` | `parameters` |
|---|---|---|
| `6a24b3aa71d7` `A` | 20 str + 13 bool | 138 str + 30 bool |
| `55fc87c18307` `with AUD` | 20 str + 13 bool | 136 str + 30 bool + **2 int** |

Toggles are already real bools upstream and two numbers are already real ints.
A converter written to the plan's claim — string in, typed out — fails on 43
toggles at the first profile. The plan's *conclusion* survives its own wrong
premise: `validate.coerce` is still exactly the right function, because it types
by **shape through the schema** rather than by inspecting the value, and that is
what makes the two profiles' disagreement about `SimilarityThresholdSlider`
(`"60"` vs `20`) and `StrengthAmountSlider` (`"100"` vs `150`) collapse to one
type. Plan 03-02's summary had already recorded this correction; plan 03-03
carries the uncorrected version anyway.

**2. The reassignment count and the second defect.** The plan's own
`<measured_facts>` heading says "four sites" and its body then lists five
(`:232-233`, `:252`, `:258`, `:372-373`, `:1199-1200`). Five is right, confirmed
by grep. More importantly the plan under-describes the tier error in one place
and describes it correctly in another: both load-path assignments write into
`merged["options"]` / `options`, which is the **global** tier, while
`SwapModelSelection` and `SwapperResSelection` are **project**-tier keys that
live in `parameters`. The forced value was therefore being written where nothing
would ever read it *as well as* being wrong. Neither site is ported.

**3. `visomaster_headless.py:99` is the video-processor call, not the models one.**
The prompt and the plan both point at line 99 as the call that reaches the engine.
Read directly, the headless reference makes **both** calls:

```
98:    window.models_processor.set_number_of_threads(threads)
99:    window.video_processor.set_number_of_threads(threads)
```

Line 98 is the one this repository can make; line 99 is the module Phase 1
dropped. The substantive point is untouched — a faithful port of
`change_threads_number` would only make the call on line 99 and would therefore
do nothing at all — but the line number in the plan names the wrong one of the
two, and a reader checking it would find the dropped call and conclude the plan
meant that.

## What Was Built

### Task 1 — the typed, committed seed (`560bd28` RED, `d632e09` GREEN)

`tools/migrate_profiles.py` (282 lines, no Qt — the source is plain JSON and the
schema is already committed, so it runs on the plain developer interpreter). It
maps `options` onto the global tier and `parameters` onto the project tier, puts
every value through `validate.coerce`, and **refuses to write a seed at all** if
any of the three disagreement classes is non-zero. It is the one intended caller
of the lenient path; `tests/test_settings_validation.py`'s caller-set scan covers
`visoswap/` and `backend/` and not `tools/`, so nothing had to be relaxed to let
this land.

```
profiles: D:\Visomaster\profiles.json (2 found)
  6a24b3aa71d7 'A': project=168 global=33 unknown=0 missing=0 invalid=0
  55fc87c18307 'with AUD': project=168 global=33 unknown=0 missing=0 invalid=0
totals: unknown=0 missing=0 invalid=0
```

`visoswap/settings/data/presets_seed.json` — 432 lines, sorted, newline
terminated, committed and shipped as package data.

`visoswap/settings/presets.py` seeds idempotently (2 rows, then 2 rows and
`unchanged: 2`), lists with payloads decoded, and applies **overrides only**,
routing every write through `store.set_project` / `store.set_global`. A test
asserts against the file's own source that it contains no `INSERT INTO
project_settings`, because a private write path is a second set of rules and the
second one is always the one without validation.

**The measured override sets are tiny, and that is worth recording:**

| Preset | Tier | Key | Preset | Default |
|---|---|---|---|---|
| both | global | `AudioMuxingToggle` | `False` | `True` |
| `with AUD` | project | `SimilarityThresholdSlider` | `20` | `60` |
| `with AUD` | project | `StrengthEnableToggle` | `True` | `False` |
| `with AUD` | project | `StrengthAmountSlider` | `150` | `100` |

Preset `A` disagrees with the schema defaults about **one** setting in 201.
Applying it writes one global row and **zero** project rows. That makes the
override-only assertion easy to satisfy vacuously, so the test suite carries an
explicit guard — `test_applying_a_preset_writes_at_least_one_override_somewhere`
— whose whole job is to fail if the counting test ever becomes "wrote nothing,
which is trivially only the differing keys".

It also means both presets store `SwapperResSelection` at the value that is
*also* the schema default. The reassignment removal is still exactly as
load-bearing as claimed — `"256"` was being forced over it — but the proof that
128 survives is a proof that the default survives, not that an override does.
Said plainly here rather than left to be noticed.

### Task 2 — the three orphaned handlers (`3428655` RED, `4f6e0f2` GREEN)

`visoswap/settings/handlers.py`, standard library only (`math`, `logging`), every
handler taking its target as an argument.

| Key | What the handler does | Divergence from upstream |
|---|---|---|
| `ProvidersPrioritySelection` | `switch_providers_priority(value)` then `clear_gpu_memory()` | upstream's `video_processor.stop_processing()` and the Qt progress bar are deliberately absent, not stubbed |
| `nThreadsSlider` | `models_processor.set_number_of_threads(int(value))` | upstream calls the **video processor**, which no longer exists — a faithful port would be a no-op that looks correct |
| `VideoPlaybackCustomFpsToggle` | returns `{VideoPlaybackCustomFpsSlider: clamp(round(media.fps))}` | none; the direction is upstream's, which is the reverse of the function's name |

Bounds are read from the schema entry, and a test parses `handlers.py` and fails
if `120` appears as a numeric literal anywhere in the code — while leaving it
free to appear in the docstring that explains the clamp, which it does.

`effective_playback_fps` sits beside them: the slider when the toggle is on, the
clip's own rate when it is off, `None` when there is neither. Phase 4's scheduler
and the player both need that answer and deriving it twice is how the two end up
disagreeing about what time it is.

**Three deliberate non-handlers**, recorded in `DELIBERATELY_UNHANDLED` by name
with a reason each, and a schema walk that fails if any key carrying an
`exec_function` is neither handled nor recorded:

- `ViewFaceMaskEnableToggle` — read directly by `processors/workers/frame_worker.py`
  at lines 73, 751, 757 and 1280 (verified in this tree, not assumed). Its
  callback only refit a Qt image view.
- `ViewFaceCompareEnableToggle` — same.
- `ThemeSelection` — loads a Qt stylesheet onto a `QApplication` that does not
  exist here.

**Added beyond the plan (deviation Rule 2, T-03-17):** `dispatch` validates the
value against its schema entry *before* the target is reached, and a test asserts
the recording stand-in received nothing when validation fails. The threat model
says both values are validated "before any handler sees them"; the store does
that on write, but a handler can fire from a value that never went through the
store, and "the caller definitely validated" is the assumption that holds right
up until it does not.

### Task 3 — the gate, proven in both directions (`e6e4e6d`)

`tests/test_no_swap_model_overrides.py` parses every `.py` under `visoswap/`,
`tools/` and — when Phases 4 and 5 create them — `backend/`, flagging any
assignment whose target is a subscript with either key as its literal index, an
attribute of either name, or a bare name. A line-oriented scan covers
`frontend/src/` for `.ts/.tsx/.js/.jsx/.vue/.svelte`, skipping comment lines and
matching only a real `=` (never a `:`, which in TypeScript is how a settings
payload is legitimately *shaped*). One gate, one file.

Proven rather than trusted:

- a planted file with four assignments in four spellings — flagged, all four,
  with the right line numbers and key names;
- a file naming both keys in a docstring, a comment, a tuple, a subscript
  **read**, a `.get()` and a dict literal — not flagged;
- the same pair for the frontend scanner;
- the examined-file count asserted non-zero, plus a test naming four files the
  scan must have walked, so a scanner that silently narrowed itself fails here
  rather than passing everywhere.

`tests/` is excluded because plan 02-02's engine smoke test deliberately pins a
swapper model and resolution as a fixture. The exclusion and its reason are in
the module docstring, since an unexplained exclusion in a gate is
indistinguishable from a hole.

And the behaviour is closed, not just the source: both presets are seeded,
applied, and asserted to resolve to the `"128"` their source profile stored.

## Deviations from Plan

**1. [Rule 3 — Blocking] `.gitignore` swallowed the committed seed.**
Found during Task 1. `git add visoswap/settings/data/presets_seed.json` failed:
the repository ignores `data/` for the runtime frame cache at `./data/`, and
unanchored that pattern also matches `visoswap/settings/data/`. The plan chose
that path without noticing. Anchored the rule to `/data/` — verified the only
`data` directory anywhere in the tree is the new one, so nothing else changed
status — and added `test_the_seed_is_actually_tracked_by_git`, which shells out
to `git ls-files --error-unmatch` and fails rather than skips when git is absent.
Without the test, the seed would have existed on exactly the machine that
generated it, every other test here would have passed on that machine, and the
failure would have surfaced as an empty presets table on somebody else's. Commit
`d632e09`.

**2. [Rule 2 — Missing critical functionality] Neither generated file was
package data.** `pyproject.toml` declared package data only for CLIPseg's
tokenizer. `schema.json` and the new seed are both generated offline and
committed precisely so a user machine needs neither source — and a wheel would
have shipped neither. Added `"visoswap.schema" = ["*.json"]` and
`"visoswap.settings" = ["data/*.json"]`. Commit `d632e09`.

**3. [Rule 2 — Missing critical functionality] `dispatch` validates before the
target.** Described under Task 2 above. Commit `4f6e0f2`.

**4. [Rule 1 — Bug] The Qt-source scan reads docstrings as source.** The first
version of `handlers.py` said "fires a Qt slot" in its module docstring;
`tests/test_no_qt_source.py` flags the token `slot` outside `#` comments and the
suite went red. Reworded to "Qt callback". The gate is right and the prose was
wrong — worth knowing for Phases 4 and 5, since the obvious vocabulary for
describing what was removed is the vocabulary the gate forbids. Commit `4f6e0f2`.

No architectural decisions were needed; nothing was installed. Rule 4 never
fired.

## Verification, verbatim

```
$ python -m pytest tests/ -q
286 passed in 50.07s

$ python -B tools/migrate_profiles.py
UNCHANGED: D:\Dev\visoswap\visoswap\settings\data\presets_seed.json (2 presets)
$ git diff --exit-code -- visoswap/settings/data/presets_seed.json
SEED CLEAN

$ python - (seed shape check from the plan)
2 presets, 168/33, no string where the schema declares a number or a bool

$ python - (presets table, seeded twice)
first : {'inserted': 2, 'updated': 0, 'unchanged': 0} rows = 2
second: {'inserted': 0, 'updated': 0, 'unchanged': 2} rows = 2

$ python -m pytest tests/test_no_swap_model_overrides.py -q
7 passed in 0.20s

$ python - (inference-stack scan over visoswap/settings)
settings package free of the inference stack (8 files)

$ python -m pytest tests/test_engine_seal.py -q
8 passed

$ python -m pytest tests/test_qt_free.py tests/test_no_qt_source.py \
      tests/test_vendor_headers.py tests/test_dropped_modules.py -q
18 passed
$ grep -n PENDING_QT_STRIP tests/conftest.py
68:PENDING_QT_STRIP = frozenset()

$ find "D:/Visomaster" -newermt '-1 day' -not -path '*/dependencies/*'
D:/Visomaster/output/...  D:/Visomaster/web_uploads/...
  (runtime artifacts written 2026-08-22 03:13-03:18 by the user's own web-UI
   session, before this one; no source file and no __pycache__ is newer)
$ git -C "C:/Users/Sakat/.devswarm/repos/1/ef7f9c3d/config-parallel-setup" status --short
  (empty)
$ git -C D:/Dev/visoswap status --short
  (empty)
```

Test count: **237 → 286**, +49. No test skips; none were added.

`ruff` is installed on none of the three interpreters and the repository carries
no ruff config, so the workflow's lint step has nothing to run. Recorded rather
than reported as a pass.

## Requirements marked

All four Schema & Settings requirements moved to Complete, in the checkbox list
and the traceability table, via `gsd-tools query requirements.mark-complete`.
`.planning/ROADMAP.md` and `.planning/STATE.md` were deliberately not touched.

| Requirement | Why it is genuinely true now |
|---|---|
| SCHEMA-01 | `visoswap/schema/schema.json`, 201 entries, every one carrying an explicit `type` and an already-typed `default`. Landed in 03-01; the requirement was still listed Pending. |
| SCHEMA-02 | `store.resolve` applies face → project → global → schema default, and `resolve_parameters` / `resolve_control` return whole tiers with every key present. Landed in 03-01 and completed in 03-02; still listed Pending. |
| SCHEMA-03 | `visoswap/settings/faces.py` keys the face tier by a digest of the recognition embedding, with the embedding and its model name stored beside it for threshold matching. Landed in 03-02; still listed Pending. |
| SCHEMA-04 | This plan. Both profiles are in the committed seed and seed into `setting_presets` as exactly 2 rows. |

ENGINE-01 was left Pending: it belongs to Phase 2 and is not this plan's to
judge.

## Known Stubs

None. Nothing in this plan is placeholder, no test is skipped, and every
`<verify>` in the plan was run and is quoted above.

## Threat Flags

None. This plan adds no network endpoint, no auth path and no schema change at a
trust boundary. The one new filesystem read is a package-relative constant path
(`SEED_PATH`), derived from `__file__` and never from a caller.

## What Phase 4 inherits

- `presets.seed_presets(connection)` at connect time, idempotent, alongside
  `db.apply_settings_schema`.
- `handlers.dispatch(key, value, models_processor)` for the two engine-affecting
  settings, and `handlers.effective_playback_fps(...)` for the scheduler — the
  single derivation of what rate playback runs at.
- The reassignment gate begins scanning `backend/` the moment that directory
  exists. It needs no edit to start.

One thing to watch: `apply_preset` clears a stale override for a DFM model that
is not on the machine, and reports it. Phase 4 should surface that report rather
than dropping it, or a preset will quietly come out one setting lighter than the
user expects.

## Self-Check: PASSED

All 8 created files present on disk. All 6 commits found in `git log`.
