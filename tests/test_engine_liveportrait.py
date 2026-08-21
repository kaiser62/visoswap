"""LivePortrait's first execution: a swapped face, then edited, then compared.

Roadmap criterion 4 as amended by ``02-DECISION-deferred-paths.md``. The original
criterion named three paths -- DFM, LivePortrait and CLIPseg -- that Phase 1
vendored and never ran. The owner descoped two of them: ``rd64-uni-refined.pth``
and any ``.dfm`` file are absent from this machine and from VisoMaster's own
model manifest, so exercising either would have meant sourcing ~1.1GB of external
weights to test a feature nothing uses. LivePortrait is the third, its eight ONNX
files are present in full, and this is where it runs.

**Two gates, deliberately of different kinds.** ``FaceEditorEnableToggle`` is an
ordinary project-tier settings key. ``EngineContext.edit_faces_enabled`` is not a
setting at all -- it is one of the two plain fields that replaced a Qt toggle
button in Phase 1, read at three sites in ``frame_worker``. A run that opens both
is what proves that substitution was faithful rather than merely type-correct,
which is the thing a type annotation cannot say.

**The failure modes are asserted separately.** Criterion 4 distinguishes a Qt
import error from a missing-attribute error, and a single ``exit == 0`` cannot
say which happened. The runner's exit vocabulary from plan 02-01 already
separates them -- 1 seal, 2 dependency, 3 asset, 4 engine -- so each gets its own
test and its own message.

**Nothing here skips.** A missing lip array, a missing model link and an
unregenerable baseline are all ``ASSET_MISSING`` and all failures. A skipped
editor test is a criterion that looks met and has edited nothing.
"""

import ast
from pathlib import Path

import pytest

from tests.conftest import run_engine_runner

TESTS_DIR = Path(__file__).resolve().parent
RUNNER_SOURCE = TESTS_DIR / "_engine_runner.py"
SETTINGS_FIXTURE = TESTS_DIR / "fixtures" / "engine_settings.json"
ARTIFACTS_DIR = TESTS_DIR / "artifacts"
SWAP_ONLY_FRAME = ARTIFACTS_DIR / "smoke_swapped_frame.png"
EDITED_FRAME = ARTIFACTS_DIR / "liveportrait_frame.png"

#: A ceiling on the whole run, in seconds (T-02-08).
#:
#: Higher than the smoke run's because this one loads LivePortrait's motion
#: extractor (107MB), appearance-feature extractor, stitching heads and the
#: 402MB warping-spade decoder **and** shells out to a full smoke run to rebuild
#: its baseline. Same rule as the smoke ceiling: this bounds *unbounded*, not
#: performance, and tightening it into a benchmark would only produce a flaky
#: test on a busy GPU. A first CUDA run on a cold machine was measured at over
#: 300s for the swap alone while the driver compiled kernels.
MAX_ELAPSED_SECONDS = 900.0


def _module() -> ast.Module:
    return ast.parse(RUNNER_SOURCE.read_text(encoding="utf-8"), str(RUNNER_SOURCE))


def _string_constants(module: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "..."`` assignments, by name.

    Needed because the face-editor override dict keys one of its entries off
    ``FACE_EDIT_CONTROL_KEY`` rather than repeating the literal -- the runner
    prints that constant in its report line, and a key that is printed and a key
    that is applied must be the same object, not two strings that agree today.
    """
    constants = {}
    for node in module.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
    return constants


def _dict_literal_keys(node: ast.Dict, constants: dict[str, str]) -> set[str]:
    keys = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        elif isinstance(key, ast.Name) and key.id in constants:
            keys.add(constants[key.id])
        else:
            raise AssertionError(
                "override key {} is neither a string literal nor a module-level "
                "string constant, so this check cannot see it".format(ast.dump(key))
            )
    return keys


def _face_edit_override_keys() -> set[str]:
    """Every project-tier key the face-editor run applies, read statically.

    ``FACE_EDIT_PROJECT_OVERRIDES`` is built as a copy of the smoke overrides
    plus an ``update()``, deliberately, so the two runs cannot drift apart on a
    setting neither is about. That means it is not a dict literal and the smoke
    test's reader cannot see it, so this one follows the same two-step
    construction rather than the runner being reshaped to suit a test.
    """
    module = _module()
    constants = _string_constants(module)
    keys: set[str] = set()

    for node in module.body:
        if isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "SMOKE_PROJECT_OVERRIDES" in targets and isinstance(node.value, ast.Dict):
                keys |= _dict_literal_keys(node.value, constants)
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "update"
            and isinstance(func.value, ast.Name)
            and func.value.id == "FACE_EDIT_PROJECT_OVERRIDES"
        ):
            for argument in node.value.args:
                if isinstance(argument, ast.Dict):
                    keys |= _dict_literal_keys(argument, constants)
    return keys


def test_the_face_editor_overrides_name_keys_the_fixture_has():
    """A typo caught in milliseconds, on any machine, with no GPU and no weights.

    The runner asserts the same membership at run time (T-02-11) and that is the
    assertion that protects a caller. This one protects the developer, and the
    precedent is not hypothetical: plan 02-02's override list named
    ``ThreadsSlider`` and ``TextMaskingEnableToggle``, and the real keys are
    ``nThreadsSlider`` and ``ClipEnableToggle``.
    """
    import json

    tier = json.loads(SETTINGS_FIXTURE.read_text(encoding="utf-8"))["project"]
    keys = _face_edit_override_keys()
    assert keys, "no face-editor overrides found; this check would pass vacuously"
    assert "FaceEditorEnableToggle" in keys, (
        "the face-editor run does not set FaceEditorEnableToggle, so "
        "swap_edit_face_core would return the frame untouched and every "
        "assertion below would be about a plain swap: {}".format(sorted(keys))
    )
    unknown = sorted(key for key in keys if key not in tier)
    assert not unknown, (
        "the face-editor overrides name {} key(s) the project tier does not "
        "have: {}".format(len(unknown), ", ".join(unknown))
    )


def test_the_moved_control_is_moved_off_its_default():
    """A run where every control sits at its default proves nothing about controls.

    LivePortrait's warp-and-decode roundtrip changes pixels on its own, so a
    non-zero diff with every slider at zero would be a real result about the
    *path* and no result at all about the *controls*. This pins that the runner
    actually moves one.
    """
    import json

    module = _module()
    constants = _string_constants(module)
    key = constants.get("FACE_EDIT_CONTROL_KEY")
    assert key, "_engine_runner.py defines no FACE_EDIT_CONTROL_KEY string"

    value = None
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FACE_EDIT_CONTROL_VALUE"
            for t in node.targets
        ):
            value = ast.literal_eval(node.value)
    assert value is not None, "_engine_runner.py defines no FACE_EDIT_CONTROL_VALUE"

    tier = json.loads(SETTINGS_FIXTURE.read_text(encoding="utf-8"))["project"]
    assert key in tier, "{} is not a project-tier key".format(key)
    assert value != tier[key], (
        "{} is set to {!r}, which is already its default. The editor would have "
        "nothing to do that it was told to do.".format(key, value)
    )


def _parse(detail: str) -> dict[str, str]:
    """``faces=2 provider=CUDA ...`` -> a mapping. Non-``key=value`` words dropped."""
    fields = {}
    for word in detail.split():
        key, sep, value = word.partition("=")
        if sep:
            fields[key] = value
    return fields


@pytest.fixture(scope="session")
def faceedit_run(engine_python):
    """Run the editor once and share the result.

    Session-scoped: the run costs two model sets and a nested smoke run, and
    every assertion below is about the same single run rather than about a fresh
    one.

    The swap-only baseline is **truncated to zero bytes** first, on purpose. It
    is gitignored, so in this worktree it is usually sitting there and in a clean
    checkout it is not, and comparing against a stale or absent baseline is the
    exact failure the runner's regeneration rule exists to prevent. Zero bytes is
    the sharper case than deletion: an empty file satisfies ``isfile``, and
    ``cv2.imread`` answers a zero-byte PNG with ``None`` rather than an error, so
    a naive existence check would turn a truncated baseline into a comparison
    against nothing. Forcing it on every run means the regeneration branch is
    exercised every time rather than only in a clean checkout nobody runs.
    """
    EDITED_FRAME.unlink(missing_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    SWAP_ONLY_FRAME.write_bytes(b"")

    code, output = run_engine_runner(engine_python, ["--faceedit"])
    label, _, rest = output.partition(":")
    mode, _, detail = rest.partition(":")
    return {
        "code": code,
        "output": output,
        "label": label,
        "mode": mode,
        "detail": detail,
        "fields": _parse(detail),
    }


def test_the_editor_path_did_not_reach_qt(faceedit_run):
    """Exit 1 is the seal firing: Qt, VisoMaster's ``app`` or the backend.

    Its own test because roadmap criterion 4 asks specifically for "no Qt import
    error", and a collapsed ``exit == 0`` cannot distinguish that from any other
    way of failing.
    """
    assert faceedit_run["code"] != 1, (
        "the face-editor path reached a sealed root. This is criterion 4's "
        "no-Qt-import-error clause failing, and the detail names the module:\n"
        "  {}".format(faceedit_run["output"])
    )


def test_the_editor_path_had_every_dependency(faceedit_run):
    """Exit 2 is a missing engine package, not an engine result."""
    assert faceedit_run["code"] != 2, (
        "an engine dependency is missing on {}. Nothing about LivePortrait has "
        "been shown either way:\n  {}".format("the engine interpreter", faceedit_run["output"])
    )


def test_the_editor_path_found_every_asset_it_needed(faceedit_run):
    """Exit 3 is a missing file -- the lip array, the link, the media, the baseline.

    Never a skip. An absent, unregenerable baseline is a failure here rather than
    a no-op, because a no-op is how a criterion looks met with nothing behind it.
    """
    assert faceedit_run["code"] != 3, (
        "the face-editor run could not find an asset. If it names lip_array.pkl "
        "or model_assets, run tools/link_model_assets.py; if it names the "
        "swap-only baseline, the nested --smoke regeneration also failed and its "
        "output is quoted:\n  {}".format(faceedit_run["output"])
    )


def test_the_editor_path_raised_no_missing_attribute(faceedit_run):
    """Exit 4 with an ``AttributeError`` is criterion 4's other named failure.

    Phase 1 replaced ``main_window`` with ``EngineContext``, a plain dataclass.
    An attribute the editor path reads and the replacement never grew is exactly
    the shape of bug that survives every import gate and every type check, and
    it surfaces here as ``AttributeError`` nine hundred lines into the frame
    worker. Named separately so the message can say so.
    """
    assert "AttributeError" not in faceedit_run["detail"], (
        "the editor path read an attribute EngineContext does not have. This is "
        "criterion 4's no-missing-attribute-error clause failing:\n"
        "  {}".format(faceedit_run["output"])
    )
    assert faceedit_run["code"] != 4, (
        "the face-editor run raised inside the engine:\n  {}".format(
            faceedit_run["output"]
        )
    )


def test_the_editor_run_exits_clean_under_the_seal(faceedit_run):
    """The whole claim, after the individual failure modes have had their say."""
    assert faceedit_run["code"] == 0, faceedit_run["output"]
    assert faceedit_run["label"] == "CLEAN", faceedit_run["output"]
    assert faceedit_run["mode"] == "faceedit", faceedit_run["output"]


def test_the_seal_was_armed_against_packages_that_really_resolve(faceedit_run):
    """``app=yes`` means the VisoMaster seal refused a package that was there.

    A blocker is inert wherever the thing it blocks is absent, and an inert
    blocker reports the same ``CLEAN`` as a working one -- the lesson plan 01-04
    paid for.
    """
    reachable = faceedit_run["fields"].get("reachable_before_seal", "")
    assert "app=yes" in reachable, (
        "the editor run cannot show its VisoMaster seal is non-inert: {!r}. "
        "Point VISOMASTER_DIR at a checkout.".format(reachable)
    )


def test_the_lip_array_was_loaded_from_disk(faceedit_run):
    """``FaceEditors.__init__`` swallows a missing lip array and leaves it None.

    The constructor runs when the models processor is constructed, so a wrong
    working directory or a broken link produces a **fully constructed engine**
    with the lip retarget silently disabled, and the first sign of it is a wrong
    frame rather than an error. This is the assertion that turns that into a
    stated fact.

    Note what it does and does not prove. It proves the pickle was found and read
    through the ``model_assets`` link. It does not prove the array was *used*:
    ``lp_lip_array`` feeds ``apply_face_expression_restorer``, which is gated on
    ``FaceExpressionEnableToggle`` and is a different path from the face editor
    this run exercises. Saying so here is cheaper than letting a later reader
    conclude the editor depends on it.
    """
    assert faceedit_run["code"] == 0, faceedit_run["output"]
    assert faceedit_run["fields"].get("lip_array") == "populated", (
        "lp_lip_array was not populated from liveportrait_onnx/lip_array.pkl:\n"
        "  {}".format(faceedit_run["output"])
    )
    shape = faceedit_run["fields"].get("lip_array_shape", "")
    assert shape and shape != "?", (
        "the lip array reports no shape, so 'populated' is not a measurement: "
        "{}".format(faceedit_run["output"])
    )


def test_the_baseline_was_regenerated_rather_than_assumed(faceedit_run):
    """The fixture emptied the baseline; the runner must have rebuilt it.

    This is the regeneration rule proven rather than described. A runner that
    quietly compared against the zero-byte file would produce a diff against
    nothing and a green run, which is precisely the silent pass the rule exists
    to prevent.
    """
    assert faceedit_run["code"] == 0, faceedit_run["output"]
    assert faceedit_run["fields"].get("baseline") == "regenerated", (
        "the runner reported baseline={!r} after the fixture truncated {} to "
        "zero bytes. It compared against something it did not rebuild.".format(
            faceedit_run["fields"].get("baseline"), SWAP_ONLY_FRAME.name
        )
    )
    assert SWAP_ONLY_FRAME.stat().st_size > 0, (
        "{} is still empty after the run claimed to regenerate it".format(
            SWAP_ONLY_FRAME
        )
    )


def test_the_provider_stayed_on_cuda(faceedit_run):
    """T-02-15: the warping path's platform plugin loads only under TensorRT.

    Plan 02-02 refuses TensorRT outright rather than deprioritising it, because
    its provider options carry a relative ``tensorrt-engines`` cache path that
    lands wherever the process happens to be. Asserting the provider here is what
    turns "the plugin is not reached" from an argument into an observation.
    """
    assert faceedit_run["fields"].get("provider") == "CUDA", (
        "the editor ran on provider {!r}, not CUDA:\n  {}".format(
            faceedit_run["fields"].get("provider"), faceedit_run["output"]
        )
    )


def test_the_editor_model_was_named(faceedit_run):
    """``FaceEditorTypeSelection`` is read at eleven sites inside the editor."""
    assert faceedit_run["fields"].get("editor_model") == "Human-Face", (
        "unexpected editor model {!r}:\n  {}".format(
            faceedit_run["fields"].get("editor_model"), faceedit_run["output"]
        )
    )


def test_the_edited_frame_has_the_shape_of_the_frame_it_came_from(faceedit_run):
    fields = faceedit_run["fields"]
    assert fields["output_shape"] == fields["input_shape"], (
        "the editor returned a {} frame for a {} input. paste_back_adv puts the "
        "512 crop back into the original frame, so a mismatch means the paste "
        "back did not happen.".format(fields["output_shape"], fields["input_shape"])
    )


def test_the_editor_changed_the_frame(faceedit_run):
    """The claim: LivePortrait ran, and the frame is not the swap-only frame.

    Zero here has one meaning and it is not a subtle one -- the editor path did
    not execute. Both gates open and a pixel-identical result would mean
    ``swap_edit_face_core`` returned its input, which is what it does when
    ``FaceEditorEnableToggle`` is false.
    """
    assert faceedit_run["code"] == 0, faceedit_run["output"]
    diff = int(faceedit_run["fields"]["diff_vs_swap_only"])
    assert diff > 0, (
        "the edited frame is pixel-identical to the swap-only frame. The editor "
        "path did not run: both gates must be open, and swap_edit_face_core "
        "returns its input untouched when the per-face toggle is "
        "false.\n  {}".format(faceedit_run["output"])
    )


def test_the_editor_ran_on_top_of_a_swap(faceedit_run):
    """The editor was exercised over a swapped face, not in isolation.

    That is the combination the application will actually run, and it is where an
    interaction between the two would surface. A non-zero diff against the
    *decoded* frame as well as against the swap-only frame is what says both
    stages touched this pixel set.
    """
    assert int(faceedit_run["fields"]["diff_vs_source"]) > 0, (
        "the edited frame is identical to the decoded frame, so neither the swap "
        "nor the editor changed anything:\n  {}".format(faceedit_run["output"])
    )


def test_the_edited_frame_was_written_to_disk(faceedit_run):
    assert faceedit_run["code"] == 0, faceedit_run["output"]
    assert EDITED_FRAME.is_file(), "missing {}\n{}".format(
        EDITED_FRAME, faceedit_run["output"]
    )
    assert EDITED_FRAME.stat().st_size > 0, "empty {}".format(EDITED_FRAME)


def test_the_edited_frame_is_not_a_copy_of_the_swap_only_frame(faceedit_run):
    """Its own assertion, with its own message, rather than a pixel count.

    Both frames go through the same encoder from the same process, so an
    identical image really does produce an identical file: the byte comparison is
    exact here, not an approximation of one.
    """
    assert faceedit_run["code"] == 0, faceedit_run["output"]
    assert EDITED_FRAME.read_bytes() != SWAP_ONLY_FRAME.read_bytes(), (
        "{} and {} are byte-identical -- the editor returned the frame it was "
        "given.".format(EDITED_FRAME.name, SWAP_ONLY_FRAME.name)
    )


def test_the_editor_ran_within_a_bounded_time(faceedit_run):
    elapsed = float(faceedit_run["fields"]["elapsed"].rstrip("s"))
    assert elapsed < MAX_ELAPSED_SECONDS, (
        "the editor run took {:.1f}s against a {:.0f}s ceiling. Read the "
        "ceiling's docstring before raising it -- it bounds a run that never "
        "returns, not performance.".format(elapsed, MAX_ELAPSED_SECONDS)
    )
