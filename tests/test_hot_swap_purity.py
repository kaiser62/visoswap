"""Stop -> change face -> start on one project leaves nothing from the old face.

D-13 makes face hot-swap first-class, and the purity guarantee is supposed to
come from the per-start wipe in ``start_scheduler``: frame rows deleted, cached
frame files deleted, recorder output deleted, then a fresh generator built from
a freshly-read project row so the new ``source_face_path`` is honored. This
module proves that whole vertical through the real API -- real routes, real
database, real scheduler, real worker pool, real frame files on disk -- with
only the engine itself substituted at the ``build_generator`` seam, so it runs
on the plain developer interpreter with no GPU.

The substitution patches the symbol each call site bound: the fail-fast
validation in ``backend.api.generation`` and the frame producers in
``backend.workers.generation_worker``. The stub's ``generate_at`` writes bytes
that encode which ``source_face_path`` its bound project carried, so "which
face made this frame" is readable straight off the disk afterwards.

The recorder is disabled (``RECORDER_ENABLED=false``) and the first run's
working ``output.mp4`` / ``output.mp4.part`` are seeded by hand instead: a live
fragmented recording races the assertion (the second start legitimately begins
writing a new ``.part``), whereas seeded artefacts prove the wipe itself,
deterministically. Export/copy behaviour is covered by ``tests/test_recorder``.
"""

from __future__ import annotations

import io
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.config import get_settings
from backend.main import create_app
from backend.models.database import db
from backend.services import cache, facestore, recorder
from backend.services.generator import FrameGenerator, GenerationResult

FPS = 10.0
DURATION = 45.0  # seconds; wide enough for an immediate tier and a lookahead tier
SIZE = (160, 120)
PLAYHEAD = 12.0  # partway into the clip, between grid points


class FaceMarkerGenerator(FrameGenerator):
    """Stands in for ``EngineFrameGenerator`` at the seam.

    ``generate_at`` writes a one-"pixel" payload whose bytes name the
    ``source_face_path`` of the project bound at call time -- the exact value
    D-13 says must flip between runs and must never blend.
    """

    name = "face-marker-stub"
    decodes_own_source = True

    def __init__(self, project: dict) -> None:
        self._bound: dict | None = dict(project) if project else None

    async def health(self) -> dict:
        return {"backend": self.name}

    async def generate(self, image_bytes: bytes, filename: str) -> GenerationResult:
        # Never reached: `decodes_own_source` routes workers to generate_at,
        # exactly as with the real engine backend.
        raise NotImplementedError("face-marker stub decodes its bound video")

    async def validate(self) -> None:
        if not self._bound or not self._bound.get("video_path"):
            raise ValueError("stub generator has no bound video")

    async def bind(self, project: dict) -> None:
        # Deliberately WITHOUT EngineFrameGenerator's same-id early return: a
        # fresh instance is built per run here, mirroring the real lifecycle,
        # and generate_at reads the bound row at call time anyway.
        self._bound = dict(project)

    async def unbind(self) -> None:
        self._bound = None

    async def close(self) -> None:
        self._bound = None

    async def generate_at(self, timestamp: float, filename: str) -> GenerationResult:
        if not self._bound:
            raise RuntimeError("stub generator is not bound")
        face = self._bound.get("source_face_path")
        if not face:
            raise ValueError("bound project has no source_face_path")
        payload = f"FACE|{face}|{timestamp:.3f}".encode("utf-8")
        return GenerationResult(payload, None, 0.001, filename)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """The real app against a throwaway database and throwaway disk state.

    Built exactly as ``tests/test_api_settings.py`` builds it (temp
    DATABASE_URL, ``db._conn = None``, ``MODELS_VERIFY_MODE=fast``), plus the
    ``tests/test_recorder.py`` discipline of repointing DATA_DIR and OUTPUT_DIR
    into ``tmp_path`` *before* the settings cache clears, so nothing this test
    writes can land in the repository.
    """
    monkeypatch.setenv("MODELS_VERIFY_MODE", "fast")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///{}".format(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("RECORDER_ENABLED", "false")
    get_settings.cache_clear()
    db._conn = None

    built: list[tuple[FaceMarkerGenerator, dict]] = []

    def factory(project: dict, timeout: float, worker_index: int = 0):
        # Snapshot the project row at construction: the worker pool unbinds
        # every generator on shutdown, so by assertion time `_bound` is None
        # and the freshness claim has to be judged from what each instance
        # was BUILT against (the disk markers prove what it generated with).
        generator = FaceMarkerGenerator(project)
        built.append((generator, dict(project)))
        return generator

    from backend.api import generation as generation_api
    from backend.workers import generation_worker as worker_module

    # Both call sites bound ``build_generator`` into their own namespaces at
    # import time; patch each where it is looked up.
    monkeypatch.setattr(generation_api, "build_generator", factory)
    monkeypatch.setattr(worker_module, "build_generator", factory)

    app = create_app()
    with TestClient(app) as c:
        yield c, built
    get_settings.cache_clear()


def _clip(tmp_path) -> bytes:
    """A small real mp4, built the way ``tests/test_recorder.py`` builds hers."""
    dest = tmp_path / "clip.mp4"
    cmd = [
        get_settings().ffmpeg_bin, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"testsrc=size={SIZE[0]}x{SIZE[1]}:rate={FPS:g}:duration={DURATION:g}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={DURATION:g}",
        "-c:a", "aac",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest.read_bytes()


def _library_face(c, colour) -> str:
    """Upload a face into the global store and activate it on the project.

    Since plan 05.1-02 the activation endpoint is the only writer of
    ``source_face_path`` (T-05.1-02-07), so the hot-swap cycle goes through it
    exactly as the product does.
    """
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), colour).save(buf, "PNG")
    uploaded = c.post(
        "/api/faces", files={"file": ("face.png", buf.getvalue(), "image/png")}
    )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()["face_id"]


def _wait_for_completed(client, project_id: str, at_least: int) -> dict:
    deadline = time.monotonic() + 90.0
    last = {}
    while time.monotonic() < deadline:
        last = client.get(
            f"/api/projects/{project_id}/generation/status"
        ).json()
        if last["counts"]["completed"] >= at_least:
            return last
        time.sleep(0.05)
    raise AssertionError(
        f"no {at_least} completed frame(s) after 90s; last status: {last}"
    )


def _generated_payloads(project_id: str) -> list[bytes]:
    base = cache.generated_dir(project_id)
    return [
        path.read_bytes()
        for path in sorted(base.iterdir())
        if path.is_file() and not path.name.endswith(".part")
    ]


def _row_created_at(app_db_path, project_id: str) -> dict[float, float]:
    """`timestamp -> created_at` straight off the app database.

    The ``/frames`` API deliberately serves timeline metadata only and does not
    expose ``created_at``, so the no-stale-rows assertion reads the column where
    it lives -- synchronously over the throwaway sqlite file, which keeps this
    free of any cross-event-loop aiosqlite reuse.
    """
    import sqlite3

    connection = sqlite3.connect(app_db_path)
    try:
        rows = connection.execute(
            "SELECT timestamp, created_at FROM frames WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    finally:
        connection.close()
    return {float(ts): float(created) for ts, created in rows}


def test_stop_change_face_start_leaves_nothing_from_the_previous_face(
    client, tmp_path
):
    c, built = client
    project_id = c.post(
        "/api/projects", json={"name": "hot swap", "generation_mode": "interval"}
    ).json()["id"]

    upload = c.post(
        f"/api/projects/{project_id}/source",
        files={"file": ("clip.mp4", _clip(tmp_path), "video/mp4")},
    )
    assert upload.status_code == 200, upload.text

    face_one = _library_face(c, (200, 40, 40))
    face_two = _library_face(c, (40, 200, 40))

    # ---- run one: face one -------------------------------------------------
    activated = c.post(
        f"/api/projects/{project_id}/face", json={"face_id": face_one}
    )
    assert activated.status_code == 200, activated.text
    bound_one = str(facestore.face_path(face_one))

    started = c.post(f"/api/projects/{project_id}/scheduler/start", json={})
    assert started.status_code == 200, started.text
    _wait_for_completed(c, project_id, 1)
    stopped_at = time.time()
    stop = c.post(f"/api/projects/{project_id}/scheduler/stop")
    assert stop.status_code == 200, stop.text
    assert c.get(
        f"/api/projects/{project_id}/generation/status"
    ).json()["running"] is False

    first_run_rows = c.get(
        f"/api/projects/{project_id}/frames"
    ).json()["frames"]
    assert first_run_rows, "run one produced no frame rows to forget"

    # Seed what a stopped first run leaves behind: the working recording.
    recorder.output_path(project_id).write_bytes(b"stale first-run mp4")
    recorder.partial_path(project_id).write_bytes(b"stale first-run partial")
    assert recorder.output_path(project_id).is_file()
    assert recorder.partial_path(project_id).is_file()

    built_before_restart = len(built)

    # ---- run two: face two, playhead partway into the clip ------------------
    activated = c.post(
        f"/api/projects/{project_id}/face", json={"face_id": face_two}
    )
    assert activated.status_code == 200, activated.text
    bound_two = str(facestore.face_path(face_two))

    restarted = c.post(
        f"/api/projects/{project_id}/scheduler/start",
        json={"current_time": PLAYHEAD},
    )
    assert restarted.status_code == 200, restarted.text
    _wait_for_completed(c, project_id, 1)
    c.post(f"/api/projects/{project_id}/scheduler/stop")

    # ---- the five purity assertions ----------------------------------------
    # 1. No first-face frame file survives on disk.
    survivors = [
        payload
        for payload in _generated_payloads(project_id)
        if payload.startswith(f"FACE|{bound_one}|".encode("utf-8"))
    ]
    assert not survivors, (
        f"{len(survivors)} frame file(s) made with the first face survived the "
        "stop -> change -> start cycle"
    )

    # 2. No frame row created before the stop survives the second start.
    created_at = _row_created_at(tmp_path / "app.db", project_id)
    assert created_at, "run two produced no frame rows"
    stale = [ts for ts, created in created_at.items() if created < stopped_at]
    assert not stale, (
        f"{len(stale)} frame row(s) predate the first stop: {stale}"
    )

    # 3. Neither working recording file from the first run survives.
    assert not recorder.output_path(project_id).exists(), (
        "the first run's output.mp4 survived the second start"
    )
    assert not recorder.partial_path(project_id).exists(), (
        "the first run's output.mp4.part survived the second start"
    )

    # 4. Every second-run frame carries the second face's marker.
    payloads = _generated_payloads(project_id)
    assert payloads, "run two wrote no generated frame files"
    wrong = [
        payload for payload in payloads
        if not payload.startswith(f"FACE|{bound_two}|".encode("utf-8"))
    ]
    assert not wrong, (
        f"{len(wrong)} frame(s) were not made with the second face"
    )

    # 5. A generator built for the second run reports the second face -- the
    #    freshness claim in D-13, exactly what a same-id early return in a
    #    reused generator would otherwise defeat. Generators are constructed
    #    from a freshly-read project row; every one built after the second
    #    start must carry the second source_face_path.
    second_run_generators = [project for _gen, project in built[built_before_restart:]]
    assert second_run_generators, (
        "no generator was constructed for the second run"
    )
    stale_bindings = [
        project.get("source_face_path") for project in second_run_generators
        if project.get("source_face_path") != bound_two
    ]
    assert not stale_bindings, (
        f"{len(stale_bindings)} generator(s) of the second run were built "
        f"against a stale source_face_path: {stale_bindings}"
    )

    # ---- closer-frames-first ordering (benchmark addendum item 2) ----------
    # Run two started with the playhead partway in. The scheduler plans its
    # window forward from the playhead: the nearest grid point at-or-after it
    # lands in the immediate tier, later points fall into the lookahead tier,
    # so the minimum priority must sit on the frame nearest the playhead --
    # the first one the workers claim. (This scheduler never grants its best
    # priority to a frame *behind* the playhead: `priority_for` ranks past
    # frames background, useful only on rewatch.)
    planned = [
        row for row in
        c.get(f"/api/projects/{project_id}/frames").json()["frames"]
        if row["status"] == "completed"
    ]
    min_priority = min(row["priority"] for row in planned)
    leaders = sorted(
        row["timestamp"] for row in planned if row["priority"] == min_priority
    )
    grid_step = 5.0  # the project's default interval
    nearest_forward = (
        -(-PLAYHEAD // grid_step) * grid_step  # ceil to the grid
    )
    assert abs(leaders[0] - nearest_forward) < 1e-6, (
        f"minimum priority {min_priority} sits at {leaders}, expected the "
        f"grid point nearest the playhead ({PLAYHEAD}s -> {nearest_forward}s)"
    )
    behind = [
        row for row in planned
        if row["timestamp"] < PLAYHEAD - 0.05
        and row["priority"] == min_priority
    ]
    assert not behind, (
        "a frame behind the playhead shares the best priority: "
        f"{[row['timestamp'] for row in behind]}"
    )


def _worker_tasks(project_id: str) -> list:
    """The live worker tasks. Held as objects, not ids -- a dead task's id gets
    handed straight back out to its replacement."""
    from backend.services.scheduler import registry

    scheduler = registry.peek(project_id)
    return list(scheduler._workers) if scheduler is not None else []


def test_starting_a_running_scheduler_restarts_it_rather_than_racing_it(
    client, tmp_path
):
    """Start is a restart, and a restart deletes the previous run's state.

    Left running, the old scheduler would carry on against a queue and a cache
    that had just been emptied under it -- and its recorder would still hold
    `output.mp4.part` open, which is the `WinError 32` the endpoint used to
    raise. Starting must take the previous run down first.
    """
    c, built = client
    project_id = c.post("/api/projects", json={"name": "restart"}).json()["id"]
    upload = c.post(
        f"/api/projects/{project_id}/source",
        files={"file": ("clip.mp4", _clip(tmp_path), "video/mp4")},
    )
    assert upload.status_code == 200, upload.text
    face = _library_face(c, (200, 40, 40))
    assert c.post(
        f"/api/projects/{project_id}/face", json={"face_id": face}
    ).status_code == 200

    assert c.post(
        f"/api/projects/{project_id}/scheduler/start", json={}
    ).status_code == 200
    _wait_for_completed(c, project_id, 1)
    first_run_rows = c.get(f"/api/projects/{project_id}/frames").json()["frames"]
    assert first_run_rows, "run one planned nothing to be forgotten"

    # Seed the artefact the old code tripped over, then start again WITHOUT
    # stopping -- the exact sequence from the report.
    recorder.partial_path(project_id).write_bytes(b"stale partial")
    workers_before = _worker_tasks(project_id)
    assert workers_before, "run one has no worker pool to be replaced"
    restarted = c.post(f"/api/projects/{project_id}/scheduler/start", json={})
    assert restarted.status_code == 200, restarted.text

    assert not recorder.partial_path(project_id).exists(), (
        "the previous run's partial recording survived the restart"
    )
    _wait_for_completed(c, project_id, 1)
    # The pool the second start is running on must not be the first run's. That
    # is what separates a real restart from the old early return, which cleared
    # the state and then left the previous run going against the wreckage.
    assert all(task.done() for task in workers_before), (
        "the previous run's workers kept going -- their queue and cache were "
        "deleted underneath them"
    )
    assert _worker_tasks(project_id), "the restart left no worker pool running"
    assert c.post(
        f"/api/projects/{project_id}/scheduler/stop"
    ).status_code == 200


def test_releasing_deletes_what_it_can_and_names_what_it_cannot(client, tmp_path):
    """The release control's contract: sweep, then report the stragglers.

    Whatever is left is held by a process this app cannot reach into, so the
    only useful answer is to name the file and let the user close whatever has
    it open."""
    c, _built = client
    project_id = c.post("/api/projects", json={"name": "release"}).json()["id"]
    recorder.output_path(project_id).parent.mkdir(parents=True, exist_ok=True)
    recorder.output_path(project_id).write_bytes(b"held open")
    recorder.partial_path(project_id).write_bytes(b"free")

    handle = recorder.output_path(project_id).open("rb")
    try:
        response = c.post(f"/api/projects/{project_id}/recording/release")
    finally:
        handle.close()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["removed"] == 1
    assert body["held"] == ["output.mp4"]

    # Closed now, so a second press finishes the job.
    again = c.post(f"/api/projects/{project_id}/recording/release").json()
    assert again["removed"] == 1
    assert again["held"] == []
    assert again["recording"]["available"] is False


def test_releasing_is_refused_while_the_run_that_owns_it_is_going(client, tmp_path):
    c, _built = client
    project_id = c.post("/api/projects", json={"name": "release busy"}).json()["id"]
    assert c.post(
        f"/api/projects/{project_id}/source",
        files={"file": ("clip.mp4", _clip(tmp_path), "video/mp4")},
    ).status_code == 200
    face = _library_face(c, (40, 40, 200))
    assert c.post(
        f"/api/projects/{project_id}/face", json={"face_id": face}
    ).status_code == 200
    assert c.post(
        f"/api/projects/{project_id}/scheduler/start", json={}
    ).status_code == 200
    try:
        refused = c.post(f"/api/projects/{project_id}/recording/release")
        assert refused.status_code == 409, refused.text
    finally:
        c.post(f"/api/projects/{project_id}/scheduler/stop")


def test_the_test_writes_nothing_outside_tmp(client, tmp_path, monkeypatch):
    """Guard the guard: the fixture really repointed both roots."""
    c, _built = client
    settings = get_settings()
    assert str(settings.data_dir).startswith(str(tmp_path)), settings.data_dir
    assert str(settings.output_dir).startswith(str(tmp_path)), settings.output_dir
