---
phase: 03-settings-schema-three-tier-resolution
plan: 02
subsystem: settings-schema
tags: [settings, face-identity, embedding, validation, gating, three-tier, sqlite]
status: complete

requires:
  - 03-01 (visoswap/schema/schema.json -- 201 typed entries, normalised gates)
  - 03-01 (visoswap/schema/__init__.py -- stdlib loader, dfm_models, effective_default)
  - 03-01 (visoswap/settings/db.py -- project_faces and face_settings DDL)
  - 03-01 (visoswap/settings/store.py -- the face/project/global/default chain)
  - 02-02 (FaceCard.face_id -- the content-addressed digest this reuses as the key)
  - 01-01 (attribution gate, Qt-reachability probe, engine-interpreter runner convention)
provides:
  - visoswap/settings/faces.py (embedding-keyed face identity, threshold matching, injectable metric)
  - visoswap/settings/validate.py (strict validation and migration-only coercion, both schema-driven)
  - visoswap/settings/gates.py (visibility evaluator, quarantined from resolution by an AST test)
  - store.resolve_parameters / store.resolve_control (whole-tier mappings, every key present)
  - tests/_similarity_runner.py (engine-interpreter agreement runner)
affects:
  - 03-03 is the only permitted caller of validate.coerce; a test fails if a second one appears
  - 04 passes ModelsProcessor.findCosineDistance into faces.resolve_identity and deletes the transcription
  - 04 stores resolve_parameters(...) under each face id in EngineContext.parameters
  - 05 renders visibility from gates.is_visible and must not filter resolved values by it

tech-stack:
  added: []
  patterns:
    - "one metric, one threshold: identity matching reuses the swap pipeline's own SimilarityThresholdSlider rather than inventing a second notion of 'same face'"
    - "transcribe behind an injection point and pin it with a cross-interpreter agreement test that fails rather than skips"
    - "strict and lenient as two named entry points, with the lenient one's caller set asserted against the tree"
    - "validate on read as well as on write, and name the tier in the read-side error -- the tier is the diagnosis"
    - "derive test values from the schema entry rather than listing them, so a retyped control moves the sweep with it"

key-files:
  created:
    - visoswap/settings/faces.py
    - visoswap/settings/validate.py
    - visoswap/settings/gates.py
    - tests/_similarity_runner.py
    - tests/test_face_settings.py
    - tests/test_settings_validation.py
    - tests/test_settings_gates.py
  modified:
    - visoswap/settings/store.py
    - visoswap/settings/__init__.py
    - tests/test_settings_resolution.py
    - tests/test_schema_generated.py
    - tests/test_vendor_headers.py
    - docs/settings-schema.md

decisions:
  - "Two keys cannot carry a distinct second value, not one. The plan anticipated only the dynamic key; FaceEditorTypeSelection ships with a single option and is equally unsweepable. Both are named in NO_ALTERNATE and covered by a separate test, and a third joining them fails."
  - "The plan's prose says 8 keys have a gated parent. Counted, it is 11 -- the same correction plan 03-01 already recorded and the plan text still carries."
  - "Option membership for the one dynamic key is enforced on write and not on read. A deleted model file is not corruption, and because resolution reads the whole tier at once, treating it as corruption would take all 201 settings down with one removed file."
  - "The step is not a constraint. An off-step value is accepted, because upstream never enforced it and rejecting it would make legitimate stored values unwritable."
  - "Ints widen to float for float keys; floats do not narrow to int. A renderer never sends 1.0 for a slider sitting at one, and 2.5 for a step count is a caller who has not decided what they mean."
  - "Under the 'last' gate rule the discarded parents are discarded from the visibility chain too, so one notion of which parents matter answers both the value question and the visibility question. Not observable in the committed schema; pinned against a hand-built one."
  - "Transitive visibility is a deliberate divergence from upstream, which draws controls nested under switched-off sections."

metrics:
  duration: ~70 minutes
  completed: 2026-08-22
  tasks: 3
  commits: 4

actuals:
  tokens: 71000
  tasks: 3
  commits: 4
---

# Phase 03 Plan 02: Face Tier, Validation & Gates Summary

The face tier is now keyed by what the recogniser saw rather than by where a face
sat in a list, matched by the swap pipeline's own metric and threshold; every
write and every read is held to rules read out of the schema and nowhere else; and
all 201 keys are carried through the full tier chain in both directions with their
declared types intact, while the gate evaluator sits beside that path and is
provably not on it.

## The inherited RED file: sound, kept, and two things added

A previous session was cut off after Task 1 with `tests/test_settings_validation.py`
written and untracked -- 546 lines, failing with an `ImportError` because the
module it tests did not exist. That is the RED state the plan asks for, not a
fault, and the file is a good one. Every item in the plan's behavior block is
covered, each representative key is checked against the schema before being used
so a retyped control fails the premise rather than the assertion, and the two
traps the plan names -- `isinstance(True, int)`, and an int being a perfectly
good float -- are each tested in both directions rather than once.

Two things were checked rather than taken on trust:

**The nine measured raw defaults are accurate.** All nine were re-read from
`D:/Visomaster` before anything was implemented: `SimilarityThresholdSlider`
`'60'`, `StrengthAmountSlider` `'100'`, `nThreadsSlider` `'2'`,
`ColorBrightnessDecimalSlider` `'1.00'`, `FaceExpressionVYRatioDecimalSlider`
`'-0.125'`, `ViewFaceMaskEnableToggle` `False`, `SwapModelSelection`
`'Inswapper128'`, `ThemeSelection` `'Dark'`, `ClipText` `''`. The table spans all
five shapes and both tiers, as its own test asserts.

**One test was passing for an accidental reason.** The read-side tier-naming test
picked its global key as "the first global-tier key of any shape", which today is
a toggle and therefore does reject the string payload the test inserts. It would
have stopped meaning anything the day a text key sorted first, since a text key
accepts `"not an int"` quite happily. Narrowed to the first global-tier **int**
key. That is the only change made to the inherited file before it was committed as
the RED artifact.

The two tests added *after* the validator existed (the dynamic-key read
relaxation, below) went into the GREEN commit, not the RED one, because they
document a decision that was made while implementing rather than before.

## Three things in the plan that are factually wrong

**1. "8 keys have a parent that is itself gated."** It is **11**. The depth census
is `{0: 57, 1: 133, 2: 11}` -- a complete partition of 201 -- and the eleven are
the 3 pipe-gated `FaceExpression...` sliders plus 8 `FaceParser*Makeup*` keys.
Plan 03-01's summary already recorded this correction; plan 03-02's
`<measured_facts>` carries the uncorrected number anyway. Max depth 2 and zero
cycles both hold. Now pinned in `tests/test_settings_gates.py` as well as in
`tests/test_schema_generated.py`.

**2. "skip only the dynamic key whose option list is empty."** There are **two**
keys with no second value, not one. `FaceEditorTypeSelection` is a selection whose
option list upstream is exactly `['Human-Face']`, so there is no different member
to override with. Both are named in `NO_ALTERNATE`, both are still asserted to
resolve and to carry their declared type, and a test fails if a third key joins
them. Sweep coverage is therefore 166 of 168 project keys and 33 of 33 global,
with the remaining two covered explicitly.

**3. "every value in `profiles.json` is a string."** Measured across both saved
profiles: `options` is 20 str + 13 bool, and `parameters` is 138 str + 30 bool in
the first profile, 136 str + 30 bool + **2 int** in the second (`SimilarityThresholdSlider: 20`,
`StrengthAmountSlider: 150`). Toggles are already real bools upstream and two
numbers are already real ints. This matters for plan 03-03: the lenient converter
must accept a bool for a toggle and an int for an int key as-is, which it does,
because it mirrors the schema generator's own shape rule exactly. Had the
converter been written to the plan's claim -- string in, typed out -- migration
would have failed on 43 toggles at the first profile.

Both profiles hold exactly 33 options and 168 parameters, which independently
corroborates the tier split.

## Task 1 -- the face tier, keyed by embedding (commit `34dafd9`)

Executed by the previous session; described here from the commit and the code
because this summary covers the whole plan.

`visoswap/settings/faces.py` (440 lines, standard library only -- `array`,
`hashlib`, `math`, `sqlite3`, `logging`) stores an embedding as raw
little-endian float32 bytes beside the name of the recognition model that
produced it, with the dimension recoverable from the byte length. The face key is
the same digest `FaceCard.face_id` derives, so a first sighting produces a stable
content-derived identifier rather than a counter, and Phase 2's exact-digest
keying becomes threshold matching here without the key changing shape.

`resolve_identity` scores against every identity stored for the project **under
the same recognition model** -- filtered in SQL, because embeddings from the four
models live in unrelated spaces and a model mismatch is a miss rather than an
error -- and returns the **best** match at or above the threshold, not the first.
First-past-the-post would hand the settings to whichever row sorted earlier once
the threshold was loosened enough for two people to clear it.

The threshold is `SimilarityThresholdSlider` itself, at the project tier, not a
new constant. A user who loosens matching for swapping loosens it for settings
identity too, deliberately: one notion of "same face" is better than two that can
disagree.

The similarity function is a pure-Python transcription of
`ModelsProcessor.findCosineDistance` -- which returns `100 - (1 - cos) * 50`, a
0-to-100 **similarity**, not a 0-to-1 distance -- exposed behind an injection
point so Phase 4 can pass the engine's bound method and delete the duplicate.
`tests/_similarity_runner.py` runs the engine's own unbound method on the engine
interpreter over vectors the test writes and reports both its scores and its face
ids; `tests/test_face_settings.py` asserts agreement to 1e-4 over seven pairs and
exact digest agreement over three identities. A missing engine interpreter
**fails** -- verified again in this session, below.

## Task 2 -- validation at the boundary (commits `9cd3cda` RED, `6a347df` GREEN)

`visoswap/settings/validate.py` (312 lines) has two entry points that are
deliberately not one. `validate` is strict and takes the type the schema declares;
`coerce` is lenient, exists for migration, applies
`tools/dump_engine_settings.py`'s own shape rule so the migration path and the
generation path cannot disagree, and then runs the strict validator on what it
produced. A test walks `visoswap/` and `backend/` and fails if anything other than
the validator itself calls `coerce`.

No settings key appears as a literal in the module; a test pins that against all
201 names. Bools are tested before ints in every numeric branch -- asserted across
all 93 ints, 42 floats and 43 toggles, not on one key. `VALIDATORS`' key set is
compared directly against the schema's declared type vocabulary, so a sixth shape
appearing upstream fails here rather than raising a `KeyError` on the first write
of such a key.

Every write in `store.py` now validates -- global, project and face alike -- and
so does every read, because a row written by a migration or by hand has never
passed the write gate. `CorruptStoredSetting` subclasses the write-side error and
names the **tier** the offending row sits in.

### The one asymmetry, and why it exists

Read-side validation initially broke a 03-01 test, and the failure was the right
kind: `resolve` refused a value it had itself just accepted. For the one key whose
options are a directory listing, the option list changes without anyone editing a
setting. Deleting a model file would otherwise turn an ordinary stored selection
into a hard read failure -- and since resolution reads the whole tier at once, one
removed file would take all 201 settings down with it, reporting a corrupt
database that was not corrupt.

So `validate_stored` enforces type, bounds and length in both directions but
enforces **membership only on the way in**, and it detects the dynamic case from
the schema's own `options_from` field rather than from a key name. Two tests pin
both halves: a stored selection whose file has vanished still resolves, and a
value that was never a model is still refused on the way in.

The 03-01 test that surfaced it was also made honest. It wrote a name nothing on
disk answered to, while validating it against the ambient repository models
directory rather than the temporary one it read back from -- which passed only for
as long as that directory stayed empty. It now writes a second model that actually
exists in its own directory.

## Task 3 -- 201 keys through three tiers, and the gates kept out (commit `97a59d0`)

`tests/test_settings_resolution.py` derives a second and third acceptable value
for each key from its own schema entry -- numerics step off the default toward
whichever bound has room, toggles invert, selections take another member -- and
carries each project key through five steps: default, project override, face
override, face cleared, project cleared, asserting `type(...)` as well as the
value at every one. 166 project keys plus the two named exclusions, and all 33
global keys. For the 43 toggles and the one two-option selection there is no third
value, so the face tier is given the schema default; a face row that was ignored
would return the project value there, not the default it actually returns.

`store.resolve_parameters` and `store.resolve_control` return whole tiers with
every key present, always -- 168 and 33. That is the mapping `frame_worker`
indexes by face id at four sites, and it reads its keys unconditionally.

`visoswap/settings/gates.py` (177 lines) evaluates both mechanisms, both compound
spellings, and transitive chains, and raises `GateCycle` rather than recursing.
Its docstring opens with the rule that matters, and the rule is asserted rather
than asserted about: `tests/test_settings_gates.py` closes a gate, resolves the
child key, gets its value back, finds the key still present in
`resolve_parameters`, and separately parses `store.py`'s AST to prove it does not
import the module at all.

The two compound spellings are tested discriminatingly, not just exercised: the
pipe case asserts `True/False` is **hidden**, which an OR would show, and the
comma case asserts `False/True` is **shown**, which an AND would hide.

## Deviations from Plan

### Auto-fixed

**1. [Rule 1 - Bug] Read-side validation refused a value the write side accepted**
- **Found during:** Task 2
- **Issue:** validating the dynamic key's option membership on read made
  `tests/test_schema_generated.py::test_resolution_ends_at_the_resolved_default_not_the_frozen_null`
  fail, and would have made any deleted model file break every settings read.
- **Fix:** `validate.validate_stored`, membership enforced on write only, detected
  from `options_from` rather than from a key name. Two new tests pin both halves.
- **Commit:** `6a347df`

**2. [Rule 1 - Bug] A 03-01 test passed only while a directory stayed empty**
- **Found during:** Task 2
- **Issue:** the dynamic-key resolution test wrote a filename nothing answered to
  and validated it against the ambient repository models directory, not the
  temporary one it read from.
- **Fix:** the test now creates a second model in its own directory and overrides
  to that.
- **Commit:** `6a347df`

**3. [Rule 2 - Missing coverage] The inherited RED test's global key was shape-blind**
- **Found during:** Task 2, before the RED commit
- **Fix:** narrowed to the first global-tier int key, with the reason in a comment.
- **Commit:** `9cd3cda`

**4. [Rule 2 - Missing coverage] A second unsweepable key**
- **Found during:** Task 3
- **Issue:** the plan named one key that cannot carry a distinct alternate value.
  There are two.
- **Fix:** `NO_ALTERNATE` names both with reasons, a test fails if a third
  appears, and both are still asserted to resolve with their declared type.
- **Commit:** `97a59d0`

### Additions beyond the plan's letter

- `store.resolve_parameters` / `store.resolve_control` as named functions rather
  than an unnamed "whole-tier resolution", so the two engine-context fields they
  correspond to are visible at the call site.
- `gates.deciding_parents` exposed, because the `last` rule means the parent list
  and the deciding list are not the same list and callers should not re-derive it.
- Discriminating assertions on the two compound gate spellings, rather than
  assertions that merely pass under either reading.

### Authentication gates

None. This plan installs nothing and reaches no network.

## Verification

```
python -m pytest tests/ -q
237 passed in 49.44s
```

```
VISOSWAP_ENGINE_PYTHON="D:/nope/does-not-exist/python.exe" python -m pytest tests/test_face_settings.py -q
1 failed, 21 passed, 4 errors in 0.43s
```
Fails rather than skips, in both places it can: the standalone reachability test
and the session fixture. Zero skips.

```
resolution does not import the gate evaluator
no key literals in validator
__init__.py []
db.py ['sqlite3']
faces.py ['array', 'hashlib', 'logging', 'math', 'sqlite3', 'sys', 'visoswap']
gates.py ['visoswap']
store.py ['json', 'sqlite3', 'visoswap']
validate.py ['visoswap']
```
No module under `visoswap/settings/` imports numpy, torch, onnxruntime, cv2 or
kornia. `tests/test_engine_seal.py`, `tests/test_qt_free.py`,
`tests/test_no_qt_source.py` and `tests/test_vendor_headers.py` all pass inside the
237, and `PENDING_QT_STRIP` is still empty.

Read-only trees unmodified: `git status --short` in
`C:/Users/Sakat/.devswarm/repos/1/ef7f9c3d/config-parallel-setup` prints nothing,
and no `.py` file or `__pycache__` under `D:/Visomaster` outside `dependencies/`
has been touched. The only recent mtimes there are `output/` and `web_uploads/`,
which are the running application's own artifacts and predate this session.

Per-file: `test_face_settings.py` 26, `test_settings_validation.py` 34,
`test_settings_gates.py` 13, `test_settings_resolution.py` 17.

`ruff check` was not run: ruff is not installed on any interpreter in this
environment and the repository carries no ruff configuration.

## Success Criteria

- **Roadmap criterion 3 met.** All 201 keys carried through the tier chain with
  declared types asserted at every step, the face tier keyed by recognition
  embedding. Two keys have no distinct second value and are covered explicitly.
- **Reload survival.** A perturbed embedding above the threshold resolves to the
  same key and finds its override; one below does not, and gets a different
  identity. Both directions, since a matcher returning one key for everything
  passes the first half perfectly.
- **Wrong values rejected at the boundary**, with rules read from the schema and
  no key name hardcoded in the validator, on every write path and every read path.
- **Gate evaluation exists and is provably absent from the resolution path** --
  by AST, by source scan, and by resolving a gated-off key and getting its value.

## Known Stubs

None.

## Threat Flags

None. No new network surface, no new file access pattern, no new trust boundary.
The threat register's `mitigate` dispositions for this plan are all implemented:
T-03-08 (identity scoped to the recognition model, matched at the pipeline's own
threshold, agreement test against the engine's method), T-03-09 (strict validation
on every write and every read, bools before ints), T-03-10 (bound parameters
everywhere including the model-name filter, hostile ids round-tripped as data),
T-03-11 (visited-chain cycle guard), T-03-14 (read-side failures name the tier).

## Open Items For The Human

`.planning/REQUIREMENTS.md` still lists SCHEMA-01, SCHEMA-02 and SCHEMA-03 as
Pending. 03-01 did not mark SCHEMA-01 and this plan has not marked SCHEMA-02 or
SCHEMA-03, to stay consistent and because planning-state files are yours to
update. All three are now satisfied in the tree.

## Self-Check: PASSED
