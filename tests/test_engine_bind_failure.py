"""A silent engine failure must become a visible one.

On a box where the engine cannot come up at all — "Torch not compiled with CUDA
enabled" on a Mac, a CUDA OOM, a model that will not load — `bind()` fails every
time. The worker used to hand the frame back and retry forever, so the queue sat
at "183 pending, 0 failed" with a frozen UI and no error anywhere to show the
user. These pin the circuit breaker that turns that into a failed run with a
message.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from backend.config import get_settings
from backend.models.database import STATUS_PENDING, Database
from backend.services.generator import FrameGenerator, GenerationResult
from backend.services.scheduler import ProjectScheduler
from backend.workers.generation_worker import run_worker

PROJECT_ID = "b" * 32


class BindFails(FrameGenerator):
    """A generator whose bind always fails, the way a CUDA-less box fails."""

    name = "bind-fails"
    decodes_own_source = True

    def __init__(self, project, message="Torch not compiled with CUDA enabled"):
        self.project = project
        self.message = message
        self.binds = 0

    async def health(self):
        return {}

    async def validate(self):
        return None

    async def bind(self, project):
        self.binds += 1
        raise RuntimeError(self.message)

    async def generate(self, image_bytes, filename):
        raise AssertionError("generate must not be reached when bind fails")

    async def generate_at(self, timestamp, filename):
        raise AssertionError("generate_at must not be reached when bind fails")

    async def close(self):
        return None


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@asynccontextmanager
async def opened(data_dir, monkeypatch, *, failing=True, message="Torch not compiled with CUDA enabled"):
    import sys
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "_CURRENT_MESSAGE", message, raising=False)

    def factory(project):
        return BindFails(project, message)

    monkeypatch.setenv("VISOSWAP_GENERATOR_FACTORY", f"{__name__}:factory")
    monkeypatch.setenv("GENERATION_CONCURRENCY", "2")
    get_settings.cache_clear()

    db = Database(data_dir / "test.db")
    await db.connect()
    await db.create_project(id=PROJECT_ID, name="bind")
    await db.enqueue_frames(PROJECT_ID, [0.5, 1.0], 1)
    try:
        yield db
    finally:
        await db.close()
        get_settings.cache_clear()


def factory(project):  # the importable target for VISOSWAP_GENERATOR_FACTORY
    return BindFails(project)


@pytest.mark.asyncio
async def test_bind_failure_counted_and_bind_success_resets(data_dir):
    db = Database(data_dir / "a.db")
    await db.connect()
    await db.create_project(id=PROJECT_ID, name="x")
    try:
        s = ProjectScheduler(PROJECT_ID, db)
        s.running = True
        assert s._bind_failures == 0
        assert await s.note_bind_failure(RuntimeError("boom")) is False
        assert s._bind_failures == 1
        s.note_bind_success()
        assert s._bind_failures == 0 and s._bind_error is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_run_is_failed_after_n_consecutive_bind_failures(data_dir):
    db = Database(data_dir / "b.db")
    await db.connect()
    await db.create_project(id=PROJECT_ID, name="x")
    try:
        s = ProjectScheduler(PROJECT_ID, db)
        s.running = True
        limit = int(get_settings().max_bind_failures)
        results = [await s.note_bind_failure(RuntimeError("Torch not compiled with CUDA enabled"))
                   for _ in range(limit)]
        assert results[:-1] == [False] * (limit - 1) and results[-1] is True
        assert s.running is False
        project = await db.get_project(PROJECT_ID)
        assert project["status"] == "error"
        assert "Torch not compiled with CUDA enabled" in project["error"]
        assert project["error"].startswith("I could not start the engine:")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_error_message_is_the_first_line_only(data_dir):
    db = Database(data_dir / "c.db")
    await db.connect()
    await db.create_project(id=PROJECT_ID, name="x")
    try:
        s = ProjectScheduler(PROJECT_ID, db)
        s.running = True
        long = RuntimeError("CUDA out of memory\nTried to allocate 2.00 GiB\n  frame 41\n  ...")
        for _ in range(int(get_settings().max_bind_failures)):
            await s.note_bind_failure(long)
        project = await db.get_project(PROJECT_ID)
        assert "Tried to allocate" not in project["error"]
        assert "CUDA out of memory" in project["error"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failure_does_not_trip_when_the_run_already_stopped(data_dir):
    db = Database(data_dir / "d.db")
    await db.connect()
    await db.create_project(id=PROJECT_ID, name="x")
    try:
        s = ProjectScheduler(PROJECT_ID, db)
        s.running = False
        for _ in range(int(get_settings().max_bind_failures) + 2):
            assert await s.note_bind_failure(RuntimeError("late")) is False
        assert (await db.get_project(PROJECT_ID))["status"] != "error"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_worker_stops_instead_of_spinning_forever(data_dir, monkeypatch):
    """The real path: the worker loop must exit and the project must be failed."""
    async with opened(data_dir, monkeypatch) as db:
        s = ProjectScheduler(PROJECT_ID, db)
        s.running = True
        task = asyncio.create_task(run_worker(s, 0))
        await asyncio.wait_for(task, timeout=15.0)
        assert s.running is False
        project = await db.get_project(PROJECT_ID)
        assert project["status"] == "error"
        assert "engine" in (project["error"] or "").lower()
        # frames are handed back, not burned: nothing is marked failed
        counts = await db.counts(PROJECT_ID)
        assert counts.get("failed", 0) == 0
        assert counts.get(STATUS_PENDING, 0) + counts.get("processing", 0) >= 1


@pytest.mark.asyncio
async def test_worker_retries_below_the_limit(data_dir, monkeypatch):
    """Under the threshold the old behaviour stands: retry, do not fail the run."""
    async with opened(data_dir, monkeypatch) as db:
        monkeypatch.setattr(ProjectScheduler, "note_bind_failure", _never_trips, raising=True)
        s = ProjectScheduler(PROJECT_ID, db)
        s.running = True
        task = asyncio.create_task(run_worker(s, 0))
        await asyncio.sleep(1.2)
        assert s.running is True                       # still retrying
        s.running = False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert (await db.get_project(PROJECT_ID))["status"] != "error"


async def _never_trips(self, exc):
    self._bind_failures += 1
    self._bind_error = str(exc)
    return False


def test_generator_factory_ignored_for_non_pooled_calls(monkeypatch, tmp_path):
    """The test hook must not be able to replace a one-off validate/preview generator."""
    from backend.services import generator as G

    monkeypatch.setenv("VISOSWAP_GENERATOR_FACTORY", f"{__name__}:factory")
    project = {"id": PROJECT_ID, "backend": "engine", "generated_format": "jpeg"}
    built = G.build_generator(project, 0.0, 0, pooled=False)
    assert not isinstance(built, BindFails)


@pytest.mark.asyncio
async def test_status_endpoint_exposes_status_and_error(data_dir):
    """A failed run must be visible on the status poll, not only on the project row."""
    from backend.api.generation import _status

    db = Database(data_dir / "st.db")
    await db.connect()
    await db.create_project(id=PROJECT_ID, name="x")
    await db.enqueue_frames(PROJECT_ID, [0.5], 1)
    try:
        s = ProjectScheduler(PROJECT_ID, db)
        s.running = True
        for _ in range(int(get_settings().max_bind_failures)):
            await s.note_bind_failure(RuntimeError("Torch not compiled with CUDA enabled"))
        st = await _status(PROJECT_ID, db)
        assert st["running"] is False
        assert st["status"] == "error"
        assert "Torch not compiled with CUDA enabled" in st["error"]
        # the frames are still pending: the point is that the *reason* is now visible
        assert st["counts"].get("pending", 0) >= 1
    finally:
        await db.close()
