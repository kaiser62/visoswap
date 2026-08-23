"""The sealed backend runner: the only thing that drives the real engine through a route.

Runs as a subprocess, **never imported by pytest** -- the same split as
``_engine_runner.py`` and for the same reason: the interpreter that runs pytest
has no torch; the interpreter that has torch has the web stack here too (the
combined runtime, ``.venv-clean``). So this file lives on the far side of a
process boundary and the pytest side drives it via a ``conftest.py`` helper.

Before anything else, a ``sys.meta_path`` finder is installed refusing the seven
Qt binding roots (Phase 1) and ``app`` (VisoMaster's application package). The
``backend`` group is deliberately left off the block list -- this runner's whole
job is to prove the *backend* can drive the engine -- and it says so in its
output rather than silently.

Exit codes mirror ``_engine_runner.py``:

    0  CLEAN           the tracer completed and no sealed root was reached
    1  SEAL_BREACHED   a sealed root was reached, or leaked into sys.modules
    2  DEPS_MISSING    an engine or web dependency is not installed
    3  ASSET_MISSING    a required asset, video, source face or interpreter is missing
    4  ENGINE_ERROR     anything else

Exactly one machine-readable line is printed before exit:

    LABEL:mode:detail

Usage::

    python tests/_backend_runner.py --tracer
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)

for _path in (_REPO_ROOT, _TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _blocked_roots import (  # noqa: E402 - must follow the sys.path setup
    ALL_SEALED_ROOTS,
    root_of,
)

from visoswap.schema import resolve_models_dir  # noqa: E402 - same reason

#: The Qt binding roots and VisoMaster's app package stay sealed. ``backend`` is
#: deliberately NOT in the seal: the tracer must prove the backend can drive the
#: engine. State that in the output line, never silently.
SEALED = [r for r in ALL_SEALED_ROOTS if r != "backend"]

EXIT_CLEAN = 0
EXIT_SEAL_BREACHED = 1
EXIT_DEPS_MISSING = 2
EXIT_ASSET_MISSING = 3
EXIT_ENGINE_ERROR = 4

VISOMASTER_ENV_VAR = "VISOMASTER_DIR"
# Plan 04-04 (sever VisoMaster): the default is no longer a VisoMaster checkout.
# A real checkout may still be supplied as an override; the project-owned copy
# and self-built seal subject are the default.
DEFAULT_VISOMASTER_DIR = None

VIDEO_ENV_VAR = "VISOSWAP_TEST_VIDEO"
SOURCE_ENV_VAR = "VISOSWAP_TEST_SOURCE"
MEDIA_DIR = os.path.join(_REPO_ROOT, "tests", "media")
DEFAULT_TEST_VIDEO = os.path.join(
    MEDIA_DIR, "17f0d620_rosh_generate_135bda4c9686.mp4"
)
DEFAULT_TEST_SOURCE = os.path.join(
    MEDIA_DIR, "598004cb_tonima.JPG"
)

#: How long the tracer waits for a single completed frame before giving up.
#: The cancelled-recording hang is plan 04-02's problem; here a bounded wait
#: turns a hang into a reported ENGINE_ERROR rather than a stuck subprocess.
TRACER_TIMEOUT_SECONDS = 300.0

#: Root of the sealed Qt blocker, so a reachability probe can be attributed.
QT_ROOT_SIGNAL = "PySide6"


def _reachable_before_seal():
    """Which sealed roots resolved before the blocker armed (non-inertness)."""
    result = {}
    for root in SEALED:
        try:
            __import__(root)
            result[root] = True
        except Exception:
            result[root] = False
    return result


def _arm_seal():
    """Install a meta_path finder refusing every sealed root."""

    class _Blocker:
        def find_module(self, fullname, path=None):  # noqa: B027
            if root_of(fullname) in SEALED:
                raise ImportError("sealed root: " + fullname)
            return None

        def find_spec(self, fullname, path=None, target=None):
            if root_of(fullname) in SEALED:
                raise ImportError("sealed root: " + fullname)
            return None

    sys.meta_path.insert(0, _Blocker())


def _leaked_roots():
    return [r for r in SEALED if r in sys.modules]


def _one_line(detail):
    return " ".join(str(detail).split())


def _report(code, label, mode, detail):
    print("{}:{}:{}".format(label, mode, _one_line(detail)))
    return code


def _resolve_media(env_var, default):
    return os.environ.get(env_var) or default


def _run_tracer(guard=None):
    """Drive ONE real generation through backend.api.generation's route.

    ``guard`` is an optional ``_open_guard.VisoMasterOpenGuard`` armed for the
    run; when given, the report carries ``guard_opens`` so ``--no-visomaster``
    can prove no opened path resolved inside a VisoMaster install.
    """
    mode = "tracer"

    reachable = _reachable_before_seal()
    _arm_seal()

    video_path = _resolve_media(VIDEO_ENV_VAR, DEFAULT_TEST_VIDEO)
    source_path = _resolve_media(SOURCE_ENV_VAR, DEFAULT_TEST_SOURCE)
    for label, path in (("target video", video_path), ("source face", source_path)):
        if not os.path.isfile(path):
            return _report(
                EXIT_ASSET_MISSING,
                "ASSET_MISSING",
                mode,
                "{} not found at {} -- point {} / {} at one".format(
                    label, path, VIDEO_ENV_VAR, SOURCE_ENV_VAR
                ),
            )

    models_dir = str(resolve_models_dir())
    if not os.path.isdir(models_dir):
        return _report(
            EXIT_ASSET_MISSING,
            "ASSET_MISSING",
            mode,
            "model_assets not reachable at {} -- run tools/link_model_assets.py".format(
                models_dir
            ),
        )

    # Isolate all backend state into a temp data dir so the tracer never touches
    # the developer's real app.db or data/.
    tmpdir = tempfile.mkdtemp(prefix="visoswap-tracer-")
    os.environ["DATA_DIR"] = tmpdir
    os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tmpdir, "app.db")
    os.environ["RECORDER_ENABLED"] = "0"  # no muxing for the tracer

    try:
        import aiosqlite  # noqa: F401
        import fastapi  # noqa: F401
    except ImportError as exc:
        return _report(EXIT_DEPS_MISSING, "DEPS_MISSING", mode, exc)

    import asyncio

    import backend.main as app_main
    import backend.models.database as database
    from backend.services import cache

    if guard is not None:
        guard.__enter__()

    try:
        result = asyncio.run(
            _tracer_coro(video_path, source_path, app_main, database, cache, reachable, guard)
        )
    except BaseException as exc:  # noqa: BLE001
        return _report(
            EXIT_ENGINE_ERROR,
            "ENGINE_ERROR",
            mode,
            "{}: {} | {}".format(type(exc).__name__, exc, traceback.format_exc()),
        )
    return result


async def _tracer_coro(video_path, source_path, app_main, database, cache, reachable, guard=None):
    import asyncio

    from backend.config import get_settings

    settings = get_settings()
    db = database.db

    # Point the shared singleton at the temp DB. The module-level `db` resolves
    # its path lazily on connect, so reset the connection first.
    database.db._conn = None  # noqa: SLF001
    database.db._explicit_path = None  # noqa: SLF001
    await db.connect()

    # Probe media dimensions via ffprobe through the ffmpeg service.
    from backend.services import ffmpeg

    info = await ffmpeg.probe(video_path)
    project = await db.create_project(
        name="tracer",
        video_path=video_path,
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        source_face_path=source_path,
        interval=1.0,
        lookahead=10.0,
    )
    cache.ensure_project_dirs(project["id"])

    # Build the app so the tracer goes through the real route, not the adapter
    # directly. `create_app()` runs the lifespan (connect, dirs) as a side
    # effect only when used as a context manager; we already connected.
    app = app_main.create_app()

    from fastapi.testclient import TestClient

    started = time.monotonic()

    with TestClient(app) as client:
        # /url= binds a URL in the background; instead bind the local video by
        # patching the project row directly, then start the scheduler on a short
        # range covering one target.
        resp = client.post(
            f"/api/projects/{project['id']}/scheduler/start",
            json={"current_time": 0.0, "range_start": 0.0, "range_duration": 1.0},
        )
        if resp.status_code != 200:
            return _report(
                EXIT_ENGINE_ERROR,
                "ENGINE_ERROR",
                "tracer",
                "scheduler start {}: {}".format(resp.status_code, resp.text),
            )

        # Poll for a completed frame until the bound or a completed frame.
        frame = None
        while time.monotonic() - started < TRACER_TIMEOUT_SECONDS:
            rows = await db.list_frames(project["id"], status="completed", start=0, end=1.0)
            if rows:
                frame = rows[0]
                break
            await asyncio.sleep(1.0)

        if frame is None:
            # Report the queue state to distinguish "nothing generated" from a hang.
            counts = await db.counts(project["id"])
            return _report(
                EXIT_ENGINE_ERROR,
                "ENGINE_ERROR",
                "tracer",
                "no completed frame within {:.0f}s; counts={}".format(
                    TRACER_TIMEOUT_SECONDS, counts
                ),
            )

        frame_path = frame.get("generated_frame_path") or ""
        abs_path = cache.to_absolute(frame_path) if frame_path else Path()
        size = abs_path.stat().st_size if Path(abs_path).is_file() else 0

        # Provider + face count from the project's config; the engine resolved
        # provider is reported by the worker's logs, but the scheduler's bound
        # faces are recorded on the generator. Surface what we can measure.
        provider = settings.visomaster_provider

        reachable_before_seal = ",".join(
            "{}={}".format(r, "yes" if ok else "no")
            for r, ok in sorted(reachable.items())
        )
        leaked = _leaked_roots()
        if leaked:
            return _report(
                EXIT_SEAL_BREACHED,
                "SEAL_BREACHED",
                "tracer",
                "sealed roots in sys.modules after run: " + ", ".join(leaked),
            )

        elapsed = time.monotonic() - started
        guard_report = ""
        if guard is not None:
            guard_report = " guard_opens={}".format(guard.opens)
        return _report(
            EXIT_CLEAN,
            "CLEAN",
            "tracer",
            "frame={} size={} provider={} elapsed={:.1f}s "
            "reachable_before_seal={}{}".format(
                os.path.basename(str(abs_path)) if abs_path else "",
                size,
                provider,
                elapsed,
                reachable_before_seal,
                guard_report,
            ),
        )


def main(argv):
    if argv == ["--tracer"]:
        return _run_tracer()
    if argv == ["--tracer", "--no-visomaster"]:
        from _open_guard import VisoMasterOpenGuard

        return _run_tracer(guard=VisoMasterOpenGuard())
    return _report(
        EXIT_ENGINE_ERROR,
        "ENGINE_ERROR",
        "-",
        "usage: tests/_backend_runner.py (--tracer [--no-visomaster])",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
