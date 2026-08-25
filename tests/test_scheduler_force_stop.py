"""The forced stop: the one that returns even when the run will not.

The ordinary stop waits — for every worker to unwind and for the recorder to
drain — and that wait is what keeps the recording whole. But a worker parked
inside a model call holds the wait open for as long as the call takes, and a
backend that has stopped answering holds it open indefinitely. These pin the
behaviour that makes the force button worth having: it returns, it settles the
rows the killed workers abandoned, and it does not silently become the default.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from backend.config import get_settings
from backend.models.database import STATUS_PROCESSING, Database
from backend.services.scheduler import ProjectScheduler

PROJECT_ID = "f" * 32


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@asynccontextmanager
async def opened(data_dir):
    """A connected database with the project row already in it.

    Built inside the test rather than in a fixture: the connection belongs to
    the running loop, and the suite has no async fixture convention to borrow.
    """
    database = Database(data_dir / "test.db")
    await database.connect()
    await database.create_project(id=PROJECT_ID, name="forced")
    try:
        yield database
    finally:
        await database.close()


@asynccontextmanager
async def wedged_scheduler(db: Database):
    """A scheduler holding one worker whose cancellation does not take.

    Exactly the case the force button exists for: a worker blocked inside a
    model call that never checks for cancellation. `released` is the test's own
    way back out — without it the task would outlive the event loop.
    """
    scheduler = ProjectScheduler(PROJECT_ID, db)
    scheduler.running = True
    released = asyncio.Event()

    async def wedged() -> None:
        while not released.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue

    task = asyncio.create_task(wedged())
    await asyncio.sleep(0)
    scheduler._workers.append(task)
    try:
        yield scheduler, released
    finally:
        released.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_forced_stop_returns_without_waiting_for_a_wedged_worker(data_dir):
    async with opened(data_dir) as db, wedged_scheduler(db) as (scheduler, released):
        await asyncio.wait_for(scheduler.stop(force=True), timeout=2.0)
        assert scheduler.running is False
        project = await db.get_project(PROJECT_ID)
        assert project["status"] == "idle"


@pytest.mark.asyncio
async def test_an_ordinary_stop_still_waits(data_dir):
    # The guarantee the force flag is trading away has to still be there when
    # the flag is not passed, or every stop quietly truncates the recording.
    async with opened(data_dir) as db, wedged_scheduler(db) as (scheduler, released):
        # Not `wait_for`: cancelling the ordinary stop would itself hang, since
        # the cancellation has to travel through the same worker that is
        # ignoring it. Watch the task instead and let it finish on its own.
        stopping = asyncio.create_task(scheduler.stop())
        done, _ = await asyncio.wait({stopping}, timeout=0.5)
        assert not done, "the ordinary stop returned without waiting for its worker"
        released.set()
        await asyncio.wait_for(stopping, timeout=5.0)


@pytest.mark.asyncio
async def test_a_forced_stop_settles_the_frames_its_workers_abandoned(data_dir):
    async with opened(data_dir) as db:
        await db.enqueue_frames(PROJECT_ID, [1.0, 2.0], priority=1)
        claimed = await db.claim_next_frame(PROJECT_ID)
        assert claimed is not None
        assert (await db.counts(PROJECT_ID))[STATUS_PROCESSING] == 1

        async with wedged_scheduler(db) as (scheduler, released):
            await asyncio.wait_for(scheduler.stop(force=True), timeout=2.0)

        counts = await db.counts(PROJECT_ID)
    # Nothing is left claiming to be on the GPU. An unsettled row would pin the
    # recording watermark under it forever and keep reporting work in flight.
    assert counts[STATUS_PROCESSING] == 0
    assert counts["cancelled"] == 1
    # Pending work is left alone: it was never started, and the next run clears
    # the queue itself.
    assert counts["pending"] == 1
