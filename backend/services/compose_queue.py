"""The compose queue: one offline pass at a time, visible and cancellable.

A compose is a full re-encode. Two of them at once do not go twice as fast --
they contend for the same cores and each finishes later than it would have
alone -- so the queue runs strictly one job at a time and everything else waits
its turn.

Every stop enqueues one, which is what makes the queue necessary rather than a
convenience: pressing Stop three times in a minute must not stack three
re-encodes the user cannot see or call off. So jobs are addressable, their state
is reportable, and both a waiting job and a running one can be cancelled --
`POST /api/compose/jobs/{id}/cancel`, surfaced as a panel in the UI.

Jobs live in memory. They describe work this process is doing or has just done;
a restart has no work in flight to describe, and the artefacts themselves are on
disk in the output folder where the Gallery already lists them.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from backend.services.composer import ComposePass, build_pass

log = logging.getLogger(__name__)

JobState = Literal["pending", "running", "done", "failed", "cancelled", "empty"]

#: Finished jobs kept for the panel to show. Old enough to have scrolled out of
#: interest, and bounded so a long session cannot grow the list without limit.
HISTORY_LIMIT = 40


@dataclass
class ComposeJob:
    """One queued or finished compose, as the API reports it."""

    id: str
    project_id: str
    project_name: str
    source: Path
    face: str = ""
    state: JobState = "pending"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    frames_written: int = 0
    total_frames: int = 0
    #: Filename in the output folder once published; None until then.
    output_name: str | None = None
    error: str | None = None
    #: The live pass, held only while running so progress can be read off it.
    _pass: ComposePass | None = None
    _task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return self.state in ("pending", "running")

    def snapshot(self) -> dict[str, Any]:
        """The wire shape. Progress is read from the live pass, not stored."""
        run = self._pass
        if run is not None:
            self.frames_written = run.frames_written
            self.total_frames = run.total_frames
        return {
            "id": self.id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "frames_written": self.frames_written,
            "total_frames": self.total_frames,
            "output_name": self.output_name,
            "error": self.error,
        }


class ComposeQueue:
    """Serial runner for compose jobs. One instance per process (`queue`)."""

    def __init__(self) -> None:
        self._jobs: list[ComposeJob] = []
        self._waiting: asyncio.Queue[str] = asyncio.Queue()
        self._runner: asyncio.Task[None] | None = None

    # -- public surface -------------------------------------------------------

    def enqueue(
        self,
        project_id: str,
        source: Path,
        *,
        project_name: str = "",
        face: str = "",
    ) -> ComposeJob:
        """Add a job and make sure the runner is up. Returns immediately.

        Deliberately synchronous and non-blocking: this is called from the stop
        path, and stopping a run must never wait on a re-encode.
        """
        job = ComposeJob(
            id=uuid.uuid4().hex[:12],
            project_id=project_id,
            project_name=project_name,
            source=Path(source),
            face=face,
        )
        self._jobs.append(job)
        self._trim()
        self._waiting.put_nowait(job.id)
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run_forever(), name="compose-queue")
        log.info(
            "[COMPOSE] project=%s queued job=%s position=%d",
            project_id, job.id, sum(1 for j in self._jobs if j.active),
        )
        return job

    def jobs(self) -> list[dict[str, Any]]:
        """Newest first, which is the order the panel reads top-down."""
        return [job.snapshot() for job in reversed(self._jobs)]

    def get(self, job_id: str) -> ComposeJob | None:
        return next((job for job in self._jobs if job.id == job_id), None)

    def cancel(self, job_id: str) -> bool:
        """Call off a job. True if this call is what stopped it.

        A pending job is marked here and skipped when the runner reaches it --
        pulling it out of the queue would mean searching an `asyncio.Queue`,
        which has no such operation, so the runner filters instead. A running
        job is cancelled through its task, and the pass kills its own ffmpeg
        processes on the way out.
        """
        job = self.get(job_id)
        if job is None or not job.active:
            return False
        if job.state == "pending":
            job.state = "cancelled"
            job.finished_at = time.time()
            return True
        if job._task is not None:
            job._task.cancel()
        return True

    def forget(self, job_id: str) -> bool:
        """Drop a finished job from the list. Refused while it is still going."""
        job = self.get(job_id)
        if job is None or job.active:
            return False
        self._jobs.remove(job)
        return True

    async def aclose(self) -> None:
        """Cancel everything and wait. For application shutdown."""
        for job in self._jobs:
            if job.active:
                self.cancel(job.id)
        runner = self._runner
        self._runner = None
        if runner is not None:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)

    # -- the runner -----------------------------------------------------------

    async def _run_forever(self) -> None:
        while True:
            job_id = await self._waiting.get()
            job = self.get(job_id)
            # Gone or called off while it waited: nothing to do, and this is the
            # normal path for a cancelled pending job.
            if job is None or job.state != "pending":
                continue
            # Its own task, not this coroutine: cancelling a running job must
            # cancel the job, and cancelling the runner would take every job
            # queued behind it down as well.
            task = asyncio.create_task(self._run_one(job), name=f"compose:{job.id}")
            job._task = task
            await asyncio.gather(task, return_exceptions=True)
            # Also here, not only at enqueue: a burst that is queued all at once
            # is all still active when the last of it arrives, so trimming on
            # the way in alone never sheds anything.
            self._trim()

    async def _run_one(self, job: ComposeJob) -> None:
        job.state = "running"
        job.started_at = time.time()
        try:
            run = await build_pass(
                job.project_id, job.source,
                name=job.project_name, face=job.face,
            )
            if run is None:
                # Nothing was generated. Not a failure -- a run stopped before
                # it produced a frame has nothing to compose, and reporting that
                # as an error would put a red row in the panel for a normal stop.
                job.state = "empty"
                return
            job._pass = run
            job.total_frames = run.total_frames
            output = await run.run()
            job.output_name = output.name
            job.frames_written = run.frames_written
            job.state = "done"
        except asyncio.CancelledError:
            job.state = "cancelled"
            log.info("[COMPOSE] project=%s job=%s cancelled", job.project_id, job.id)
            # Swallowed on purpose: cancelling one job must not tear down the
            # runner that the jobs behind it are waiting on. `aclose` cancels the
            # runner itself, which is a different task and unaffected by this.
        except Exception as exc:
            job.state = "failed"
            job.error = str(exc)
            log.exception(
                "[COMPOSE] project=%s job=%s failed", job.project_id, job.id
            )
        finally:
            job.finished_at = time.time()
            job._pass = None
            job._task = None

    def _trim(self) -> None:
        """Keep the finished tail bounded; never drop anything still going."""
        finished = [job for job in self._jobs if not job.active]
        excess = len(finished) - HISTORY_LIMIT
        for job in finished[:max(0, excess)]:
            self._jobs.remove(job)


#: Process-wide queue. The API and the scheduler both push onto this one.
queue = ComposeQueue()
