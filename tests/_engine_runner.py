"""The sealed engine runner: the only thing in this project that runs engine code.

Run as a subprocess, **never imported by pytest** -- the same shape as
``_qt_guard_probe.py`` and for the same reason. The interpreter that runs pytest
has no torch; the interpreter that has torch also has Qt. Neither one alone can
both drive a test and execute the engine, so the two are split across a process
boundary and this file lives on the far side of it.

Before anything else is imported, a ``sys.meta_path`` finder is installed at
index 0 refusing three groups of roots:

* the seven Qt binding roots, from Phase 1;
* ``app``, VisoMaster's application package;
* ``backend``, VisoSwap's web layer.

Blocking the second and third is what turns the roadmap's criterion 3 from a text
search into a runtime proof. A grep says no line imports ``backend``. This says
the engine *cannot* reach ``backend`` or VisoMaster **even when both are
installed and importable** -- and the self-test puts a VisoMaster checkout on
``sys.path`` on purpose, so the seal is proven against a package that genuinely
would have resolved rather than against one that was never there.

That distinction is the lesson plan 01-04 paid for: a blocker is inert wherever
the thing it blocks is absent, and an inert blocker reports the same ``CLEAN`` as
a working one.

Exit codes extend the probe's vocabulary so a harness fault can never be read as
an engine pass::

    0  CLEAN           the mode completed and no sealed root was reached
    1  SEAL_BREACHED   a sealed root was reached, or leaked into sys.modules
    2  DEPS_MISSING    an engine dependency is not installed
    3  ASSET_MISSING   a required asset or fixture is not on disk
    4  ENGINE_ERROR    anything else

Exactly one machine-readable line is printed before exit, so pytest can attribute
a failure without parsing a traceback::

    LABEL:mode:detail

Usage::

    python tests/_engine_runner.py --selftest
    python tests/_engine_runner.py --import PySide6.QtCore
    python tests/_engine_runner.py --smoke
"""

import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)

# Both, and before the seal: the repo root so later modes can import
# ``visoswap.*``, the tests directory so the block lists resolve as a plain
# module without dragging in the ``tests`` package.
for _path in (_REPO_ROOT, _TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _blocked_roots import (  # noqa: E402 - must follow the sys.path setup above
    ALL_SEALED_ROOTS,
    SEALED_GROUPS,
    root_of,
)

EXIT_CLEAN = 0
EXIT_SEAL_BREACHED = 1
EXIT_DEPS_MISSING = 2
EXIT_ASSET_MISSING = 3
EXIT_ENGINE_ERROR = 4

VISOMASTER_ENV_VAR = "VISOMASTER_DIR"
DEFAULT_VISOMASTER_DIR = os.path.join("D:", os.sep, "Visomaster")

#: The runner's only settings source. Not the layout dicts (they need Qt), not
#: ``profiles.json`` (untyped strings), not a dict written inline in a test.
#:
#: Overridable so the ASSET_MISSING path can be *demonstrated* rather than
#: assumed -- an exit code nobody has watched fire is an exit code nobody knows
#: the meaning of, which is the whole reason this vocabulary exists.
SETTINGS_FIXTURE_ENV_VAR = "VISOSWAP_SETTINGS_FIXTURE"
DEFAULT_SETTINGS_FIXTURE = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "engine_settings.json"
)

#: The two media fixtures the smoke mode swaps between, and their overrides.
#:
#: They are **deliberately different people**. The video already contains the
#: face in ``17f0d620_rosh.jpg``; using that image as the source would swap a
#: face onto itself, and the non-zero-diff assertion would then be testing
#: nothing while appearing to pass.
#:
#: Neither file is committed -- both are personal media. A missing one is
#: ``ASSET_MISSING``, never a skip: Phase 1 established that a missing dependency
#: must not read as a pass, and that rule covers media as well as packages.
VIDEO_ENV_VAR = "VISOSWAP_TEST_VIDEO"
SOURCE_ENV_VAR = "VISOSWAP_TEST_SOURCE"
DEFAULT_TEST_VIDEO = os.path.join(
    "D:", os.sep, "Visomaster", "output", "17f0d620_rosh_generate_135bda4c9686.mp4"
)
DEFAULT_TEST_SOURCE = os.path.join(
    "D:", os.sep, "Visomaster", "inputt", "598004cb_tonima.JPG"
)

#: Where the smoke mode writes its evidence. Gitignored: these are derivatives of
#: personal media and committing one would be an information-disclosure bug
#: rather than an untidy repository (T-02-10).
ARTIFACTS_DIR = os.path.join(_REPO_ROOT, "tests", "artifacts")
SOURCE_FRAME_ARTIFACT = "smoke_source_frame.png"
SWAPPED_FRAME_ARTIFACT = "smoke_swapped_frame.png"

#: The smoke run's settings, applied over the fixture.
#:
#: Every key here must already exist in the fixture -- an override introducing a
#: key the fixture lacks is a typo, and :func:`apply_overrides` refuses it rather
#: than writing it in blind (T-02-11).
#:
#: Most of these already *are* the fixture's values. They are stated anyway
#: because the point is that this run is pinned to a known configuration: if a
#: regenerated fixture moves a default, the smoke run must keep swapping the same
#: way, and the diff must show which default moved.
SMOKE_GLOBAL_OVERRIDES = {
    # CUDA, never TensorRT: its provider options write an engine and a timing
    # cache to the relative path 'tensorrt-engines'. See T-02-09.
    "ProvidersPrioritySelection": "CUDA",
    "DetectorModelSelection": "RetinaFace",
    "RecognitionModelSelection": "Inswapper128ArcFace",
    "SimilarityTypeSelection": "Opal",
    "DetectorScoreSlider": 50,
    "MaxFacesToDetectSlider": 20,
    "LandmarkDetectToggle": False,
    "EmbMergeMethodSelection": "Mean",
    # One thread. The plan named this key 'ThreadsSlider'; the fixture -- and
    # upstream's settings layout -- call it 'nThreadsSlider'.
    "nThreadsSlider": 1,
}

SMOKE_PROJECT_OVERRIDES = {
    "SwapModelSelection": "Inswapper128",
    "SwapperResSelection": "128",
    "SimilarityThresholdSlider": 60,
    # Text masking off. The plan named this key 'TextMaskingEnableToggle'; the
    # CLIPseg text-mask control is 'ClipEnableToggle'. That path is descoped for
    # Phase 2 by owner decision and its weights are absent from this machine, so
    # leaving it on would fail inside a model load rather than swap a face.
    "ClipEnableToggle": False,
    "FaceEditorEnableToggle": False,
}


class SealBroken(ImportError):
    """Raised by the finder when a sealed root is requested.

    A dedicated type so "we refused this" is distinguishable from "the module was
    genuinely absent", which are otherwise the same ``ImportError``.
    """


class SealingFinder:
    """A ``sys.meta_path`` finder that denies every sealed root."""

    def find_spec(self, fullname, path=None, target=None):
        if root_of(fullname) in ALL_SEALED_ROOTS:
            raise SealBroken("sealed import refused: {}".format(fullname), name=fullname)
        return None

    # Legacy hook, for anything still on the pre-PEP-451 protocol.
    def find_module(self, fullname, path=None):  # pragma: no cover
        self.find_spec(fullname, path)
        return None


def leaked_roots():
    return sorted(root for root in ALL_SEALED_ROOTS if root in sys.modules)


def one_line(text):
    return " ".join(str(text).split())


def report(code, label, mode, detail):
    print("{}:{}:{}".format(label, mode, one_line(detail)))
    return code


def resolve_visomaster_dir():
    return os.path.abspath(os.environ.get(VISOMASTER_ENV_VAR) or DEFAULT_VISOMASTER_DIR)


def reachability_before_sealing():
    """Which sealed roots would actually have resolved, measured *before* arming.

    This is the anti-inertness check. ``find_spec`` after the seal is armed trips
    the seal, so it has to happen first, and its result is reported rather than
    asserted: ``backend`` does not exist until Phase 5, and a self-test that
    demanded it be reachable would be a test of the calendar.
    """
    import importlib.util

    reachable = {}
    for root in sorted(ALL_SEALED_ROOTS):
        try:
            reachable[root] = importlib.util.find_spec(root) is not None
        except Exception:  # noqa: BLE001 - a broken parent package is "not reachable"
            reachable[root] = False
    return reachable


def arm_seal():
    """Install the finder at index 0. Returns the finder."""
    finder = SealingFinder()
    sys.meta_path.insert(0, finder)
    return finder


def resolve_settings_fixture():
    return os.path.abspath(
        os.environ.get(SETTINGS_FIXTURE_ENV_VAR) or DEFAULT_SETTINGS_FIXTURE
    )


def load_settings(path):
    """The typed settings pair, or ``None`` if the fixture is missing."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def mode_selftest():
    """Arm the seals, prove each group refuses, import nothing from the engine.

    Later plans add modes that actually swap a frame. This one proves only that
    the harness is armed and *capable of failing*, which is the thing that has to
    be true before any engine failure can be believed.
    """
    mode = "selftest"

    preloaded = leaked_roots()
    if preloaded:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots already in sys.modules before the seal armed: "
            + ", ".join(preloaded),
        )

    # On sys.path deliberately: a seal proven against an absent package proves
    # nothing. With the checkout on the path, `app` genuinely resolves, so the
    # refusal below is the seal firing rather than the module simply not being
    # there.
    visomaster_dir = resolve_visomaster_dir()
    if os.path.isdir(visomaster_dir) and visomaster_dir not in sys.path:
        sys.path.append(visomaster_dir)

    reachable = reachability_before_sealing()

    fixture_path = resolve_settings_fixture()
    settings = load_settings(fixture_path)
    if settings is None:
        return report(
            EXIT_ASSET_MISSING,
            "ASSET_MISSING",
            mode,
            "settings fixture not found at {} -- regenerate with "
            "tools/dump_engine_settings.py".format(fixture_path),
        )

    arm_seal()

    import importlib

    # Each group is provoked separately. Concluding "the seal is armed" from one
    # group firing is exactly how the other two end up unenforced.
    for group, roots in SEALED_GROUPS:
        # Provoke with a root that genuinely resolves where one exists, so the
        # refusal is the seal firing rather than the module simply not being
        # installed. Falls back to the first root by name when the whole group is
        # absent -- ``backend`` until Phase 5 -- which still proves the finder
        # fires on the name, and is reported as such below.
        candidates = sorted(root for root in roots if reachable.get(root))
        probe_root = candidates[0] if candidates else sorted(roots)[0]
        try:
            importlib.import_module(probe_root)
        except SealBroken:
            pass
        except BaseException as exc:  # noqa: BLE001 - any other outcome is a failure
            return report(
                EXIT_SEAL_BREACHED,
                "SEAL_BREACHED",
                mode,
                "the {} seal raised {} instead of SealBroken for {!r}: {}".format(
                    group, type(exc).__name__, probe_root, exc
                ),
            )
        else:
            return report(
                EXIT_SEAL_BREACHED,
                "SEAL_BREACHED",
                mode,
                "the {} seal did not fire: import {!r} succeeded".format(
                    group, probe_root
                ),
            )

    leaked = leaked_roots()
    if leaked:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots in sys.modules after provoking: " + ", ".join(leaked),
        )

    return report(
        EXIT_CLEAN,
        "CLEAN",
        mode,
        "groups={} settings=project:{},global:{} reachable_before_seal={}".format(
            "+".join(group for group, _ in SEALED_GROUPS),
            len(settings.get("project", {})),
            len(settings.get("global", {})),
            ",".join(
                "{}={}".format(root, "yes" if ok else "no")
                for root, ok in sorted(reachable.items())
            ),
        ),
    )


def mode_import(dotted):
    """Import one named module under the armed seal and report the outcome.

    This is how the runner is *shown* capable of exiting 1 rather than assumed
    to be. A gate nobody has watched fail is a gate nobody knows the state of.
    """
    mode = "import:{}".format(dotted)

    preloaded = leaked_roots()
    if preloaded:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots already in sys.modules before the seal armed: "
            + ", ".join(preloaded),
        )

    visomaster_dir = resolve_visomaster_dir()
    if os.path.isdir(visomaster_dir) and visomaster_dir not in sys.path:
        sys.path.append(visomaster_dir)

    arm_seal()

    import importlib

    try:
        importlib.import_module(dotted)
    except SealBroken as exc:
        return report(EXIT_SEAL_BREACHED, "SEAL_BREACHED", mode, exc)
    except ModuleNotFoundError as exc:
        if root_of(getattr(exc, "name", None)) in ALL_SEALED_ROOTS:
            # Defensive: the finder raises, so this should be unreachable.
            return report(EXIT_SEAL_BREACHED, "SEAL_BREACHED", mode, exc)
        return report(EXIT_DEPS_MISSING, "DEPS_MISSING", mode, exc)
    except FileNotFoundError as exc:
        return report(EXIT_ASSET_MISSING, "ASSET_MISSING", mode, exc)
    except BaseException as exc:  # noqa: BLE001 - the runner reports, never raises
        return report(
            EXIT_ENGINE_ERROR,
            "ENGINE_ERROR",
            mode,
            "{}: {}".format(type(exc).__name__, exc),
        )

    leaked = leaked_roots()
    if leaked:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots in sys.modules after importing {}: {}".format(
                dotted, ", ".join(leaked)
            ),
        )

    return report(EXIT_CLEAN, "CLEAN", mode, "imported")


def resolve_media(env_var, default):
    return os.path.abspath(os.environ.get(env_var) or default)


def apply_overrides(tier, overrides, tier_name):
    """``tier`` updated in place, refusing any key it does not already have.

    An override naming a key the fixture lacks is a typo -- and a typo written
    in blind surfaces much later, as a ``KeyError`` deep inside a tensor
    operation, or worse as a setting that silently does nothing. Assert
    membership at the boundary instead (T-02-11).
    """
    unknown = sorted(key for key in overrides if key not in tier)
    if unknown:
        raise KeyError(
            "smoke overrides name {} key(s) absent from the {} tier: {}".format(
                len(unknown), tier_name, ", ".join(unknown)
            )
        )
    tier.update(overrides)
    return tier


def mode_smoke():
    """Load a video, detect a face, swap a frame, write the evidence to disk.

    The whole point of the phase in one function: this is the first thing in the
    project that produces a pixel. Everything before it was import-cleanliness.

    It runs under the same seal as every other mode, so a passing run is also a
    proof that the engine reached a swapped frame **without** Qt, without
    VisoMaster's ``app`` package and without the backend -- which is roadmap
    criterion 3 as a runtime fact rather than a text search.
    """
    mode = "smoke"

    preloaded = leaked_roots()
    if preloaded:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots already in sys.modules before the seal armed: "
            + ", ".join(preloaded),
        )

    visomaster_dir = resolve_visomaster_dir()
    if os.path.isdir(visomaster_dir) and visomaster_dir not in sys.path:
        sys.path.append(visomaster_dir)

    reachable = reachability_before_sealing()

    # Every asset resolved before anything expensive happens, so a missing file
    # costs a millisecond rather than a model load. None of these is a skip.
    fixture_path = resolve_settings_fixture()
    settings = load_settings(fixture_path)
    if settings is None:
        return report(
            EXIT_ASSET_MISSING,
            "ASSET_MISSING",
            mode,
            "settings fixture not found at {} -- regenerate with "
            "tools/dump_engine_settings.py".format(fixture_path),
        )

    video_path = resolve_media(VIDEO_ENV_VAR, DEFAULT_TEST_VIDEO)
    source_path = resolve_media(SOURCE_ENV_VAR, DEFAULT_TEST_SOURCE)
    for label, path, env_var in (
        ("target video", video_path, VIDEO_ENV_VAR),
        ("source face", source_path, SOURCE_ENV_VAR),
    ):
        if not os.path.isfile(path):
            return report(
                EXIT_ASSET_MISSING,
                "ASSET_MISSING",
                mode,
                "{} not found at {} -- point {} at one, or see "
                "docs/engine-test-assets.md".format(label, path, env_var),
            )

    models_dir = os.path.join(_REPO_ROOT, "model_assets")
    if not os.path.isdir(models_dir):
        return report(
            EXIT_ASSET_MISSING,
            "ASSET_MISSING",
            mode,
            "model_assets not reachable at {} -- run tools/link_model_assets.py. "
            "Without it ModelsProcessor constructs a silently degraded "
            "processor rather than raising.".format(models_dir),
        )

    arm_seal()

    import time

    started = time.monotonic()

    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        return report(EXIT_DEPS_MISSING, "DEPS_MISSING", mode, exc)

    try:
        from visoswap.engine import Engine
    except SealBroken as exc:
        return report(EXIT_SEAL_BREACHED, "SEAL_BREACHED", mode, exc)
    except ModuleNotFoundError as exc:
        if root_of(getattr(exc, "name", None)) in ALL_SEALED_ROOTS:
            return report(EXIT_SEAL_BREACHED, "SEAL_BREACHED", mode, exc)
        return report(EXIT_DEPS_MISSING, "DEPS_MISSING", mode, exc)
    except BaseException as exc:  # noqa: BLE001 - the runner reports, never raises
        return report(
            EXIT_ENGINE_ERROR,
            "ENGINE_ERROR",
            mode,
            "importing visoswap.engine raised {}: {}".format(type(exc).__name__, exc),
        )

    try:
        control = apply_overrides(
            dict(settings.get("global", {})), SMOKE_GLOBAL_OVERRIDES, "global"
        )
        parameters = apply_overrides(
            dict(settings.get("project", {})), SMOKE_PROJECT_OVERRIDES, "project"
        )

        engine = Engine(
            device="cuda", global_settings=control, project_settings=parameters
        )
        media = engine.load(video_path)
        cards = engine.detect_faces(0)
        if not cards:
            return report(
                EXIT_ENGINE_ERROR,
                "ENGINE_ERROR",
                mode,
                "no faces detected in any sampled frame of {}".format(
                    os.path.basename(video_path)
                ),
            )

        frame_number = 0
        # The engine's own decoder is the one that fed detection, so read the
        # comparison frame through the same path rather than opening a second
        # capture: two decoders can disagree on which frame index is which.
        source_frame = engine._read_frame(frame_number)  # noqa: SLF001
        swapped = engine.swap(frame_number, source_path)

        if source_frame.shape != swapped.shape:
            return report(
                EXIT_ENGINE_ERROR,
                "ENGINE_ERROR",
                mode,
                "swapped frame shape {} does not match the decoded frame's "
                "{}".format(swapped.shape, source_frame.shape),
            )

        # Pixels, not channel values: a pixel counts once however many of its
        # three channels moved.
        diff_pixels = int(np.count_nonzero(np.any(source_frame != swapped, axis=-1)))

        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        written = []
        for name, image in (
            (SOURCE_FRAME_ARTIFACT, source_frame),
            (SWAPPED_FRAME_ARTIFACT, swapped),
        ):
            path = os.path.join(ARTIFACTS_DIR, name)
            if not cv2.imwrite(path, image):
                return report(
                    EXIT_ENGINE_ERROR,
                    "ENGINE_ERROR",
                    mode,
                    "could not write {}".format(path),
                )
            written.append(name)

        provider = engine.context.models_processor.provider_name
        engine._release()  # noqa: SLF001 - the decoder holds an OS handle
    except SealBroken as exc:
        return report(EXIT_SEAL_BREACHED, "SEAL_BREACHED", mode, exc)
    except FileNotFoundError as exc:
        return report(EXIT_ASSET_MISSING, "ASSET_MISSING", mode, exc)
    except BaseException as exc:  # noqa: BLE001 - the runner reports, never raises
        import traceback

        return report(
            EXIT_ENGINE_ERROR,
            "ENGINE_ERROR",
            mode,
            "{}: {} | {}".format(
                type(exc).__name__, exc, traceback.format_exc().replace("\n", " ~ ")
            ),
        )

    leaked = leaked_roots()
    if leaked:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots in sys.modules after swapping: " + ", ".join(leaked),
        )

    # One line, no image payload -- the project's own logging convention, and
    # T-02-10: nothing that could reconstruct a face goes into a log.
    return report(
        EXIT_CLEAN,
        "CLEAN",
        mode,
        "faces={} frames={} fps={:.3f} provider={} input_shape={} output_shape={} "
        "diff_pixels={} elapsed={:.1f}s artifacts={} "
        "reachable_before_seal={}".format(
            len(cards),
            media["frame_count"],
            media["fps"],
            provider,
            "x".join(str(n) for n in source_frame.shape),
            "x".join(str(n) for n in swapped.shape),
            diff_pixels,
            time.monotonic() - started,
            "+".join(written),
            ",".join(
                "{}={}".format(root, "yes" if ok else "no")
                for root, ok in sorted(reachable.items())
            ),
        ),
    )


USAGE = "usage: _engine_runner.py (--selftest | --import DOTTED_NAME | --smoke)"


def main(argv):
    if argv == ["--selftest"]:
        return mode_selftest()
    if argv == ["--smoke"]:
        return mode_smoke()
    if len(argv) == 2 and argv[0] == "--import":
        return mode_import(argv[1])
    return report(EXIT_ENGINE_ERROR, "ENGINE_ERROR", "-", USAGE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
