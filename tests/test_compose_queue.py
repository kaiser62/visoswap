"""The compose queue: serial, addressable, cancellable.

Every stop enqueues a compose, so the queue's job is to make work the user did
not explicitly ask for both visible and stoppable. These tests drive it with a
stubbed pass rather than real ffmpeg — what is under test here is ordering,
state and cancellation, not encoding, which `test_composer` covers.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.services import compose_queue as cq


class _StubPass:
    """Stands in for `ComposePass`: waits to be released, then publishes."""

    def __init__(self, name: str, gate: asyncio.Event | None = None) -> None:
        self.name = name
        self.gate = gate
        self.total_frames = 10
        self.frames_written = 0
        self.output: Path | None = None
        self.started = asyncio.Event()

    async def run(self) -> Path:
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        self.frames_written = self.total_frames
        self.output = Path(f"{self.name}.mp4")
        return self.output


@pytest.fixture
def queue(monkeypatch):
    """A fresh queue per test — `cq.queue` is process-wide and would leak."""
    return cq.ComposeQueue()


def _install(monkeypatch, passes: dict[str, object]):
    """Route `build_pass` to a stub keyed on project id. None means 'nothing'."""
    async def build(project_id, source, *, name="", face=""):
        return passes.get(project_id)

    monkeypatch.setattr(cq, "build_pass", build)


def test_a_job_runs_and_reports_the_take_it_produced(queue, monkeypatch):
    run = _StubPass("take")
    _install(monkeypatch, {"p1": run})

    async def scenario():
        job = queue.enqueue("p1", Path("src.mp4"), project_name="Demo")
        assert job.state == "pending"
        await asyncio.wait_for(_settled(queue, job.id), timeout=5)
        return job.snapshot()

    snap = asyncio.run(scenario())
    assert snap["state"] == "done"
    assert snap["output_name"] == "take.mp4"
    assert snap["frames_written"] == 10


def test_jobs_run_one_at_a_time_not_all_at_once(queue, monkeypatch):
    # A compose saturates the machine. Two at once finish later than two in a
    # row, so the second must not start until the first is out of the way.
    gate = asyncio.Event()
    first, second = _StubPass("first", gate), _StubPass("second")
    _install(monkeypatch, {"p1": first, "p2": second})

    async def scenario():
        queue.enqueue("p1", Path("a.mp4"))
        job2 = queue.enqueue("p2", Path("b.mp4"))
        await asyncio.wait_for(first.started.wait(), timeout=5)
        await asyncio.sleep(0.05)
        held = second.started.is_set()
        gate.set()
        await asyncio.wait_for(_settled(queue, job2.id), timeout=5)
        return held, second.started.is_set()

    held, ran_after = asyncio.run(scenario())
    assert held is False
    assert ran_after is True


def test_cancelling_a_waiting_job_stops_it_ever_running(queue, monkeypatch):
    gate = asyncio.Event()
    first, second = _StubPass("first", gate), _StubPass("second")
    _install(monkeypatch, {"p1": first, "p2": second})

    async def scenario():
        queue.enqueue("p1", Path("a.mp4"))
        job2 = queue.enqueue("p2", Path("b.mp4"))
        await asyncio.wait_for(first.started.wait(), timeout=5)
        assert queue.cancel(job2.id) is True
        gate.set()
        await asyncio.sleep(0.1)
        return job2.state, second.started.is_set()

    state, ever_started = asyncio.run(scenario())
    assert state == "cancelled"
    assert ever_started is False


def test_cancelling_a_running_job_leaves_the_queue_running(queue, monkeypatch):
    # The running job gets its own task precisely so that cancelling it does not
    # take down the runner every job behind it is waiting on.
    gate = asyncio.Event()
    first, second = _StubPass("first", gate), _StubPass("second")
    _install(monkeypatch, {"p1": first, "p2": second})

    async def scenario():
        job1 = queue.enqueue("p1", Path("a.mp4"))
        job2 = queue.enqueue("p2", Path("b.mp4"))
        await asyncio.wait_for(first.started.wait(), timeout=5)
        queue.cancel(job1.id)
        await asyncio.wait_for(_settled(queue, job2.id), timeout=5)
        return job1.snapshot(), job2.snapshot()

    one, two = asyncio.run(scenario())
    assert one["state"] == "cancelled"
    assert two["state"] == "done"


def test_a_project_with_nothing_generated_is_empty_not_failed(queue, monkeypatch):
    # A run stopped before it produced a frame has nothing to compose. Reporting
    # that as an error would put a red row in the panel for a normal stop.
    _install(monkeypatch, {})

    async def scenario():
        job = queue.enqueue("p1", Path("a.mp4"))
        await asyncio.wait_for(_settled(queue, job.id), timeout=5)
        return job.snapshot()

    assert asyncio.run(scenario())["state"] == "empty"


def test_a_pass_that_raises_fails_only_its_own_job(queue, monkeypatch):
    class _Broken(_StubPass):
        async def run(self):
            self.started.set()
            raise RuntimeError("ffmpeg produced no output")

    broken, good = _Broken("broken"), _StubPass("good")
    _install(monkeypatch, {"p1": broken, "p2": good})

    async def scenario():
        job1 = queue.enqueue("p1", Path("a.mp4"))
        job2 = queue.enqueue("p2", Path("b.mp4"))
        await asyncio.wait_for(_settled(queue, job2.id), timeout=5)
        return job1.snapshot(), job2.snapshot()

    one, two = asyncio.run(scenario())
    assert one["state"] == "failed"
    assert one["error"] == "ffmpeg produced no output"
    assert two["state"] == "done"


def test_jobs_are_listed_newest_first(queue, monkeypatch):
    _install(monkeypatch, {})

    async def scenario():
        queue.enqueue("p1", Path("a.mp4"))
        job2 = queue.enqueue("p2", Path("b.mp4"))
        await asyncio.wait_for(_settled(queue, job2.id), timeout=5)
        return [row["project_id"] for row in queue.jobs()]

    assert asyncio.run(scenario()) == ["p2", "p1"]


def test_a_finished_job_can_be_cleared_and_a_live_one_cannot(queue, monkeypatch):
    gate = asyncio.Event()
    run = _StubPass("held", gate)
    _install(monkeypatch, {"p1": run})

    async def scenario():
        job = queue.enqueue("p1", Path("a.mp4"))
        await asyncio.wait_for(run.started.wait(), timeout=5)
        live = queue.forget(job.id)
        gate.set()
        await asyncio.wait_for(_settled(queue, job.id), timeout=5)
        return live, queue.forget(job.id), queue.jobs()

    live, cleared, remaining = asyncio.run(scenario())
    assert live is False
    assert cleared is True
    assert remaining == []


def test_shutdown_cancels_everything_still_in_flight(queue, monkeypatch):
    gate = asyncio.Event()
    first, second = _StubPass("first", gate), _StubPass("second")
    _install(monkeypatch, {"p1": first, "p2": second})

    async def scenario():
        job1 = queue.enqueue("p1", Path("a.mp4"))
        job2 = queue.enqueue("p2", Path("b.mp4"))
        await asyncio.wait_for(first.started.wait(), timeout=5)
        await queue.aclose()
        return job1.state, job2.state, second.started.is_set()

    one, two, ran = asyncio.run(scenario())
    assert one == "cancelled"
    assert two == "cancelled"
    assert ran is False


def test_the_finished_tail_is_bounded(queue, monkeypatch):
    _install(monkeypatch, {})

    async def scenario():
        last = None
        for _ in range(cq.HISTORY_LIMIT + 5):
            last = queue.enqueue("p1", Path("a.mp4"))
        await asyncio.wait_for(_settled(queue, last.id), timeout=10)
        return len(queue.jobs())

    assert asyncio.run(scenario()) <= cq.HISTORY_LIMIT


# -- the stop hook ------------------------------------------------------------


class _StubDb:
    def __init__(self, project: dict | None) -> None:
        self.project = project

    async def get_project(self, project_id: str):
        return self.project


def _scheduler(project: dict | None):
    """A scheduler with only what `_queue_compose` reads. Constructing a real
    one would need a database, a video and a model."""
    from backend.services.scheduler import ProjectScheduler

    scheduler = ProjectScheduler.__new__(ProjectScheduler)
    scheduler.project_id = "p1"
    scheduler.db = _StubDb(project)
    return scheduler


def test_a_stop_queues_a_compose_of_what_the_run_generated(monkeypatch, tmp_path):
    # "Save them after every stop" — the live recording holds what the writer
    # could commit in time, and this second pass holds every generated frame
    # there is. The two are the same file only when generation kept up.
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.config import get_settings

    get_settings.cache_clear()
    seen: list[tuple] = []
    monkeypatch.setattr(
        cq.queue, "enqueue",
        lambda pid, src, **kw: seen.append((pid, Path(src).name, kw)),
    )
    scheduler = _scheduler(
        {"video_path": "projects/p1/source.mp4", "name": "Demo",
         "source_face_path": "faces/ada.jpg"}
    )

    asyncio.run(scheduler._queue_compose())

    assert len(seen) == 1
    project_id, source_name, kw = seen[0]
    assert project_id == "p1"
    assert source_name == "source.mp4"
    assert kw == {"project_name": "Demo", "face": "ada"}
    get_settings.cache_clear()


def test_a_stop_on_a_project_with_no_local_video_queues_nothing(monkeypatch):
    seen: list = []
    monkeypatch.setattr(cq.queue, "enqueue", lambda *a, **k: seen.append(a))
    asyncio.run(_scheduler({"name": "Demo"})._queue_compose())
    assert seen == []


def test_a_stop_still_stops_when_the_compose_cannot_be_queued(monkeypatch):
    # The stop has already done its job by this point. A convenience layered on
    # top of it must not be able to turn a successful stop into a failed one.
    def boom(*args, **kwargs):
        raise RuntimeError("queue is broken")

    monkeypatch.setattr(cq.queue, "enqueue", boom)
    asyncio.run(_scheduler({"video_path": "projects/p1/s.mp4"})._queue_compose())


async def _settled(queue: cq.ComposeQueue, job_id: str) -> None:
    """Poll until the job leaves pending/running. Cheaper than a callback API
    the queue would otherwise have to grow purely for tests."""
    while True:
        job = queue.get(job_id)
        if job is None or not job.active:
            return
        await asyncio.sleep(0.01)
