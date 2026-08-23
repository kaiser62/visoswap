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
installed and importable** -- and the self-test puts a package named ``app`` on
``sys.path`` on purpose (a self-built subject, or a real checkout via
``VISOMASTER_DIR``), so the seal is proven against a package that genuinely
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
    python tests/_engine_runner.py --source-cache
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

from visoswap.schema import resolve_models_dir  # noqa: E402 - same reason

EXIT_CLEAN = 0
EXIT_SEAL_BREACHED = 1
EXIT_DEPS_MISSING = 2
EXIT_ASSET_MISSING = 3
EXIT_ENGINE_ERROR = 4

VISOMASTER_ENV_VAR = "VISOMASTER_DIR"
# Plan 04-04 (sever VisoMaster): the default is no longer a VisoMaster checkout.
# `resolve_visomaster_dir` builds a self-contained `app` subject instead; a real
# checkout may still be supplied as an override for a sharper run.
DEFAULT_VISOMASTER_DIR = None

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
#: Project-owned media directory (gitignored). Plan 04-04 (sever VisoMaster):
#: the smoke fixtures no longer default into D:/Visomaster; they live here, a
#: copy made by the developer, and a missing file stays ASSET_MISSING.
MEDIA_DIR = os.path.join(_REPO_ROOT, "tests", "media")
DEFAULT_TEST_VIDEO = os.path.join(
    MEDIA_DIR, "17f0d620_rosh_generate_135bda4c9686.mp4"
)
DEFAULT_TEST_SOURCE = os.path.join(
    MEDIA_DIR, "598004cb_tonima.JPG"
)

#: Where the smoke mode writes its evidence. Gitignored: these are derivatives of
#: personal media and committing one would be an information-disclosure bug
#: rather than an untidy repository (T-02-10).
ARTIFACTS_DIR = os.path.join(_REPO_ROOT, "tests", "artifacts")
SOURCE_FRAME_ARTIFACT = "smoke_source_frame.png"
SWAPPED_FRAME_ARTIFACT = "smoke_swapped_frame.png"
FACE_EDIT_FRAME_ARTIFACT = "liveportrait_frame.png"
VIDEO_ARTIFACT = "smoke_swapped.mp4"

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

#: The one editor control moved off its default, and the value it moves to.
#:
#: Named as constants rather than buried in the override dict because the run
#: prints them: a face-editor result nobody can reproduce is a face-editor result
#: nobody can argue with.
#:
#: ``MouthSmileDecimalSlider`` runs -0.30 to 1.30 in upstream's face-editor
#: layout, so 0.60 is a firmly in-range, unmistakable smile. It is chosen over
#: the crop scale because it drives ``update_delta_new_smile`` into the
#: expression delta -- it changes the *face*, where a crop-scale change mostly
#: changes how much of it the warp sees.
FACE_EDIT_CONTROL_KEY = "MouthSmileDecimalSlider"
FACE_EDIT_CONTROL_VALUE = 0.60

#: The face-editor run's project tier: the smoke run's, plus both editor gates.
#:
#: Derived from ``SMOKE_PROJECT_OVERRIDES`` rather than restated, so the two runs
#: cannot drift apart on a setting neither is about. The swap stays **on**: the
#: editor running on top of a swapped face is the combination the application
#: will actually run, and an interaction between the two would surface nowhere
#: else.
FACE_EDIT_PROJECT_OVERRIDES = dict(SMOKE_PROJECT_OVERRIDES)
FACE_EDIT_PROJECT_OVERRIDES.update(
    {
        # Gate one of two. The other is ``EngineContext.edit_faces_enabled``,
        # which is not a settings key -- it is one of the two fields that
        # replaced a Qt toggle button, and it is set on the context object in
        # :func:`mode_faceedit`.
        "FaceEditorEnableToggle": True,
        FACE_EDIT_CONTROL_KEY: FACE_EDIT_CONTROL_VALUE,
    }
)

#: Exit code -> the label that goes on its report line.
#:
#: Needed because the face-editor mode may shell out to this file's own smoke
#: mode to rebuild its baseline, and a child's exit code is propagated rather
#: than flattened: a seal breach in the child is a seal breach, not an
#: "asset problem in the parent".
EXIT_LABELS = {
    EXIT_CLEAN: "CLEAN",
    EXIT_SEAL_BREACHED: "SEAL_BREACHED",
    EXIT_DEPS_MISSING: "DEPS_MISSING",
    EXIT_ASSET_MISSING: "ASSET_MISSING",
    EXIT_ENGINE_ERROR: "ENGINE_ERROR",
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


def _build_self_app_subject():
    """A minimal ``app`` package in a temp dir, standing in for VisoMaster's.

    Plan 04-04 (sever VisoMaster): the seal's non-inertness proof must hold on a
    machine with no VisoMaster install. This builds a package named ``app`` --
    the name the seal refuses -- so ``app`` genuinely resolves before the seal
    arms and a refusal afterwards is the seal firing, not the package having been
    absent. It includes the exact module path the breach case imports
    (``app.ui.widgets.common_layout_data``) so that case resolves too.
    """
    import tempfile

    subject = tempfile.mkdtemp(prefix="visoswap-seal-")
    pkg = os.path.join(subject, "app")
    os.makedirs(os.path.join(pkg, "ui", "widgets"), exist_ok=True)
    with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as handle:
        handle.write("# self-built seal subject (plan 04-04)\n__version__ = 'seal-subject'\n")
    with open(
        os.path.join(pkg, "ui", "widgets", "common_layout_data.py"),
        "w",
        encoding="utf-8",
    ) as handle:
        handle.write("# self-built seal subject (plan 04-04)\nCOMMON_LAYOUT_DATA = {}\n")
    return subject


def resolve_visomaster_dir():
    """The directory to put on ``sys.path`` as the VisoMaster seal subject.

    A real checkout via ``VISOMASTER_DIR`` is the sharper run (a genuinely larger
    ``app``). Otherwise the default is a self-built ``app`` package, so the
    non-inertness proof holds on any machine with no VisoMaster install.
    """
    override = os.environ.get(VISOMASTER_ENV_VAR)
    if override and os.path.isdir(override):
        return os.path.abspath(override)
    return _build_self_app_subject()


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


def mode_smoke(guard=None):
    """Load a video, detect a face, swap a frame, write the evidence to disk.

    The whole point of the phase in one function: this is the first thing in the
    project that produces a pixel. Everything before it was import-cleanliness.

    It runs under the same seal as every other mode, so a passing run is also a
    proof that the engine reached a swapped frame **without** Qt, without
    VisoMaster's ``app`` package and without the backend -- which is roadmap
    criterion 3 as a runtime fact rather than a text search.

    ``guard`` is an optional ``_open_guard.VisoMasterOpenGuard``; when given it is
    armed for the run and its observed open count is reported, so ``--no-visomaster``
    can prove no opened path resolved inside a VisoMaster install.
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

    if guard is not None:
        guard.__enter__()

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

    models_dir = str(resolve_models_dir())
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
    guard_report = ""
    if guard is not None:
        guard_report = " guard_opens={}".format(guard.opens)
    return report(
        EXIT_CLEAN,
        "CLEAN",
        mode,
        "faces={} frames={} fps={:.3f} provider={} input_shape={} output_shape={} "
        "diff_pixels={} elapsed={:.1f}s artifacts={} "
        "reachable_before_seal={}{}".format(
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
            guard_report,
        ),
    )


def mode_video():
    """Swap every frame of the bound video and write an output mp4.

    The engine's public surface is single-frame (``Engine.swap(frame, source)``)
    and deliberately has no display path out of the worker -- the seam Phase 2
    defined. A whole-clip test therefore has to be this loop: bind once, detect
    once, then read-and-swap each frame through the same decoder path and write
    the results with OpenCV's writer. This is the same process boundary as
    ``mode_smoke`` so the seal and the provider lock still hold.
    """
    mode = "video"

    preloaded = leaked_roots()
    if preloaded:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots already in sys.modules before the seal armed: "
            + ", ".join(preloaded),
        )

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

    models_dir = str(resolve_models_dir())
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

    output_path = os.path.join(ARTIFACTS_DIR, VIDEO_ARTIFACT)
    writer = None
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

        frame_count = int(media["frame_count"])
        fps = float(media["fps"])
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)

        # Probe the frame shape once so the writer is sized correctly; the
        # engine's own decoder is the one that fed detection, so reuse it.
        probe = engine._read_frame(0)  # noqa: SLF001
        height, width = probe.shape[:2]
        writer = cv2.VideoWriter(
            output_path,
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            return report(
                EXIT_ENGINE_ERROR,
                "ENGINE_ERROR",
                mode,
                "could not open VideoWriter for {}".format(output_path),
            )

        swapped_frames = 0
        for frame_number in range(frame_count):
            swapped = engine.swap(frame_number, source_path)
            swapped_frames += 1
            writer.write(swapped)
        writer.release()
        writer = None
        written = swapped_frames

        if written == 0:
            return report(
                EXIT_ENGINE_ERROR,
                "ENGINE_ERROR",
                mode,
                "video writer accepted no frames for {}".format(output_path),
            )

        provider = engine.context.models_processor.provider_name
        engine._release()  # noqa: SLF001 - the decoder holds an OS handle
    except SealBroken as exc:
        if writer is not None:
            writer.release()
        return report(EXIT_SEAL_BREACHED, "SEAL_BREACHED", mode, exc)
    except FileNotFoundError as exc:
        if writer is not None:
            writer.release()
        return report(EXIT_ASSET_MISSING, "ASSET_MISSING", mode, exc)
    except BaseException as exc:  # noqa: BLE001 - the runner reports, never raises
        if writer is not None:
            writer.release()
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

    return report(
        EXIT_CLEAN,
        "CLEAN",
        mode,
        "faces={} frames={} fps={:.3f} provider={} written={} shape={}x{} "
        "elapsed={:.1f}s artifact={}".format(
            len(cards),
            frame_count,
            fps,
            provider,
            written,
            width,
            height,
            time.monotonic() - started,
            VIDEO_ARTIFACT,
        ),
    )


def mode_source_cache():
    """Prove the source-embedding memo's identity and invalidation semantics.

    Phase 05.1 plan 01: ``Engine._source_embedding_store`` now memoises per
    ``(realpath, st_mtime_ns, st_size)``, cleared on ``load``. This mode drives
    the private helper directly -- no ``detect_faces``, no pixel output --
    through four probes and reports each as a ``key=value`` token on one line:

    * ``repeat``   two calls for one untouched file -> the identical object;
    * ``rewrite``  the file replaced in place (new bytes, new mtime) -> a
                   different object, proving the stat is taken every call;
    * ``rebind``   a second ``Engine.load`` -> a different object, proving the
                   memo does not survive a rebind across parameter tiers;
    * ``entries``  how many keys the memo holds at the end of the run.

    Timings, counts and booleans only -- no pixel data and no embedding values
    (T-02-10).
    """
    import shutil
    import tempfile
    import time

    mode = "source-cache"

    preloaded = leaked_roots()
    if preloaded:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots already in sys.modules before the seal armed: "
            + ", ".join(preloaded),
        )

    # Every asset resolved before anything expensive happens, exactly as the
    # neighbouring modes do: a missing one is ASSET_MISSING, never a skip.
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

    models_dir = str(resolve_models_dir())
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

    started = time.monotonic()

    try:
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

    tmp_dir = tempfile.mkdtemp(prefix="visoswap-src-cache-")
    try:
        try:
            import cv2
        except ImportError as exc:
            return report(EXIT_DEPS_MISSING, "DEPS_MISSING", mode, exc)

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
            engine.load(video_path)

            # Probe 1 -- repeat: one untouched file embedded once.
            first = engine._source_embedding_store(source_path)  # noqa: SLF001
            second = engine._source_embedding_store(source_path)  # noqa: SLF001
            repeat = "same" if second is first else "different"

            # Probe 2 -- rewrite: the same path carrying different bytes. The
            # perturbation stays in a far corner of the image so the picture
            # remains one detectable face; the explicit utime bump guarantees
            # st_mtime_ns moves even on a coarse-timestamped filesystem, which
            # is precisely the replacement-in-place case the stat-in-key rule
            # exists for.
            temp_source = os.path.join(tmp_dir, "probe_source.jpg")
            shutil.copyfile(source_path, temp_source)
            rewritten_first = engine._source_embedding_store(temp_source)  # noqa: SLF001
            image = cv2.imread(temp_source)
            if image is None:
                raise RuntimeError(
                    "the copied probe image {} did not decode".format(temp_source)
                )
            image[:8, :8, :] = np.clip(
                image[:8, :8, :].astype(np.int16) - 60, 0, 255
            ).astype(np.uint8)
            if not cv2.imwrite(temp_source, image):
                raise RuntimeError("could not rewrite {}".format(temp_source))
            stamp = os.stat(temp_source)
            os.utime(temp_source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
            rewritten_second = engine._source_embedding_store(temp_source)  # noqa: SLF001
            rewrite = (
                "different" if rewritten_second is not rewritten_first else "same"
            )

            # Probe 3 -- rebind: the memo must not survive Engine.load().
            pre_rebind = engine._source_embedding_store(source_path)  # noqa: SLF001
            engine.load(video_path)
            post_rebind = engine._source_embedding_store(source_path)  # noqa: SLF001
            rebind = "different" if post_rebind is not pre_rebind else "same"

            entries = len(engine._source_store_cache)  # noqa: SLF001
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
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    leaked = leaked_roots()
    if leaked:
        return report(
            EXIT_SEAL_BREACHED,
            "SEAL_BREACHED",
            mode,
            "sealed roots in sys.modules after probing: " + ", ".join(leaked),
        )

    return report(
        EXIT_CLEAN,
        "CLEAN",
        mode,
        "repeat={} rewrite={} rebind={} entries={} provider={} elapsed={:.1f}s".format(
            repeat, rewrite, rebind, entries, provider, time.monotonic() - started
        ),
    )


def swap_only_baseline_path():
    return os.path.join(ARTIFACTS_DIR, SWAPPED_FRAME_ARTIFACT)


def regenerate_swap_only_baseline():
    """Rebuild the swap-only frame by running this file's own smoke mode.

    Returns ``(exit_code, output)`` from the child.

    A fresh subprocess rather than an in-process call to :func:`mode_smoke`. The
    two are equivalent on paper -- same fixture resolution, same settings, same
    seal -- but calling it in-process would arm the seal twice, load both model
    sets into one CUDA context and leave the smoke run's engine holding a decoder
    handle while the editor run opened its own. Seven seconds is a cheap price
    for a baseline built by exactly the command that produced the original.
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-B", os.path.abspath(__file__), "--smoke"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    output = " ".join((proc.stdout or "").split() + (proc.stderr or "").split())
    return proc.returncode, output


def ensure_swap_only_baseline(mode):
    """Guarantee a non-empty swap-only frame on disk. -> ``(state, failure)``.

    ``state`` is ``'reused'`` or ``'regenerated'``; ``failure`` is ``None`` or an
    already-formatted report to return.

    ``tests/artifacts/`` is gitignored by plan 02-02, so in the worktree that
    produced it the baseline is sitting there and in a clean checkout it is not.
    Neither case may be guessed at. **Zero bytes is treated as missing**, and
    that is the sharper half: an empty file passes ``isfile``, and ``cv2.imread``
    answers a zero-byte PNG with ``None`` rather than an error, so a naive
    existence check turns a truncated baseline into a comparison against
    nothing.
    """
    path = swap_only_baseline_path()
    try:
        present = os.path.getsize(path) > 0
    except OSError:
        present = False
    if present:
        return "reused", None

    code, output = regenerate_swap_only_baseline()
    if code != EXIT_CLEAN:
        return None, report(
            code,
            EXIT_LABELS.get(code, "ENGINE_ERROR"),
            mode,
            "the swap-only baseline {} is absent or empty and regenerating it "
            "with --smoke exited {}: {}".format(path, code, output),
        )
    try:
        rebuilt = os.path.getsize(path) > 0
    except OSError:
        rebuilt = False
    if not rebuilt:
        return None, report(
            EXIT_ASSET_MISSING,
            "ASSET_MISSING",
            mode,
            "--smoke exited CLEAN but did not leave a non-empty {}: {}".format(
                path, output
            ),
        )
    return "regenerated", None


def mode_faceedit():
    """Swap a face, then run LivePortrait over it, and prove the frame moved.

    The first execution of the face-editor path in this project. Roadmap
    criterion 4, as amended by ``02-DECISION-deferred-paths.md``: LivePortrait is
    exercised for real; DFM and CLIPseg stay import-proven by decision, because
    neither one's weights exist on this machine.

    Two gates have to be open, and they are deliberately different in kind. The
    per-face ``FaceEditorEnableToggle`` is a settings key and rides in with the
    project tier. ``EngineContext.edit_faces_enabled`` is **not** a setting -- it
    is one of the two plain fields that replaced a Qt toggle button in Phase 1,
    read at three sites in ``frame_worker``. Setting it here is what proves that
    substitution was faithful rather than merely type-correct.
    """
    mode = "faceedit"

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

    models_dir = str(resolve_models_dir())
    if not os.path.isdir(models_dir):
        return report(
            EXIT_ASSET_MISSING,
            "ASSET_MISSING",
            mode,
            "model_assets not reachable at {} -- run tools/link_model_assets.py. "
            "Without it ModelsProcessor constructs a silently degraded "
            "processor rather than raising.".format(models_dir),
        )

    # The lip array is opened by ``FaceEditors.__init__``, which swallows
    # FileNotFoundError and leaves the array as None. Checking the file here as
    # well means a missing one is reported as the asset problem it is, before a
    # model load, rather than as a null dereference nine hundred lines into the
    # frame worker.
    lip_array_path = os.path.join(models_dir, "liveportrait_onnx", "lip_array.pkl")
    if not os.path.isfile(lip_array_path):
        return report(
            EXIT_ASSET_MISSING,
            "ASSET_MISSING",
            mode,
            "{} not readable (cwd={}). FaceEditors.__init__ swallows this and "
            "leaves lp_lip_array as None, so without this check the engine "
            "constructs successfully with the lip retarget silently "
            "disabled.".format(lip_array_path, os.getcwd()),
        )

    # Before anything expensive, and before the seal: the comparison this mode
    # exists to make is meaningless against an absent or truncated baseline.
    baseline_state, failure = ensure_swap_only_baseline(mode)
    if failure is not None:
        return failure
    baseline_path = swap_only_baseline_path()

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
        baseline = cv2.imread(baseline_path)
        if baseline is None:
            return report(
                EXIT_ASSET_MISSING,
                "ASSET_MISSING",
                mode,
                "the swap-only baseline {} ({}) did not decode".format(
                    baseline_path, baseline_state
                ),
            )

        control = apply_overrides(
            dict(settings.get("global", {})), SMOKE_GLOBAL_OVERRIDES, "global"
        )
        parameters = apply_overrides(
            dict(settings.get("project", {})), FACE_EDIT_PROJECT_OVERRIDES, "project"
        )

        engine = Engine(
            device="cuda", global_settings=control, project_settings=parameters
        )
        # Gate two: the field that replaced the Qt toggle button.
        engine.context.edit_faces_enabled = True

        lip_array = engine.context.models_processor.lp_lip_array
        if lip_array is None:
            return report(
                EXIT_ASSET_MISSING,
                "ASSET_MISSING",
                mode,
                "lp_lip_array is None after constructing the engine, though {} "
                "exists (cwd={}). models_dir is relative until Phase 4, so this "
                "means the processor resolved it somewhere else.".format(
                    lip_array_path, os.getcwd()
                ),
            )
        lip_array_shape = "x".join(str(n) for n in getattr(lip_array, "shape", ()))
        if not getattr(lip_array, "size", 0):
            return report(
                EXIT_ASSET_MISSING,
                "ASSET_MISSING",
                mode,
                "lp_lip_array loaded from {} but is empty (shape={})".format(
                    lip_array_path, lip_array_shape or "?"
                ),
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
        source_frame = engine._read_frame(frame_number)  # noqa: SLF001
        edited = engine.swap(frame_number, source_path)

        if source_frame.shape != edited.shape:
            return report(
                EXIT_ENGINE_ERROR,
                "ENGINE_ERROR",
                mode,
                "edited frame shape {} does not match the decoded frame's "
                "{}".format(edited.shape, source_frame.shape),
            )
        if baseline.shape != edited.shape:
            return report(
                EXIT_ENGINE_ERROR,
                "ENGINE_ERROR",
                mode,
                "the swap-only baseline {} is {} but the edited frame is {} -- "
                "the two runs did not see the same frame".format(
                    baseline_path,
                    "x".join(str(n) for n in baseline.shape),
                    "x".join(str(n) for n in edited.shape),
                ),
            )

        diff_vs_swap_only = int(
            np.count_nonzero(np.any(baseline != edited, axis=-1))
        )
        diff_vs_source = int(
            np.count_nonzero(np.any(source_frame != edited, axis=-1))
        )

        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        frame_path = os.path.join(ARTIFACTS_DIR, FACE_EDIT_FRAME_ARTIFACT)
        if not cv2.imwrite(frame_path, edited):
            return report(
                EXIT_ENGINE_ERROR, "ENGINE_ERROR", mode, "could not write {}".format(frame_path)
            )

        provider = engine.context.models_processor.provider_name
        editor_model = parameters["FaceEditorTypeSelection"]
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
            "sealed roots in sys.modules after editing: " + ", ".join(leaked),
        )

    return report(
        EXIT_CLEAN,
        "CLEAN",
        mode,
        "faces={} frames={} provider={} editor_model={} lip_array=populated "
        "lip_array_shape={} control={}={} baseline={} input_shape={} "
        "output_shape={} diff_vs_swap_only={} diff_vs_source={} elapsed={:.1f}s "
        "artifacts={} reachable_before_seal={}".format(
            len(cards),
            media["frame_count"],
            provider,
            editor_model,
            lip_array_shape or "?",
            FACE_EDIT_CONTROL_KEY,
            FACE_EDIT_CONTROL_VALUE,
            baseline_state,
            "x".join(str(n) for n in source_frame.shape),
            "x".join(str(n) for n in edited.shape),
            diff_vs_swap_only,
            diff_vs_source,
            time.monotonic() - started,
            FACE_EDIT_FRAME_ARTIFACT,
            ",".join(
                "{}={}".format(root, "yes" if ok else "no")
                for root, ok in sorted(reachable.items())
            ),
        ),
    )


def mode_no_visomaster():
    """Run the smoke swap with an open guard armed against every VisoMaster root.

    Plan 04-04 Task 3 (closing ENGINE-01 clause 2): a grep proves no *literal*
    names a VisoMaster path; only a guard watching real opens proves no *resolved*
    path reaches one. The smoke run completes under the guard, and the report
    carries ``guard_opens`` so a caller can assert the guard observed real opens
    rather than none.
    """
    from _open_guard import VisoMasterOpenGuard

    guard = VisoMasterOpenGuard()
    return mode_smoke(guard=guard)


USAGE = (
    "usage: _engine_runner.py "
    "(--selftest | --import DOTTED_NAME | --smoke | --faceedit | --video "
    "| --source-cache | --no-visomaster)"
)


def main(argv):
    if argv == ["--selftest"]:
        return mode_selftest()
    if argv == ["--smoke"]:
        return mode_smoke()
    if argv == ["--faceedit"]:
        return mode_faceedit()
    if argv == ["--video"]:
        return mode_video()
    if argv == ["--source-cache"]:
        return mode_source_cache()
    if argv == ["--no-visomaster"]:
        return mode_no_visomaster()
    if len(argv) == 2 and argv[0] == "--import":
        return mode_import(argv[1])
    return report(EXIT_ENGINE_ERROR, "ENGINE_ERROR", "-", USAGE)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
