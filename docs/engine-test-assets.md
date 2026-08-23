# Engine test assets

Where every asset the Phase 2 engine tests depend on comes from, and what breaks
if it is wrong. Three assets, three mechanisms: a link for the weights, a
checked-in JSON file for the settings, a sealed subprocess for the runtime.

---

## 1. The weight set: now env-driven, project-owned copy

`visoswap/models/models_data.py` line 6 (`models_dir`) is env-driven since plan
04-04: it reads `MODELS_DIR` with a project-owned default.

```python
models_dir = os.environ.get('MODELS_DIR', './model_assets_owned')
```

That one line is the **single edit site** that Phase 1 marked and plan 04-04
changed. Every asset path in the engine is built from `models_dir` by f-string, so
all 62 tracked paths follow the change for free. `model_assets_owned/` is the
project's own verified copy of the weights (gitignored, made by
`tools/copy_model_assets.py`), replacing the `model_assets` junction into the
read-only VisoMaster tree as the default. The vendored file now has **two** named
edit sites: the env-driven `models_dir` (plan 04-04) and the provenance note it
carries (plan 01-04); the file still carries its attribution header and its diff
against upstream is confined to those sites. See `docs/no-visomaster-install.md`.

Two consequences, both load-bearing rather than incidental:

- **The working directory of whatever runs engine code is part of the
  contract.** A relative `./model_assets_owned` resolves against the process CWD.
  Run engine code from anywhere but the repository root and the engine looks for
  weights somewhere else entirely — and, worse, `ModelsProcessor` defaults to
  TensorRT, whose execution-provider options write a cache into a *relative*
  `tensorrt-engines/` directory. From the wrong CWD that writes into the
  read-only source tree. Always run from the repository root.
- **The directory must resolve when `ModelsProcessor` is *constructed*, not when
  a frame is swapped.** `ModelsProcessor.__init__` builds all seven
  sub-processors, and `FaceEditors.__init__` opens
  `liveportrait_onnx/lip_array.pkl` inside a `try`/`except FileNotFoundError`
  that leaves `lp_lip_array` as `None`. A missing models directory therefore does
  not raise at construction — it silently produces a degraded processor. That
  silent `None` is the failure mode of getting this wrong.

The `model_assets` junction still exists and still resolves into `D:/Visomaster`.
It is deliberately left in place (see `docs/no-visomaster-install.md`); the
project simply no longer *defaults* to it.

`tools/copy_model_assets.py` makes the project-owned copy and verifies it against
the manifest. `tools/link_model_assets.py` still exists for the junction and for
a developer who explicitly wants the borrowed tree.

### Source resolution

| Order | Source |
|-------|--------|
| 1 | `VISOSWAP_MODEL_ASSETS_SOURCE` environment variable |
| 2 | `D:/Visomaster/model_assets` |

The resolved source is printed on every run, before anything can fail, so a link
pointed at the wrong tree is diagnosable from the log alone.

### Usage

```
python tools/link_model_assets.py           # create the link
python tools/link_model_assets.py --check   # verify link + probe set
```

On Windows the link is a **junction** (`mklink /J`), not a symbolic link:
junctions require no administrator rights and no developer-mode opt-in. On POSIX
it is `os.symlink`. The mechanism is chosen from `os.name`, never guessed from
the shape of a path string.

Creating over an existing link that already points at the resolved source is a
no-op success. Creating over an existing **real directory** refuses and exits
non-zero — overwriting a real directory of weights is the one unrecoverable
mistake this script can make, so it never deletes, never overwrites and never
merges.

### The link is read-only

It points into `D:/Visomaster`, which is **read-only source material** for this
project. Anything written through the link lands in that tree. Nothing in Phase 2
writes through it; plan 02-02 pins the ONNX Runtime provider to CUDA specifically
so the TensorRT engine cache never materialises next to the weights.

`model_assets/` and `tensorrt-engines/` are already in `.gitignore`, so the link
is never committed and the cache directory can never be either.

### The probe set

`--check` verifies these nine files are readable *through the link* — opened and
read, not merely `stat`ed, because a junction into a tree with a denied ACL lists
fine and reads nothing. They are named explicitly rather than globbed so a later
phase can widen or shrink the set deliberately and see it in a diff.

| File | Read by |
|------|---------|
| `det_10g.onnx` | RetinaFace detector |
| `w600k_r50.onnx` | Inswapper128ArcFace recogniser |
| `inswapper_128.fp16.onnx` | the swapper itself |
| `occluder.onnx` | occlusion mask |
| `XSeg_model.onnx` | XSeg mask |
| `faceparser_resnet34.onnx` | face parser |
| `meanshape_68.pkl` | 3d68 landmark mean shape |
| `liveportrait_onnx/lip_array.pkl` | `FaceEditors.__init__` (the silent-`None` file above) |
| `liveportrait_onnx/motion_extractor.onnx` | LivePortrait entry model |

**`rd64-uni-refined.pth` is deliberately excluded.** CLIPseg's mask path loads
it, it exists nowhere under `D:/Visomaster`, and it is absent from the 56-entry
model manifest — upstream VisoMaster's text-masking control is non-functional on
this install too, so this is not something the vendoring broke. Plan 02-04 owns
that gap; see `.planning/phases/02-engine-api-first-swap/02-DECISION-deferred-paths.md`.
A check that fails from day one teaches everyone to ignore the check.

---

## 2. The settings pair: generated once, checked in

> **This is a test fixture, not a schema.** It lives under `tests/`, it is
> disposable, and Phase 3's generated `visoswap/schema/schema.json` replaces it
> outright. Nothing outside the test suite may read it, and `visoswap/schema/`
> is deliberately **not** created in Phase 2.

`tests/fixtures/engine_settings.json` is the only settings source the sealed
runner is allowed to read. It has exactly two top-level members, `project` (168
keys) and `global` (33 keys), with zero overlap.

### Why it is generated offline rather than imported

Of the four `*_layout_data` modules, **only `face_editor_layout_data` imports
clean** with the seven Qt roots blocked; `common_layout_data`,
`swapper_layout_data` and `settings_layout_data` all reach PySide6. So no Qt-free
test can read the layout dicts. They are read once, here, on an interpreter that
has Qt, and the result is committed.

```
"D:/Visomaster/dependencies/Python/python.exe" -B tools/dump_engine_settings.py
```

The VisoMaster checkout resolves from `VISOMASTER_DIR`, defaulting to
`D:/Visomaster`. `QT_QPA_PLATFORM` is set to `offscreen` before the imports so
`settings_layout_data` cannot try to reach a display. Output is written with
sorted keys and a trailing newline, so a regeneration after an upstream edit
diffs as the one value that moved rather than as a whole-file reordering.

### Why typing is the point

The layout dicts' `default` values are **strings**. `ClipAmountSlider` defaults
to `'50'`, `SimilarityThresholdSlider` to `'60'`,
`FaceEditorCropScaleDecimalSlider` to `'2.50'`. `profiles.json` is no better: 168
parameters, 138 of them string values. Qt widgets accept strings and coerce
internally, so nothing upstream ever had to care.

The engine does care. Hand it `'50'` and it fails deep inside a tensor operation
with a `TypeError`, a long way from the load that caused it. So the design's
phrase *"typed default where the widget has one"* means **derived from widget
shape**, not read from `default` — reading the type of `default` would just
return `str` 138 times.

### The shape rule

| Shape | Keys | Type | Coercion |
|-------|------|------|----------|
| `decimals` + `min_value`/`max_value`/`step` | 42 | float | `float(default)` |
| `min_value`/`max_value`/`step`, no `decimals` | 93 | int | `int(float(default))` |
| `min_value`/`max_value`, **no** `step` | 1 | str | `str(default)` |
| an `options` list | 22 | str | `str(default)` |
| none of the above | 43 | bool | already a `bool` |

`int` goes through `float()` first because `int('2.50')` raises and upstream
writes decimal strings into otherwise integral keys.

**The third row is a correction to the plan's measured facts.** Planning recorded
four shapes and 94 int keys. There are five, and 93. The extra key is `ClipText`,
which carries `min_value: '0'` and `max_value: '1000'` but no `step` and no
`decimals`: those bounds count *characters in a line edit*, not slider positions,
and its default is `''`. Typing it as an int the way a "min/max without decimals"
rule would is not merely wrong — `int(float(''))` raises, so the generator dies
rather than misleads. `step` is the signal that separates a slider from a text
box.

### `generated_from` note: keys that do not come from the dicts as written

**`DFMModelSelection` resolves to the empty string.** It is the one key whose
`default` *and* `options` are callables — `get_dfm_models_default_value` and
`get_dfm_models_selection_values` in `app/helpers/miscellaneous.py`, deliberately
not vendored. They scan a models directory the engine does not own, so resolving
them here would bake this machine's (currently empty) `dfm_models/` listing into
a checked-in file. The key is recorded as empty rather than dropped: **absent and
empty are different facts**, and plan 02-04 needs to know this one starts empty.
The generator prints every callable default it resolved, so this list cannot
grow silently.

### Four numeric-looking values that must stay strings

`SwapperResSelection` (`'128'`), `LandmarkDetectModelSelection` (`'203'`),
`WebcamMaxNoSelection` (`'1'`) and `WebCamMaxFPSSelection` (`'30'`) are genuine
dropdowns whose `options` lists hold strings. The engine compares the value
against that list, so coercing them to ints would silently stop the comparison
matching. `tests/test_engine_settings_fixture.py` names them as the *only*
permitted string-that-parses-as-a-number, which is what keeps the "no slider
leaked through as a string" check sharp instead of blanket.

---

## 3. The runtime: a sealed subprocess

`tests/_engine_runner.py` is the only thing in this project that executes engine
code. It runs as a subprocess and is **never imported by pytest**, the same shape
as `tests/_qt_guard_probe.py` and for the same reason: the interpreter that runs
pytest has no torch, and the interpreter that has torch also has Qt. Neither can
both drive a test and execute the engine, so the two live on opposite sides of a
process boundary.

Before anything else is imported, a `sys.meta_path` finder is installed at index
0 refusing three groups of roots:

| Group | Roots | Why |
|-------|-------|-----|
| `qt` | the seven Qt bindings | Phase 1's result, enforced at runtime |
| `visomaster` | `app` | a surviving `app.*` import means the module only works inside the source tree |
| `backend` | `backend` | the engine is a library and must not know the web layer exists |

Blocking the second and third is what turns the roadmap's Phase 2 criterion 3
from a text search into a **runtime proof**. A grep says no line imports
`backend`. The seal says the engine *cannot* reach it — and the two fail
differently, so both are kept: a grep misses an `importlib` call built from a
string, and a runtime seal misses a line nothing executes.

### One block list, not three

The roots are defined once in `tests/_blocked_roots.py`, a module that imports
nothing. `conftest.py` re-exports them, `_qt_guard_probe.py` reads `QT_ROOTS` from
them, `_engine_runner.py` reads all three groups. Two copies of a block list is
how two gates silently diverge — one grows a root, the other keeps passing, and
the weaker gate is the one everybody reads.

The definitions do not live in `conftest.py` itself because the probe and the
runner execute on the *engine* interpreter, which carries torch, onnxruntime and
PySide6 but **no pytest** — measured on both
`D:/Visomaster/dependencies/Python/python.exe` and `.venv-clean`. Neither can
import a module that imports pytest at module scope.

### Not inert

A blocker is inert wherever the thing it blocks is absent, and an inert blocker
reports exactly the same `CLEAN` as a working one. That is the lesson plan 01-04
paid for. So the runner puts a package named `app` on `sys.path` **before**
arming — a self-built subject by default, or a real checkout via `VISOMASTER_DIR`
— measures which sealed roots would genuinely have resolved, and reports it:

```
reachable_before_seal=PyQt5=no,PyQt6=no,PySide2=no,PySide6=yes,app=yes,backend=no,...
```

`PySide6=yes` and `app=yes` mean those two seals refused packages that really
were there. `backend=no` is honest: the package does not exist until Phase 5, so
that seal is armed and provably fires on the name, but cannot yet be shown
non-inert. The self-test provokes each group *separately* — concluding "the seal
is armed" from one group firing is how the other two end up unenforced.

### Engine interpreter default (plan 04-04)

The default engine interpreter is now the project's own `.venv-clean/Scripts/python.exe`
(a combined runtime with the inference stack and no PySide6), so the default run
proves ENGINE-01 clause 1 — the engine runs where Qt is genuinely absent. The
seal property (a blocker fired against a PySide6 that *is* installed) remains
provable by pointing the override at a Qt-carrying interpreter:

```
VISOSWAP_ENGINE_PYTHON="D:/Visomaster/dependencies/Python/python.exe" \
  .venv-clean/Scripts/python.exe -m pytest tests/test_engine_seal.py -q
```

Nothing skips when that Qt-carrying interpreter is absent: the seal test's
non-inertness proof builds its own `app` subject, and the `VISOMASTER_DIR`
override is only the sharper run, not a requirement.

### Exit codes

| Code | Label | Meaning |
|------|-------|---------|
| 0 | `CLEAN` | the mode completed and no sealed root was reached |
| 1 | `SEAL_BREACHED` | a sealed root was reached, or leaked into `sys.modules` |
| 2 | `DEPS_MISSING` | an engine dependency is not installed |
| 3 | `ASSET_MISSING` | a required asset or fixture is not on disk |
| 4 | `ENGINE_ERROR` | anything else |

The vocabulary exists so a **harness** fault can never be read as an engine pass.
Every one of the five is exercised by `tests/test_engine_seal.py` or by hand;
`ASSET_MISSING` is reachable in a test because the fixture path is overridable
via `VISOSWAP_SETTINGS_FIXTURE`. An exit code nobody has watched fire is an exit
code nobody knows the meaning of.

Exactly one machine-readable line is printed before exit, `LABEL:mode:detail`, so
pytest attributes a failure without parsing a traceback.

### Modes

```
python tests/_engine_runner.py --selftest              # arm, provoke all three groups
python tests/_engine_runner.py --import PySide6.QtCore # show it capable of exit 1
python tests/_engine_runner.py --smoke                 # one real swap, see section 4
```

`--selftest` and `--import` prove only that the harness is armed and capable of
failing — which is what has to be true before any engine failure can be believed.
**A failure in the runner is a harness failure; a failure after it is an engine
failure. Keeping those separable is the point.**

Run it from the repository root. Always. See section 1.

---

## 4. The media pair: two people, deliberately

Added by plan 02-02, which drives the first real swap. `--smoke` loads a video,
detects faces, swaps one frame and writes two PNGs to `tests/artifacts/`.

### The two files

| Role | Default path | Override |
|------|--------------|----------|
| Target video | `tests/media/17f0d620_rosh_generate_135bda4c9686.mp4` | `VISOSWAP_TEST_VIDEO` |
| Source face | `tests/media/598004cb_tonima.JPG` | `VISOSWAP_TEST_SOURCE` |

Both are personal media and **neither is committed**. The video is 1920x1080,
24 frames, 23.976 fps — the smallest real face-bearing clip on the machine, and
small enough that a whole swap including both model loads measures around eight
seconds. They live in `tests/media/` (gitignored, plan 04-04) rather than
`D:/Visomaster`, so the smoke fixtures no longer default into a borrowed tree.

**They are two different people, and that is the point.** The video already
contains the face in `17f0d620_rosh.jpg`. Using that image as the source would
swap a face onto itself: the swap would run, the pipeline would report success,
and the "did any pixel change" assertion would be testing nothing while
appearing to pass. A source that is a *different* person is what makes a
non-zero pixel diff mean the swap happened.

### A missing file fails; it never skips

If either media file, the settings fixture, or the `model_assets` link is
absent, `--smoke` exits `ASSET_MISSING` (3) and `tests/test_engine_smoke.py`
fails. There is no skip anywhere in that path, by design.

Phase 1 established that a missing dependency must never read as a pass. That
rule now covers media as well as packages, and for a sharper reason here: a
skipped swap test is a phase that looks finished and has swapped nothing. All
four exit-3 paths were provoked by hand rather than assumed.

The `model_assets` check is a *directory* check done before any model loads,
because `FaceEditors.__init__` swallows a missing `lip_array.pkl` and leaves
`lp_lip_array` as `None` — see section 1. Without the early check, a missing
weight set produces a degraded processor and a confusing inference failure
instead of "the link is not there".

### The smoke overrides

The run is pinned to a known configuration rather than inheriting whatever the
fixture currently holds. Most overrides restate the fixture's own value; that
is deliberate, so that a regenerated fixture moving a default shows up as a
diff on this list rather than as a differently-behaving swap.

| Tier | Key | Value |
|------|-----|-------|
| global | `ProvidersPrioritySelection` | `CUDA` |
| global | `DetectorModelSelection` | `RetinaFace` |
| global | `RecognitionModelSelection` | `Inswapper128ArcFace` |
| global | `SimilarityTypeSelection` | `Opal` |
| global | `DetectorScoreSlider` | `50` |
| global | `MaxFacesToDetectSlider` | `20` |
| global | `LandmarkDetectToggle` | `False` |
| global | `EmbMergeMethodSelection` | `Mean` |
| global | `nThreadsSlider` | `1` |
| project | `SwapModelSelection` | `Inswapper128` |
| project | `SwapperResSelection` | `'128'` |
| project | `SimilarityThresholdSlider` | `60` |
| project | `ClipEnableToggle` | `False` |
| project | `FaceEditorEnableToggle` | `False` |

Every key is asserted to exist in the tier **before** it is assigned. An
override that introduces a key is a typo, and a typo written in blind either
raises deep inside a tensor operation many calls later or, worse, silently does
nothing. The membership check is made twice on purpose: once by the runner at
its own boundary, which is what protects a caller, and once statically in
`tests/test_engine_smoke.py`, which needs no GPU, no weights and no media and
so fails in milliseconds on any machine.

**Two of the plan's key names were wrong and this is how it was found.** The
plan's override list said `ThreadsSlider` and `TextMaskingEnableToggle`. The
fixture — and upstream's settings layout — call them `nThreadsSlider` and
`ClipEnableToggle`. The static check was run against the wrong names on purpose
to confirm it reports them rather than passing quietly.

### The evidence, and why it is gitignored

`--smoke` writes `tests/artifacts/smoke_source_frame.png` (the decoded frame)
and `tests/artifacts/smoke_swapped_frame.png` (the same frame after the swap).
They exist so a human can look at them: the automated test can only prove that
*some* pixels changed, and a garbled face, a whole-frame alteration and a face
pasted back onto itself would each satisfy that.

`tests/artifacts/` is in `.gitignore`, added in the same change that created
the directory. These are decoded frames of personal media and swapped faces
made from them; committing one is an information-disclosure bug, not an untidy
repository. Nothing about the images is logged either — the run's one output
line carries counts and shapes only.

### What the run reports

```
CLEAN:smoke:faces=2 frames=24 fps=23.976 provider=CUDA input_shape=1080x1920x3
output_shape=1080x1920x3 diff_pixels=110932 elapsed=7.0s
artifacts=smoke_source_frame.png+smoke_swapped_frame.png reachable_before_seal=...
```

`provider=CUDA` is read back off the models processor rather than echoed from
the settings, so it says what the engine actually selected. `diff_pixels`
counts pixels, not channel values, so a pixel counts once however many of its
channels moved.
