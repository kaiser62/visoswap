# The settings schema

`visoswap/schema/schema.json` is the single authority on the type, tier, default
and presentation of every setting in the project. It is generated offline and
committed. Nothing at runtime reads VisoMaster's layout dictionaries.

## Why it exists

Today a setting's type lives in a Qt window. `main_window.default_parameters`
(`app/ui/main_ui.py:67`) is the table `visomaster_headless.py:88-89` coerces every
incoming value against, and every `default` in the four layout dicts is a
**string** — `SimilarityThresholdSlider` is `'60'`, `FaceEditorCropScaleDecimalSlider`
is `'2.50'`, `ClipAmountSlider` is `'50'`. So is every value in `profiles.json`.
Qt widgets accept strings and coerce internally, so nothing upstream ever had to
care. The engine does: hand it `'50'` and it fails deep inside a tensor operation,
a long way from the load that caused it.

The schema moves type out of the window and into the data.

## The entry shape

Every entry carries these fields:

| Field | Meaning |
|---|---|
| `type` | One of `int`, `float`, `text`, `selection`, `toggle`. Derived from **widget shape**. |
| `tier` | `project` or `global`. |
| `tab` | `common`, `swapper`, `face_editor` or `settings` — the layout module it came from. |
| `group` | The collapsible box heading. `face_editor` has a single unnamed group, so this is `""` for its 42 keys. |
| `label` | The control's visible name. |
| `help` | The tooltip text. |
| `level` | `1`, `2` or `3` as an **int** (upstream stores the string `'1'`). |
| `default` | The typed default. `null` only for the one dynamic key. |
| `gate` | The visibility gate, or `null`. See below. |

Shape-specific fields, present only where the shape has them:

| Field | Shapes | Meaning |
|---|---|---|
| `minimum`, `maximum`, `step` | `int`, `float` | Slider bounds, coerced to the entry's own type. |
| `decimals` | `float` | Decimal places. Never present on a non-float. |
| `min_length`, `max_length` | `text` | **Character-count** bounds. Deliberately not named `minimum`/`maximum`. |
| `options` | `selection` | The option list, or `null` for the dynamic key. |
| `options_from`, `default_from` | the dynamic key only | The upstream function whose result is resolved at load. |
| `exec_function` | 6 keys | The qualified name of a side-effect handler that was dropped. |

The file also carries a `schema` header object recording the version, the date the
content last changed, the source checkout, and the fact that the content is derived
from GPLv3 material. `schema.json` is not a `.py` file, so the attribution gate in
`tests/test_vendor_headers.py` never walks it; that note is what records the licence
instead.

## The five widget shapes, and the `step` discriminator

Type comes from the shape of the spec, never from the key name and never from the
Python type of `default` (157 of the 201 defaults are strings).

| Shape | Count | Type |
|---|---|---|
| `decimals` alongside min/max/step | 42 | `float` |
| min/max/step without `decimals` | 93 | `int` |
| min/max with **no** `step` | 1 | `text` |
| an `options` list | 22 | `selection` |
| none of the above | 43 | `toggle` |

**`step` is what separates a slider from a text box.** A slider has one, a line
edit does not. `ClipText` is the only key in the fifth shape: its `min_value` of
`'0'` and `max_value` of `'1000'` are character-count bounds and its default is
`''`. A four-shape rule does not merely mislabel it — it crashes, because
`int(float(''))` raises. `ClipText` is also the only key carrying `width`.

**The name convention is wrong and the current web UI trusts it.**
`webui2/app.js:145-159` picks a control by substring: a key containing `Toggle`
renders a checkbox, one containing `Selection` renders a select, and everything
else falls through to a range slider. Line 179 parses with `Number` if the key
contains `Decimal` and `parseInt` otherwise. `ClipText` contains none of those, so
today it renders as a slider from 0 to 1000 over a text field. That is the concrete
failure the explicit `type` exists to prevent.

## Gating

Upstream has two mechanisms. Both are normalised into one `gate` object:

```json
"gate": {
  "mechanism": "toggle",
  "parents": ["OccluderEnableToggle", "DFLXSegEnableToggle"],
  "rule": "last",
  "required_value": true
}
```

- **`parentToggle`** — 140 keys. `requiredToggleValue` is `true` for all 140
  without exception.
- **`parentSelection`** — 4 keys, all naming `SwapModelSelection`.
  `SwapperResSelection` requires `Inswapper128`; `DFMModelSelection`,
  `DFMAmpMorphSlider` and `DFMRCTColorToggle` each require `DeepFaceLive (DFM)`.

`parentToggle` is a plain string with two undocumented compound spellings. Their
rules were read out of upstream's evaluator — the `'Toggle' in parent_widget_name`
branch of `app/ui/widgets/actions/common_actions.py` — and **not** inferred from the
punctuation, because the punctuation is misleading:

| Spelling | Rule | Upstream's actual behaviour | Keys |
|---|---|---|---|
| `A` | `single` | one parent | 135 |
| `A\|B` | `all` | starts True, cleared if **any** parent is unchecked — an AND, despite the pipe | 3 |
| `A, B` | `last` | **assigns** rather than combines on each pass, so only the last parent has any effect | 2 |

The `last` rule is an upstream bug. It is recorded as measured rather than quietly
improved into the `all` a reader would expect: a renderer that hides a control
upstream shows is a behaviour change nobody asked for, and it would be invisible
in a diff of the generated file.

Chains are transitive but shallow: 57 keys are ungated, 133 are gated by an
ungated parent, and 11 have a parent that is itself gated. Maximum depth is 2 and
there are no cycles.

**Gating is presentational. Resolution never consults it.** `frame_worker.py`
reads `parameters[key]` unconditionally and gates behaviour by reading the parent
toggle separately (lines 73, 751, 757, 1280). So the gate tells a renderer what to
hide and nothing more; filtering values by gate state in the store would change
engine behaviour.

Two upstream quirks worth knowing, neither of which this schema reproduces:

- The evaluator tests `if parent_widget_name in parentToggles` — a **substring**
  match. 10 keys have a parent whose name is a substring of another key's name
  (`HairMakeupEnableToggle` inside `FaceParserHairMakeupEnableToggle`, and so on),
  so toggling the shorter one also drives widgets gated on the longer one.
- Gates are only ever evaluated against widgets in the **same group**. All 144
  gates happen to be intra-group today, so nothing is currently broken by it.

## The three tiers

| Tier | Keys | Table | Keyed by |
|---|---|---|---|
| global | 33 (`settings`) | `global_settings` | key |
| project | 168 (`common` 23 + `swapper` 103 + `face_editor` 42) | `project_settings` | (project, key) |
| face | any project key | `face_settings` | (project, face, key) |

Resolution order is **face → project → global → schema default**
(`visoswap/settings/store.py`). The chain is unambiguous because the two schema
tiers share **no keys** — measured across all four layout dicts, and pinned by
`tests/test_schema_generated.py::test_the_two_tiers_do_not_intersect`. A
project-tier key can therefore never carry a global override, and a write at the
wrong tier is refused rather than silently stored where nothing will read it.

Each tier stores **only overrides**. Absence of a row means "inherit"; there is no
sentinel value that also means it, so setting a value explicitly to the default is
a real override and survives a later change to that default.

**Every value column holds a JSON-encoded scalar**, not raw text. `20` is stored
as `20`, not `'20'`, so an `int` comes back an `int` with no second coercion step
at any read. That is the difference between this design and the string
round-tripping it replaces.

`visoswap/settings/db.py` declares all five tables (the three above plus
`project_faces` and `setting_presets`) as one SQL string, executed by Phase 4's
backend rather than hand-copied into it. Two things about that DDL are deliberate:
three tables carry a foreign key onto `projects(id)` which this module does not
create — SQLite resolves foreign keys at DML time, so the DDL applies cleanly
without it — and `apply_settings_schema` does not turn `PRAGMA foreign_keys` on,
because that is the connection owner's decision.

## The one dynamic option list

`DFMModelSelection` is the only key whose options are a directory listing.
Upstream's `get_dfm_models_data()` lists `DFM_MODELS_PATH` — the relative
`./model_assets/dfm_models` — and keeps files ending `.dfm` or `.onnx`.

The generator emits `null` for its `options` and `default` and records the two
upstream function names in `options_from` / `default_from`. The **loader** runs the
scan, at load time, so a schema frozen months ago does not go stale the moment a
model file is added or removed.

The directory is resolved in this order: the `models_dir` argument, then the
`MODELS_DIR` environment variable, then the repository-relative `model_assets/`
the vendored engine already uses. Never from a request field.

Two deliberate differences from upstream:

- The list is **sorted**. Upstream returns filesystem order, which is not stable
  across machines, so two identical installs would render the dropdown
  differently.
- A missing directory is **logged**, not swallowed. Upstream's serializer
  (`web_ui.py:648-664`) catches the exception and substitutes `[]`, which is why a
  missing models directory today produces an empty dropdown and no error anywhere.
  "The directory has no models" and "the directory does not exist" are different
  facts and only one is the user's to fix. The list is empty either way; only the
  second one warns.

The default is the first option, or `''` when there are none — upstream's
behaviour, and here `''` is a real value meaning "no DFM model selected", not a
failure marker.

> **Phase 4:** `visoswap.schema.dfm_models()` returns the whole
> `{filename: path}` mapping, which is the same listing that populates the engine
> context's DFM model metadata — the field Phase 1's context surface flagged as
> unpopulated and plan 02-02 assigned to the model bootstrap. Wire that mapping
> into the engine context. **Do not write a second scan.**

## Regenerating

The generator is the project's **one Qt-touching step**. Three of the four
`*_layout_data` modules reach PySide6 on import, so it runs only on an interpreter
that has Qt, only during development, and its output is committed. A user's
machine never has the layout dicts and must never need them. That is also why it
lives in `tools/` and not under `visoswap/`: `tests/conftest.py` imports every
`.py` under `visoswap/` with the seven Qt roots blocked, so a generator placed
there would fail the Phase 1 gate by construction.

```
"D:/Visomaster/dependencies/Python/python.exe" -B tools/generate_schema.py
```

Set `VISOMASTER_DIR` to point at a checkout other than `D:/Visomaster`. Use `-B`:
the checkout is read-only source material and must not accumulate `__pycache__`.

The generator **rewrites the file only when the widget content actually changes**,
so a regeneration on a different day or a different machine is a genuine no-op and
`git diff --exit-code -- visoswap/schema/schema.json` stays clean. The `generated`
date in the header therefore means "when this content last changed". That check is
the project's only guard against a frozen schema going stale unnoticed: drift shows
up as a reviewable diff rather than as behaviour.

`tools/generate_schema.py` imports `shape_of` and `coerce` from
`tools/dump_engine_settings.py` rather than restating them. Both files type the same
201 keys off the same widget shapes; `tests/test_schema_fixture_agreement.py` proves
the import is really shared, because a copied rule looks identical on the day it is
copied and diverges on the day upstream adds a shape.

## The six dropped side effects

Upstream attached an `exec_function` to six keys. The current serializer drops every
key starting with `exec_`, which is how they went missing without anyone noticing.
The schema names them so the gap is visible and typed; `exec_function_args` is `[]`
for all six and is not emitted.

| Key | Dropped handler | Plan 03-03 |
|---|---|---|
| `nThreadsSlider` | `control_actions.change_threads_number` | **handler** |
| `ProvidersPrioritySelection` | `control_actions.change_execution_provider` | **handler** |
| `VideoPlaybackCustomFpsToggle` | `control_actions.set_video_playback_fps` | **handler** |
| `ThemeSelection` | `control_actions.change_theme` | none — a stylesheet concern with no engine meaning |
| `ViewFaceMaskEnableToggle` | `layout_actions.fit_image_to_view_onchange` | none — a Qt view refit |
| `ViewFaceCompareEnableToggle` | `layout_actions.fit_image_to_view_onchange` | none — a Qt view refit |

All six are accounted for as of plan 03-03: three have a handler in
`visoswap/settings/handlers.py` and three are recorded there by name with a
reason. `tests/test_apply_handlers.py` walks this same set and fails if a key
carrying an `exec_function` is neither. See
[`settings-presets.md`](settings-presets.md) for what each handler does and
why two of the three deliberately diverge from the upstream callback.

## The face tier, and the identity behind its key

Plan 03-02 made the face tier real. Its key is not a name a human typed and not a
detection index: it is derived from the recognition embedding, which is what lets
it survive a reload. Close the project, reopen it, detect again, and the face that
comes back is not byte-identical to the one that went in — the crop moved, the
frame was a different frame — so anything keyed on exact bytes or on detection
order has already lost the association. The reference web UI keys this tier by the
target index and clears the whole mapping on project load (`webui2/app.js:102`,
`:258`, `:283`, `:748`, `:778`); there was no per-face persistence upstream to
lift, only a model to borrow.

`project_faces` stores, per project, the face key, the **name of the recognition
model** that produced the embedding, and the embedding itself as raw
little-endian float32 bytes. The model name is not decoration. Upstream chooses
the recognition model at the **global** tier (`frame_worker.py:139` reads
`RecognitionModelSelection` and `SimilarityTypeSelection` from `control`), and the
four models produce embeddings in unrelated spaces, so an identity is only
comparable against another embedding produced under the same model. Matching
filters by model in SQL, and a mismatch is a *miss*, not an error: a project whose
recognition model was changed has no stored identities under the new one, and that
is a normal state.

**The embeddings are biometric-derived data and they are written to disk.** They
are of media the single local user supplied, they live in that user's own project
database on that user's own machine, they are never logged and they never leave
the machine (T-03-13). Stated here so it is a known fact rather than a discovery.

### One threshold, deliberately shared

Identity matching uses the **project-tier similarity threshold key**, not a new
constant of its own. The same key, at the same tier, compared the same way that
`frame_worker.py:151`, `:230` and `:284` compare it. A user who loosens matching
for swapping therefore loosens it for settings identity too.

That coupling is a decision, not an omission. One notion of "same face" in the
application is better than two that can disagree — if they disagreed, the engine
would swap face A while the settings layer served face B's overrides, and every
symptom would point at the settings being wrong rather than at the metric.

The metric itself is `ModelsProcessor.findCosineDistance`, and it is not what its
name suggests: it returns `100 - (1 - cos) * 50`, a **similarity on a 0-to-100
scale** — identical vectors score 100, orthogonal 50, opposite 0 — compared with
`>=` against an integer threshold whose range is 1 to 100 and whose default is 60.
Reimplementing it as a 0-to-1 cosine and comparing against a 0-to-1 threshold
would appear to work and would match at a completely different tightness.
`visoswap/settings/faces.py` carries a pure-Python restatement so that importing
settings does not require torch, an injection point so Phase 4 can pass the
engine's own bound method in and delete the duplicate, and an agreement test that
runs the engine's unbound method on the engine interpreter and compares. That
test **fails rather than skips** when the engine interpreter is absent.

## Validation

`visoswap/settings/validate.py` has two entry points and they are deliberately not
one.

**Strict** (`validate`) is for everything that arrives from a caller — in Phase 4
an HTTP handler, in Phase 5 a control the user moved. It accepts a value only if
it already carries the type the schema declares, then checks bounds, option
membership and text length. A string offered for a number is a rejection, not an
input to be repaired.

**Lenient** (`coerce`) exists for **migration** and has exactly one intended
caller in the project: plan 03-03's preset seeding. It applies the same
shape-derived conversion the schema generator applies and then runs the strict
validator on the result. A test scans `visoswap/` and `backend/` and fails if
anything else calls it, because a second caller is not a style problem — it is the
string round-tripping this phase exists to end, re-established one convenience at
a time.

Every rule is read from the schema. No settings key name appears as a literal in
the validator, and a test pins that: a second hardcoded rule there is the type
moving back out of the data.

Three decisions worth stating, because each is invisible in the code that
implements it:

- **Bools are tested before ints, everywhere.** `isinstance(True, int)` is True in
  Python, so without the ordering every toggle passes as a slider position and
  every `1` passes as a toggle.
- **Ints are widened to float for float keys, one-way.** Reject `1` for a float
  key and the frontend has to send `1.0` for a slider sitting at one, which no
  renderer does. The reverse is refused: `2.5` for an integral key is a caller who
  has not decided what they mean.
- **An off-step value is accepted.** The `step` is a renderer's increment hint,
  not a constraint, and upstream never enforced it — `StrengthAmountSlider` steps
  by 25 over 0 to 500, and three makeup sliders step by 3 over 0 to 255, which
  does not even reach the maximum. Rejecting off-step values would make
  legitimate stored values unwritable.

### Both directions, and one asymmetry

Every write in `store.py` validates — global, project and face alike. Every read
validates too, because a row written by a migration, by an older build or by hand
has never passed the write-side gate, and a wrong value on disk is worse than one
in flight because it persists. A read-side failure raises `CorruptStoredSetting`,
which names the **tier** the offending row sits in; the value itself says nothing
about where it came from, and the tier is the whole diagnosis.

The one asymmetry: for the single key whose options are a directory listing,
membership is enforced on the way **in** and not on the way out. Deleting a model
file is not corruption, and since resolution reads the whole tier at once,
treating it as corruption would take all 201 settings down with one removed file.
"The file this names is gone" and "this value was never allowed" are different
facts, and only the second one belongs to the settings layer.

## Gates are evaluated for display, and for nothing else

`visoswap/settings/gates.py` answers whether a renderer should draw a control.
**Resolution does not call it and must never call it**, and that is asserted by
AST rather than only written down here.

A key whose gate is closed still resolves to a value. The engine reads
`parameters[key]` unconditionally — there is no branch in the swap loop asking
whether a control was on screen — and it reads the parent toggle separately, as
its own key. Filtering resolved values by visibility would not tidy the output; it
would remove keys the engine then raises a `KeyError` on, several hundred frames
after the control that caused it went off screen.

The evaluator handles both mechanisms, both compound spellings (`all` for the pipe
that reads like OR, `last` for the comma that discards every parent but the final
one), and transitive chains. It raises `GateCycle` rather than recursing on a
loop; the measured data has 57 ungated keys, 133 at depth 1, 11 at depth 2 and no
cycles, so the raise is a change detector rather than a routine path.

One deliberate divergence from upstream: a key is visible only when every parent
that *decides* its gate is itself visible. Upstream evaluates a widget against its
immediate parents' values and never asks whether those parents are on screen, so a
control nested under a switched-off section can still be drawn. "Every parent that
decides" is meant precisely — under the `last` rule the discarded parents are
discarded from the visibility chain too, so one notion of which parents matter
answers both questions.

## Whole-tier resolution

`store.resolve_parameters` returns the project tier as one flat mapping of all 168
keys; `store.resolve_control` returns the global tier as all 33. **Every key is
present, always.** `resolve_parameters` is literally the value that goes into the
engine context's per-face parameters mapping under that face's own key —
`frame_worker.py:147` reads `self.parameters[target_face.face_id]` and three
further sites index it the same way — so a sparse result is not a graceful
fallback to a default. It is a `KeyError` inside the swap loop.
