# Presets, apply-handlers, and the reassignment that was removed

`visoswap/settings/data/presets_seed.json` holds two presets, migrated once from
VisoMaster's `profiles.json` and committed. `visoswap/settings/presets.py` seeds,
lists and applies them. `visoswap/settings/handlers.py` holds the side effects
that used to live in Qt callbacks. This document is the companion to
[`settings-schema.md`](settings-schema.md), which covers the schema itself, the
three tiers, validation and gating.

## The one user-visible change

**Both presets now swap at resolution 128 rather than at 256.**

The old web UI reassigned `SwapModelSelection` and `SwapperResSelection` on every
load, forcing the resolution to `"256"`. Both saved profiles store `"128"`. With
the reassignment gone, what the profile stored is what resolves. A user who wants
256 sets it and it persists — which is what the control was for.

## The two presets

| Id | Name | Created (UTC) | Project keys | Global keys |
|---|---|---|---|---|
| `6a24b3aa71d7` | `A` | 2026-06-14, last touched 2026-07-12 | 168 | 33 |
| `55fc87c18307` | `with AUD` | 2026-06-16, last touched 2026-07-13 | 168 | 33 |

They come from `profiles.json` in the VisoMaster checkout — a GPLv3 tree — so the
seed carries the same licence note `schema.json` carries, for the same reason.
Neither is a `.py` file, so no attribution gate walks them; the note in the file
header is how the obligation is met.

Each source profile carries an `options` object of exactly 33 keys and a
`parameters` object of exactly 168. Those counts match the schema's global and
project tiers **exactly**, with nothing left over on either side — which is an
independent corroboration of the tier split, measured from a file the schema
generator never reads.

### What the two presets actually override

Almost nothing, and that is worth knowing before reading anything into them:

| Preset | Tier | Key | Preset | Default |
|---|---|---|---|---|
| both | global | `AudioMuxingToggle` | `False` | `True` |
| `with AUD` | project | `SimilarityThresholdSlider` | `20` | `60` |
| `with AUD` | project | `StrengthEnableToggle` | `True` | `False` |
| `with AUD` | project | `StrengthAmountSlider` | `150` | `100` |

Preset `A` disagrees with the schema defaults about **one** setting. Applying it
writes one global row and zero project rows. That is not a bug in the migration —
it is what an override model looks like when the person who saved the profile
changed one thing.

## The typing rule

Every value is typed **by shape, through the schema** — never by looking at what
type it already is. `tools/migrate_profiles.py` puts each value through
`validate.coerce`, which applies the schema generator's own shape rule and then
runs the strict validator on the result. It is the one intended caller of that
lenient path, and `tests/test_settings_validation.py` fails if a second one
appears.

The reason it cannot be simpler: **the source file types the same key differently
in its own two profiles.**

| Key | In `A` | In `with AUD` | Schema | In both presets |
|---|---|---|---|---|
| `SimilarityThresholdSlider` | `"60"` (str) | `20` (int) | `int` | `int` |
| `StrengthAmountSlider` | `"100"` (str) | `150` (int) | `int` | `int` |

Measured across both profiles: `options` is 20 str + 13 bool in each;
`parameters` is 138 str + 30 bool in `A` and 136 str + 30 bool + 2 int in
`with AUD`. Toggles are already real bools upstream. A migration written to the
common belief that "every value in `profiles.json` is a string" would have failed
on 43 toggles at the first profile.

The migration reports three classes of disagreement rather than absorbing them —
a key the source has that the schema does not, a key the schema has that the
source does not, and a value that fails strict validation after conversion — and
refuses to write a seed if any is non-zero. The measured result is **zero of each
across all 402 values**, and the counts are printed either way so a clean run
says so out loud.

### The one key migrated against an empty models directory

`DFMModelSelection`'s option list is a directory scan, so whether `''` — which is
what both profiles store, and is upstream's own "no DFM model chosen" — is a
legal option depends on which files sit on the migrating machine. Letting that
decide the committed bytes would make the seed a function of one developer's
disk. It is migrated against a deliberately empty directory: the seed records
what the profiles stored, and whether a named model exists is a question for the
machine applying the preset.

At apply time, a value for that key that is not currently an option is **skipped
and named in the report**, and any stale override for it is cleared. That is the
same distinction `store.validate_stored` already draws — "the file this names is
not here" and "this was never allowed" are different facts, and only the second
is corruption.

## Applying a preset writes overrides only

Applying writes **only the keys whose value differs from the schema default**, at
the project tier for the 168 and the global tier for the 33, and **clears** any
existing override for a key where the preset agrees with the default.

Writing all 201 would work and would be wrong. Each tier means "the keys this
level disagrees about"; fill it with a complete copy and inheritance stops
meaning anything, because every value is then pinned at the most specific level
that has ever been touched.

**The consequence, stated rather than discovered: a later change to a schema
default propagates into a project that took a preset**, for exactly the keys that
project never disagreed about. That is what an override model is for. If you need
a value pinned against a future default change, set it explicitly; agreeing with
the default is not the same as choosing it.

Every write goes through `visoswap/settings/store.py`, so preset application is
validated by the same rules, held to the same tier check and encoded the same way
as any other write. A private `INSERT` here would be a side door around all
three; `tests/test_presets_seed.py` asserts against the source of `presets.py`
that none exists.

## The three apply-handlers

Six keys carried an `exec_function` upstream and the serializer dropped all six.
Three now have an explicit handler in `visoswap/settings/handlers.py`. Each takes
its target as an argument, imports nothing from the inference stack, and returns
the settings updates it causes — usually none.

| Key | Upstream callback | What the handler does |
|---|---|---|
| `ProvidersPrioritySelection` | `change_execution_provider` | `switch_providers_priority(value)` then `clear_gpu_memory()`, in that order |
| `nThreadsSlider` | `change_threads_number` | `models_processor.set_number_of_threads(int(value))` |
| `VideoPlaybackCustomFpsToggle` | `set_video_playback_fps` | returns `{VideoPlaybackCustomFpsSlider: round(media.fps)}`, or nothing |

Three things about these that a reader will otherwise get wrong:

**The thread handler is not a faithful port, on purpose.** Upstream's callback
sets the thread count on `video_processor` — the module Phase 1 dropped whole —
so porting it faithfully produces a function that does nothing while looking
exactly right. The call that reaches the engine is the models processor's, which
is what `visomaster_headless.py` uses.

**The provider handler deliberately omits upstream's first call.** Upstream stops
the video processor before switching. That module does not exist here and a stub
standing in for it would be a call that looks like it does something. The Qt
progress-bar update is likewise gone.

**The frame-rate handler goes the opposite way to its name.** `set_video_playback_fps`
does not apply the slider to playback; it writes the loaded clip's own frame rate
*into* the slider, and only when the toggle is on and media is loaded. That
direction is reproduced exactly. Because the schema types that slider as an
`int` with a step of 1 and a range of 1–120, **29.97 fps is stored as 30 and a
240 fps clip is stored at 120**. Halves round up: Python's own `round` sends 30.5
to 30, which is not a rule anyone expects of a frame rate. The precision loss is
a consequence of the data model, not a bug in the handler.

`handlers.effective_playback_fps` answers "what rate should playback actually run
at" in one place — the slider when the toggle is on, the clip's own rate when it
is off. Phase 4's scheduler needs that answer and so does the player; deriving it
twice is how the two end up disagreeing about what time it is.

### The three that deliberately have none

Recorded in `handlers.DELIBERATELY_UNHANDLED`, by name, with a reason each. A
silent absence and a deliberate decision look identical in a diff a year from
now.

| Key | Why nothing is needed |
|---|---|
| `ViewFaceMaskEnableToggle` | Still fully functional as a setting — `processors/workers/frame_worker.py` reads it directly at lines 73, 751, 757 and 1280. Its callback only refit a Qt image view. |
| `ViewFaceCompareEnableToggle` | Same: read directly by the frame worker, callback was a Qt view refit. |
| `ThemeSelection` | Loads a Qt stylesheet onto the `QApplication`. There is none here; theming belongs to the web frontend. |

`tests/test_apply_handlers.py` walks the schema for every key carrying a dropped
side-effect name and fails if one is neither handled nor recorded, so a future
upstream `exec_function` cannot land unnoticed the way these six did.

## The removed reassignment, and the gate that keeps it removed

`web_ui.py` carries the pair at five sites: `:232-233` in its built-in defaults,
`:252` and `:258` in a hand-written schema stub, `:372-373` in the defaults merge
and `:1199-1200` on the generation path. The two on the load path assign
`SwapModelSelection = "Inswapper128"` and `SwapperResSelection = "256"`.

They are wrong in two independent ways:

1. **Wrong tier.** Both assignments write into the `options` dict — the *global*
   tier — while both keys are **project**-tier keys that live in `parameters`.
   The values were being written where nothing reads them as settings for a
   face: the "appears to save, does not apply" failure this phase exists to
   remove, in the code that inspired it.
2. **Wrong value.** Both saved profiles store `"128"`. The reassignment forced
   `"256"` on every load, silently contradicting the user's own saved choice.

None of the five sites is ported. `tests/test_no_swap_model_overrides.py` parses
every Python file under `visoswap/`, `tools/` and — when Phases 4 and 5 create
them — `backend/` and `frontend/src/`, and fails on any **assignment** to either
key. It parses rather than greps because both names appear legitimately as data
in the committed schema, in the seed and in this document; naming a key stays
allowed, assigning to one does not. The `tests/` directory is excluded, because
plan 02-02's engine smoke test deliberately pins a swapper model and resolution
as a fixture so its non-zero-pixel assertion tests a real configuration.

The gate is proven in both directions — a planted assignment that must be
flagged, a mention in a comment and a string that must not — and it reports the
number of files it examined, which the test asserts is non-zero. A scanner
pointed at an empty file set passes perfectly and proves nothing, and this gate's
whole job is to run silently in phases nobody has written yet.

## Regenerating the seed

Needs no Qt, unlike the schema generator — the source is plain JSON and the
schema is already committed:

```
python tools/migrate_profiles.py
VISOMASTER_PROFILES=/path/to/profiles.json python tools/migrate_profiles.py
```

It writes only when the presets content actually changes, so the header's date
does not make regeneration produce a diff every day. A clean re-run must leave
`git diff --exit-code -- visoswap/settings/data/presets_seed.json` clean.

The output is committed and shipped as package data. `profiles.json` exists on
exactly one developer's disk; a user machine must never need it. The repository's
`.gitignore` anchors its `data/` rule to the repository root (`/data/`) for
exactly this reason — unanchored, it also swallowed the committed seed.
