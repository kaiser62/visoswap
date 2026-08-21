"""The phase's actual claim: one video, one source face, one swapped frame.

Everything before this file was import-cleanliness -- nothing had produced a
pixel. This runs the engine end to end inside the sealed subprocess and checks
what came out.

The seal is what makes the result mean something. A swap that completes with Qt,
VisoMaster's ``app`` package and the backend all made unimportable is roadmap
criterion 3 as a runtime fact, and on the default engine interpreter the first
two of those genuinely resolve -- the runner reports which ones did, so this is
not a seal proven against packages that were never there.

**Nothing here skips.** Missing media, a missing settings fixture and a missing
model link are all ``ASSET_MISSING`` from the runner and a failure here. Phase 1
established that a missing dependency must never read as a pass, and that rule
covers media as much as it covers packages: a skipped swap test is a phase that
looks finished and has swapped nothing.
"""

import ast
import json
from pathlib import Path

import pytest

from tests.conftest import run_engine_runner

TESTS_DIR = Path(__file__).resolve().parent
RUNNER_SOURCE = TESTS_DIR / "_engine_runner.py"
SETTINGS_FIXTURE = TESTS_DIR / "fixtures" / "engine_settings.json"
ARTIFACTS_DIR = TESTS_DIR / "artifacts"
SOURCE_FRAME = ARTIFACTS_DIR / "smoke_source_frame.png"
SWAPPED_FRAME = ARTIFACTS_DIR / "smoke_swapped_frame.png"

#: A ceiling on the whole run, in seconds (T-02-08).
#:
#: This bounds *unbounded*, not performance. A cold run measured 7.0s on the
#: clean venv and 7.9s on the Qt-carrying interpreter, both including the model
#: loads; the limit is two orders of magnitude above that on purpose, because a
#: crafted container that makes the decoder chew forever is the threat, and a
#: tight bound here would only produce a flaky test on a busy GPU. It is not a
#: performance gate and must not be tightened into one -- a regression from 7s to
#: 60s is a real problem this deliberately will not catch, and the place to catch
#: it is a benchmark, not a correctness test.
MAX_ELAPSED_SECONDS = 240.0


def _override_keys(name: str) -> set[str]:
    """The keys of a module-level dict literal in ``_engine_runner.py``.

    Read with ``ast`` rather than imported. ``_engine_runner`` is deliberately
    never imported by pytest -- it runs on the far side of a process boundary,
    on an interpreter that has torch and no pytest -- and that separation is
    worth more than the convenience of an import here.
    """
    module = ast.parse(RUNNER_SOURCE.read_text(encoding="utf-8"), str(RUNNER_SOURCE))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if name in targets and isinstance(node.value, ast.Dict):
            return {
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    raise AssertionError(
        "{} defines no module-level dict named {!r}".format(RUNNER_SOURCE.name, name)
    )


@pytest.mark.parametrize(
    ("override_name", "tier_name"),
    [
        ("SMOKE_GLOBAL_OVERRIDES", "global"),
        ("SMOKE_PROJECT_OVERRIDES", "project"),
    ],
)
def test_every_smoke_override_names_a_key_the_fixture_has(override_name, tier_name):
    """A typo in an override is caught here, not eight seconds into a GPU run.

    The runner asserts the same membership at runtime (T-02-11), and that
    assertion is the one that protects a caller. This one protects the
    *developer*: it needs no GPU, no weights and no media, so it fails in
    milliseconds on any machine and names the key. Two checks of one rule,
    deliberately, because they fail in different places for different people.

    It has already earned its keep. The plan's override list named
    ``ThreadsSlider`` and ``TextMaskingEnableToggle``; the fixture -- and
    upstream -- call them ``nThreadsSlider`` and ``ClipEnableToggle``.
    """
    tier = json.loads(SETTINGS_FIXTURE.read_text(encoding="utf-8"))[tier_name]
    overrides = _override_keys(override_name)
    assert overrides, "{} is empty; this check would pass vacuously".format(
        override_name
    )
    unknown = sorted(key for key in overrides if key not in tier)
    assert not unknown, (
        "{} names {} key(s) the {} tier does not have: {}. An override that "
        "introduces a key is a typo -- the engine would either raise deep inside "
        "a tensor operation or, worse, silently do nothing.".format(
            override_name, len(unknown), tier_name, ", ".join(unknown)
        )
    )


def _parse(detail: str) -> dict[str, str]:
    """``faces=2 frames=24 ...`` -> a mapping. Non-``key=value`` words dropped."""
    fields = {}
    for word in detail.split():
        key, sep, value = word.partition("=")
        if sep:
            fields[key] = value
    return fields


@pytest.fixture(scope="session")
def smoke_run(engine_python):
    """Run the swap once and share the result.

    Session-scoped because the run costs a model load, and every assertion below
    is about the same single run rather than about a fresh one. A per-test
    fixture would multiply an eight-second GPU job by the number of things worth
    asserting about it.
    """
    for artifact in (SOURCE_FRAME, SWAPPED_FRAME):
        # Deleted first so a passing assertion can never be satisfied by an
        # artifact left behind by an earlier run.
        artifact.unlink(missing_ok=True)

    code, output = run_engine_runner(engine_python, ["--smoke"])
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


def test_the_swap_runs_clean_under_the_seal(smoke_run):
    assert smoke_run["code"] == 0, (
        "the sealed smoke run did not exit CLEAN:\n  {}\n"
        "ASSET_MISSING (3) means a fixture is not on disk -- see "
        "docs/engine-test-assets.md. SEAL_BREACHED (1) means the engine reached "
        "Qt, VisoMaster or the backend on the way to a swapped "
        "frame.".format(smoke_run["output"])
    )
    assert smoke_run["label"] == "CLEAN", smoke_run["output"]
    assert smoke_run["mode"] == "smoke", smoke_run["output"]


def test_the_seal_was_armed_against_packages_that_really_resolve(smoke_run):
    """``app=yes`` in the report means the VisoMaster seal refused a real package.

    A blocker is inert wherever the thing it blocks is absent, and an inert
    blocker reports the same ``CLEAN`` as a working one -- the lesson plan 01-04
    paid for. The runner puts a VisoMaster checkout on ``sys.path`` before
    arming, so ``app`` genuinely would have imported.
    """
    reachable = smoke_run["fields"].get("reachable_before_seal", "")
    assert "app=yes" in reachable, (
        "the smoke run cannot show its VisoMaster seal is non-inert: {!r}. "
        "Point VISOMASTER_DIR at a checkout.".format(reachable)
    )


def test_at_least_one_face_was_detected(smoke_run):
    faces = int(smoke_run["fields"]["faces"])
    assert faces >= 1, (
        "detect_faces returned no FaceCard for any sampled frame. The swap that "
        "follows would be a no-op, so this is the roadmap's first criterion "
        "failing rather than a detail: {}".format(smoke_run["output"])
    )


def test_the_swapped_frame_has_the_shape_of_the_frame_it_came_from(smoke_run):
    fields = smoke_run["fields"]
    assert fields["output_shape"] == fields["input_shape"], (
        "swap() returned a {} frame for a {} input. process_frame upscales "
        "anything smaller than 512 on a side, so a mismatch here means either "
        "the fixture clip shrank below that or the worker resized and did not "
        "restore.".format(fields["output_shape"], fields["input_shape"])
    )


def test_the_swap_changed_pixels(smoke_run):
    diff_pixels = int(smoke_run["fields"]["diff_pixels"])
    assert diff_pixels > 0, (
        "the swapped frame is pixel-identical to the frame it came from. This is "
        "the specific failure the whole phase exists to detect: a face assigned "
        "under one key and looked up under another produces a similarity check "
        "that never matches, and the pipeline then returns the original frame "
        "without erroring: {}".format(smoke_run["output"])
    )


def test_the_swap_ran_within_a_bounded_time(smoke_run):
    elapsed = float(smoke_run["fields"]["elapsed"].rstrip("s"))
    assert elapsed < MAX_ELAPSED_SECONDS, (
        "the smoke run took {:.1f}s against a {:.0f}s ceiling. Read the ceiling's "
        "docstring before raising it -- it exists to bound a decoder that never "
        "returns, not to police performance.".format(elapsed, MAX_ELAPSED_SECONDS)
    )


def test_both_frames_were_written_to_disk(smoke_run):
    assert smoke_run["code"] == 0, smoke_run["output"]
    for artifact in (SOURCE_FRAME, SWAPPED_FRAME):
        assert artifact.is_file(), "missing {}\n{}".format(
            artifact, smoke_run["output"]
        )
        assert artifact.stat().st_size > 0, "empty {}".format(artifact)


def test_the_swapped_frame_is_not_a_copy_of_the_source_frame(smoke_run):
    """Its own assertion, not folded into the pixel count.

    A no-op swap producing a perfect copy is the single failure this phase is
    built to catch, and it deserves to fail with its own message rather than as
    "diff_pixels was 0". Both frames go through the same encoder from the same
    process, so an identical image really does produce an identical file: a byte
    comparison is exact here, not an approximation of one.
    """
    assert smoke_run["code"] == 0, smoke_run["output"]
    assert SOURCE_FRAME.read_bytes() != SWAPPED_FRAME.read_bytes(), (
        "{} and {} are byte-identical. The pipeline ran and returned the frame "
        "it was given -- which every non-zero-diff check would also have caught, "
        "but this is the one that names it.".format(
            SOURCE_FRAME.name, SWAPPED_FRAME.name
        )
    )


def test_the_artifacts_are_not_tracked_by_git():
    """The outputs are derivatives of personal media (T-02-10).

    ``.gitignore`` is checked rather than ``git status`` so this test says the
    same thing in a clean checkout, on a machine with no fixtures, as it does
    after a run.
    """
    gitignore = Path(__file__).resolve().parent.parent / ".gitignore"
    patterns = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "tests/artifacts/" in patterns, (
        "tests/artifacts/ is not in .gitignore. It holds decoded frames of "
        "personal media and swapped faces made from them; committing one is an "
        "information-disclosure bug, not an untidy repository."
    )
