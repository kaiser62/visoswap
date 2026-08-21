---
phase: 03-settings-schema-three-tier-resolution
plan: 01
subsystem: settings-schema
tags: [schema, settings, sqlite, three-tier, generator, gating]
status: complete

requires:
  - 02-01 (tools/dump_engine_settings.py -- shape_of and coerce, imported not copied)
  - 02-01 (tests/fixtures/engine_settings.json -- the agreement target)
  - 01-01 (attribution gate, Qt-reachability probe, conftest tree walk)
provides:
  - tools/generate_schema.py (the offline generator; the project's one Qt-touching step)
  - visoswap/schema/schema.json (201 typed entries, committed)
  - visoswap/schema/__init__.py (Qt-free loader; dfm_models() directory scan)
  - visoswap/settings/db.py (SETTINGS_SCHEMA -- all five tables, one SQL string)
  - visoswap/settings/store.py (face -> project -> global -> default resolution)
  - docs/settings-schema.md
affects:
  - 03-02 does threshold matching against project_faces.embedding, which is why the blob is stored beside the digest
  - 03-03 seeds setting_presets and gives 3 of the 6 dropped side effects a handler
  - 04 executes SETTINGS_SCHEMA rather than hand-copying it, and wires dfm_models() into the engine context
  - 05 renders from schema.json; type, gate, bounds and options all come from there

tech-stack:
  added: []
  patterns:
    - "generate offline on the interpreter that can, commit the output, and let the runtime read stdlib JSON -- the layout dicts are never reachable at runtime"
    - "rewrite a generated file only when its content changes, so a date and a source path can live in the header without making regeneration diff every day"
    - "read the evaluator, not the punctuation: 'A|B' is an AND upstream and 'A, B' is last-parent-wins"
    - "record an upstream bug as measured rather than silently improving on it -- a renderer that hides a control upstream shows is a behaviour change nobody asked for"

key-files:
  created:
    - tools/generate_schema.py
    - visoswap/schema/__init__.py
    - visoswap/schema/schema.json
    - visoswap/settings/__init__.py
    - visoswap/settings/db.py
    - visoswap/settings/store.py
    - tests/test_settings_resolution.py
    - tests/test_schema_generated.py
    - tests/test_schema_fixture_agreement.py
    - docs/settings-schema.md
  modified:
    - tests/test_vendor_headers.py

decisions:
  - "The gate-chain depth census is 57/133/11, not 8 chained keys. 11 keys have a parent that is itself gated. Max depth 2 and zero cycles both hold."
  - "Upstream's comma gate is last-parent-wins, not AND. Its loop assigns rather than combines, so only the last parent has any effect. Recorded as rule 'last'."
  - "Upstream's pipe gate is an AND, not an OR. The evaluator starts True and clears on any unchecked parent. Recorded as rule 'all'."
  - "The generator writes only when the widgets content changes. Otherwise the header's generation date and source path would make `git diff --exit-code` fail every day and on every machine, defeating the only staleness guard the phase has."
  - "store.resolve ends its chain at schema.effective_default rather than the raw default field, so the one key with a dynamic default is not the one key resolution returns None for."
  - "Writes are refused at the wrong tier, not just for unknown keys. A global key stored per project would sit there and never be read -- the exact 'appears to save, does not apply' failure this phase removes."

metrics:
  duration: ~55 minutes
  completed: 2026-08-22
  tasks: 3
  commits: 4

actuals:
  tokens: 49000
  tasks: 3
  commits: 4
---

# Phase 03 Plan 01: Settings Schema & Three-Tier Resolution Summary

201 settings keys frozen into one committed `schema.json` with a shape-derived type
and an already-typed default, a Qt-free loader that resolves the single dynamic
option list at load rather than at freeze, and a three-tier store that carries
`SimilarityThresholdSlider` from `60` down to a face override and back with
`type(...) is int` holding at every step.

## Every count in the plan held. One did not.

**Held, verified independently against `D:/Visomaster` before writing anything:**
201 keys, 168 project (common 23 + swapper 103 + face_editor 42) and 33 global,
zero overlap. Histogram exactly 93 int / 43 toggle / 42 float / 22 selection /
1 text. Default types 157 str + 43 bool + 1 callable, zero numeric. 140
`parentToggle` and 4 `parentSelection`, `requiredToggleValue` `True` for all 140
without exception, all 4 selection gates naming `SwapModelSelection`. 6
`exec_function` keys. `DFMModelSelection` the sole dynamic key. Zero cycles, max
gate depth 2. `ClipText` the sole fifth-shape key, `width: 130`, `default: ''`,
no `step`, no `decimals`.

**Did not hold — the chained-gate count.** The plan says *"8 keys have a parent
that is itself gated"*. Measured: **11**. The full depth census is 57 ungated /
133 gated by an ungated parent / 11 whose parent is itself gated — which sums to
201, so it is a complete partition rather than a sample. The 11 are the 3
`FaceExpression...` pipe-gated sliders plus 8 `FaceParser*Makeup*` keys. Max depth
2 and zero cycles are both unaffected. Pinned as
`EXPECTED_DEPTHS = {0: 57, 1: 133, 2: 11}` in `tests/test_schema_generated.py`
with a failure message that tells the next reader to report a disagreement rather
than quietly edit the number.

None of 201, 168, 33 or 93/43/42/22/1 moved, so Phases 4 and 5 are planning
against a current key set.

## What the plan asked me to read the evaluator for, and what it said

The plan said to read upstream's gate evaluator before deciding how the two
compound `parentToggle` spellings combine, and to record last-parent-wins plainly
if that is what it turned out to be. It is — and the pipe form is worse than that.
From `app/ui/widgets/actions/common_actions.py`, the `'Toggle' in
parent_widget_name` branch:

- **`'A|B'` is an AND.** The evaluator starts at `True` and clears it if *any*
  parent is unchecked. The pipe reads like OR; the code is not. 3 keys, all
  `FaceExpression...` sliders. Recorded as rule `all`.
- **`'A, B'` is last-parent-wins.** The loop body is
  `parentToggle_ischecked = main_window.parameter_widgets[name].isChecked()` — a
  plain assignment, not a combination — so every iteration discards the previous
  one and only the last parent has any effect whatsoever. 2 keys
  (`OccluderXSegBlurSlider`, `RestoreEyesMouthBlurSlider`). That is a bug
  upstream. Recorded as rule `last`, not improved into the `all` a reader would
  expect: a renderer that hides a control upstream shows is a behaviour change
  nobody asked for, and it would be invisible in a diff of a generated file.

Two further upstream quirks found while confirming this, neither reproduced here
and both now in `docs/settings-schema.md`:

- The evaluator matches with `if parent_widget_name in parentToggles` — a
  **substring** test on the raw string. **10 keys** have a parent whose name is a
  substring of another key's name (`HairMakeupEnableToggle` inside
  `FaceParserHairMakeupEnableToggle`, `ColorEnableToggle` inside
  `AutoColorEnableToggle`, and so on), so toggling the shorter control also drives
  widgets gated on the longer one.
- Gates are only ever evaluated against widgets in the **same group**
  (`parent_widget.group_layout_data`). A cross-group gate would never fire. All
  144 gates are intra-group today, so nothing is currently broken by it — but
  Phase 5 should not assume a gate it invents across groups would work upstream.

## What Was Built

**Task 1 — the tracer** (commit `a57da09`). `tools/generate_schema.py` on the Qt
interpreter, importing `shape_of` **and** `coerce` from
`tools/dump_engine_settings.py` rather than restating either. The one deliberate
divergence is the callable default, which emits `null` here and `''` in the
fixture, and `tests/test_schema_fixture_agreement.py` asserts that is the *only*
one across all 201 keys — value and type both, with bool tested before int.

`visoswap/schema/__init__.py` is stdlib-only and both Phase 1 gates picked it up
automatically by walking the tree, which is the property those gates were built
for. `visoswap/settings/db.py` declares all five tables in one SQL string so
03-02 and 03-03 add files instead of editing it. `visoswap/settings/store.py`
resolves face → project → global → default, binds every parameter, and refuses an
unknown key.

`tests/test_settings_resolution.py` runs the tracer against a **real on-disk**
SQLite database: default 60 → project 20 → face 35 → clear face → 20 → clear
project → 60, asserting `type(x) is int` at every step rather than relying on
equality, because the regression this phase exists to prevent is a value that
looks right and is typed wrong.

**Task 2 — the taxonomy** (commits `2f5d05c` RED, `f8868ee` GREEN). RED failed 9
of 27 against task 1's narrow entries. GREEN widened every entry to type, tier,
tab, group, label, help, int level, typed default, normalised gate, plus bounds /
decimals / character bounds / option list by shape, and named the six dropped side
effects.

**Task 3 — the dynamic list** (commit `d333cc5`). The frozen file carries `null`
options and `null` default for `DFMModelSelection` plus the two upstream function
names; the loader runs the scan. Directory resolution is argument → `MODELS_DIR`
→ repository-relative `model_assets/`, never a request field. Sorted, because
filesystem order makes two identical installs render differently. A **missing**
directory logs the path that was tried; an **empty** one does not, because those
are different facts and only one is the user's to fix — upstream's serializer
(`web_ui.py:648-664`) swallows both into `[]`, which is why a missing models
directory today produces an empty dropdown and no error anywhere.

## The Part That Needed More Than the Plan Asked

**The header would have broken the byte-identical check.** The plan wants a header
carrying a generation date and the VisoMaster source path (T-03-07), and *also*
wants `git diff --exit-code -- visoswap/schema/schema.json` clean after
regeneration (task 2 verify). Those two are incompatible as literally written: the
date changes tomorrow and the path changes on another machine, so the staleness
guard would fail for reasons that have nothing to do with staleness — and a guard
that cries wolf daily is a guard nobody runs. Resolved by comparing the `widgets`
content and **not writing at all** when it is unchanged. Regeneration is now a
genuine no-op (`UNCHANGED: ...`), and `generated` means "when this content last
changed", which is the more useful reading anyway.

**The dynamic key would have been the one key that resolved to `None`.** Task 1
ends resolution at the schema's `default` field; task 3 makes one key's default
`null` on purpose. Left alone, `resolve(conn, 'DFMModelSelection', ...)` would
return `None` — not a type error, just a silent wrong answer, which is worse.
`store.resolve` and `resolve_all` now end at `schema.effective_default(key,
models_dir)`, and the listing is cached per resolved directory so 201 default
reads cost one scan (T-03-05).

**Wrong-tier writes are refused, not only unknown ones.** T-03-02 asks for
unknown-key rejection. A *known* key written at the wrong tier is the same defect
wearing a better disguise: a global key stored in `project_settings` is accepted,
persisted, and never read by anything, which is precisely the "it saved and then
did nothing" failure this phase exists to remove. `store.WrongTier` closes it.
(Deviation Rule 2.)

## Deviations from Plan

**1. [Rule 2 — missing critical functionality] Wrong-tier writes refused.**
Found during Task 1. `store.WrongTier` raised when a key is written at a tier the
schema does not assign it to. Covered by
`test_a_key_cannot_be_written_at_a_tier_the_schema_does_not_put_it_in`.
Commit `a57da09`.

**2. [Rule 3 — blocking issue] Generator writes only on content change.**
Found during Task 2 while running the byte-identical verify. Without it, the plan's
own verification step 2 fails the day after generation. Commit `f8868ee`.

**3. [Rule 2 — missing critical functionality] `effective_default` in the
resolution chain.** Found during Task 3. Commit `d333cc5`.

**4. [documentation] `store.resolve` and `resolve_all` gained a trailing
`models_dir=None`.** The plan flags the resolution signature as a costly-to-change
contract; this is an optional trailing keyword, so every existing call site is
unaffected and Phase 4 can thread its configured models directory through without
a second lookup path.

## Threat Mitigations Applied

| ID | Mitigation | Where it is proven |
|---|---|---|
| T-03-01 | Every statement binds its parameters | `test_a_hostile_project_id_round_trips_as_data` — a project id that closes the literal, ends the statement and drops `project_settings`, asserted to round-trip as data with all five tables intact |
| T-03-02 | Unknown keys refused at every write and read boundary | `test_an_unknown_key_is_refused_at_every_boundary`, and the tables asserted still empty afterwards |
| T-03-05 | The listing is resolved once per directory, extension-filtered before the list is built | `_DFM_CACHE` in `visoswap/schema/__init__.py` |
| T-03-06 | Directory from argument / env / fixed default only; path normalised; only direct entries listed; nothing from the listing opened | `test_the_models_directory_comes_from_the_argument_then_the_environment` |
| T-03-07 | Header records version, date and source; regeneration asserted byte-identical | verification step 2, and `test_the_file_records_where_it_came_from` |
| T-03-SC | Nothing installed. `json`, `pathlib`, `sqlite3`, `os`, `logging` only | — |
| T-03-03 / T-03-04 | accepted per plan | — |

## Verification

```
### 1 full suite
Pytest: 158 passed
### 2 regeneration is a no-op
visomaster: D:\Visomaster
UNCHANGED: D:\Dev\visoswap\visoswap\schema\schema.json (201 keys: project=168 global=33 types={'toggle': 43, 'selection': 22, 'float': 42, 'int': 93, 'text': 1})

clean
### 3 phase 1 gates
Pytest: 12 passed
PENDING_QT_STRIP = set()
### 5 fixture agreement
Pytest: 4 passed
### 6 read-only trees
(visomaster scan above: empty = unmodified)
(reference checkout above: empty = clean)
```

Task-level verify blocks, verbatim:

```
201 keys, 168/33
dynamic list not frozen
Pytest: 11 passed          # tests/test_settings_resolution.py
Pytest: 29 passed          # tests/test_schema_generated.py
Pytest: 12 passed          # test_qt_free + test_no_qt_source + test_vendor_headers
```

158 passed, up from the 114 that closed Phase 2. No skips.

**Verification step 4 is wrong as written.** The plan asks that
`grep -rn "generate_schema" visoswap/` return nothing. It returns one line:
`visoswap/schema/__init__.py:3` — a docstring sentence saying where `schema.json`
comes from. The criterion's intent is that the generator is never *imported* from
the runtime package, and that holds:
`grep -rn "import generate_schema\|from generate_schema\|from tools" --include=*.py visoswap/`
returns nothing. The docstring pointer is worth keeping; the check should be the
import form.

## Success Criteria

1. **Roadmap criterion 1, this plan's half — met.** An offline generator produces
   201 valid JSON entries from the four layout dicts, each with an explicit
   shape-derived `type` and a typed non-string default.
2. **Roadmap criterion 2 — met.** `DFMModelSelection` carries `null` options and
   `null` default in the frozen file, asserted about the file on disk, and
   resolves against the models directory at load.
3. **Three-tier spine — met.** Proven in both directions on
   `SimilarityThresholdSlider` against a real on-disk database, typed `int` at
   every step.
4. **Schema and fixture agree — met.** All 201 keys, value and type, dynamic key
   the only exemption.
5. **Read-only trees untouched — met.** No file under `D:/Visomaster` modified in
   the last day outside `dependencies/`; the reference checkout's `git status
   --short` is empty. The generator ran under `-B` throughout.

## Notes for Later Plans

- **03-02:** `project_faces` stores `recognition_model` and the `embedding` blob
  beside the content-addressed `face_key`, because the key is a one-way digest and
  threshold matching needs the vector. Two embeddings from different recognition
  models are not comparable — the model name is stored so that can be enforced
  rather than assumed.
- **03-03:** `setting_presets` is declared and empty. Three of the six dropped
  side effects want a handler (`change_threads_number`,
  `change_execution_provider`, `set_video_playback_fps`); the other three are Qt
  view-refit and stylesheet concerns with no engine meaning, and
  `docs/settings-schema.md` says which is which.
- **04:** execute `visoswap.settings.db.SETTINGS_SCHEMA`, do not copy it. Wire
  `visoswap.schema.dfm_models()` into the engine context's DFM metadata rather
  than writing a second directory scan. Three tables reference `projects(id)`;
  turning `PRAGMA foreign_keys` on is the backend's decision and this module
  deliberately does not.
- **05:** render from `schema.json` only. Never infer a control type from a
  substring of the key name — `ClipText` is the proof that fails.

## Known Stubs

None. No placeholder values, no unwired data paths, no skipped tests.

## Self-Check: PASSED

All ten created files present on disk; `tests/test_vendor_headers.py` modified.
All four commits present in `git log`: `a57da09`, `2f5d05c`, `f8868ee`, `d333cc5`.
