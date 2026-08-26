"""Deterministic target-timestamp math and the per-project scheduler.

The pure functions at the top are the whole synchronisation contract: they are
derived only from `interval` and the reported playback position, never from
browser frame events, so there is no cumulative drift.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.models.database import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    Database,
)
from backend.services.events import hub
from backend.services.recorder import Recorder

log = logging.getLogger(__name__)

PRIORITY_IMMEDIATE = 1
PRIORITY_LOOKAHEAD = 2
PRIORITY_BACKGROUND = 3

# Frames within this many seconds ahead of playback are priority 1.
IMMEDIATE_SPAN = 30.0
# Never let a single enqueue explode; the window advances as playback does.
MAX_ENQUEUE_PER_TICK = 400
# Playback time is not perfectly discrete; treat anything inside this as equal.
TIMESTAMP_TOLERANCE = 0.05

MODE_INTERVAL = "interval"
MODE_STREAM = "stream"
# Videos that never reported an fps still need a grid in stream mode.
FALLBACK_FPS = 24.0
# Assumed seconds per frame before any measurement exists. Deliberately
# pessimistic: a grid that starts too sparse tightens as soon as real durations
# land, whereas one that starts too dense floods the queue on the first run.
COLD_FRAME_COST = 0.5
# Never plan a grid coarser than this, however slow the backend is; past a
# couple of seconds between swapped frames the held face no longer matches the
# scene under it.
MIN_STREAM_RATE = 0.5
# Ladder stop for `snap_rate`. At 60fps this is one generated frame every ~2.1s,
# which is already `MIN_STREAM_RATE` territory.
_MAX_GRID_DIVISOR = 128


# --- pure timestamp math -----------------------------------------------------


def quantize(ts: float) -> float:
    return round(float(ts), 3)


def grid_rate(project: dict[str, Any], observed_rate: float | None = None) -> float:
    """Target timestamps per second of video.

    Interval mode puts one target every `interval` seconds. Stream mode aims at
    every video frame, but only as far as the backend can actually deliver:
    asking for 24 targets a second from a backend producing 4 does not make the
    overlay smoother, it makes the playhead outrun the queue and the scheduler
    cancel each target just before it would have been generated — measured end
    state was two frames kept out of a whole run.

    So the stream grid is capped at the measured throughput. The overlay holds
    each frame until the next arrives, which is what makes a sparse grid look
    continuous rather than strobing.

    Full-video mode is the exception: it plans the whole clip up front and
    nothing races the playhead, so throttling it to what the backend can deliver
    IN REAL TIME only guarantees a permanently sparse render. A 60fps source
    capped at a measured 15fps grid produced a take with every swapped face held
    across four source frames -- an even judder instead of an uneven one. Given
    time it can have every frame, so it asks for every frame.
    """
    if project.get("full_video_mode"):
        fps = float(project.get("fps") or 0.0)
        return fps if fps > 0 else FALLBACK_FPS
    if (project.get("generation_mode") or MODE_INTERVAL) == MODE_STREAM:
        fps = float(project.get("fps") or 0.0)
        fps = fps if fps > 0 else FALLBACK_FPS
        return snap_rate(fps, min(fps, sustainable_rate(project, observed_rate)))
    interval = float(project.get("interval") or 0.0)
    if interval <= 0:
        raise ValueError("interval must be > 0")
    return 1.0 / interval


def sustainable_rate(
    project: dict[str, Any], observed_rate: float | None = None
) -> float:
    """Frames per second of video the backend can actually finish.

    `observed_rate` — frames the pool actually completed per wall second — wins
    when the scheduler has one, because the per-frame estimate below is a
    prediction and this is the outcome. They disagree by a lot: measured live at
    four workers, `measured_frame_cost` of 0.08s predicts 50 fps while the pool
    completed 18. Planning the difference produced targets that were cancelled
    unstarted as the playhead passed them, and the ones that did get made landed
    unevenly — which is the "slideshow" the sparse grid was supposed to avoid.
    The 1.2 headroom lets the grid grow back when the scene gets cheaper.

    Falls back to the project's own measured average, so a cold run still adapts
    to the clip and the hardware. Cost is strongly content-dependent — the same
    backend measured 0.03s on frames with no face and 0.5s on a close-up.
    """
    if observed_rate and observed_rate > 0:
        return max(MIN_STREAM_RATE, observed_rate * 1.2)
    cost = float(project.get("measured_frame_cost") or 0.0) or COLD_FRAME_COST
    workers = max(1, get_settings().generation_concurrency)
    return max(MIN_STREAM_RATE, workers / cost)


def snap_rate(fps: float, rate: float) -> float:
    """Snap a throughput estimate onto the `fps / n` ladder.

    Targets are `k / rate`, so the rate does not only decide HOW MANY frames get
    made — it decides WHICH timestamps exist. `sustainable_rate` is a live
    measurement and drifts by a few percent every planning tick, which moved the
    whole grid a millisecond or two each time. Nothing lined up: work already
    completed sat off the new grid so it counted for nothing, a fresh near
    duplicate was queued a millisecond away, and the playhead passed most of
    them before a worker got there. A real run ended 9557 cancelled against 2364
    completed, those completions bunched into clusters 1-2ms apart covering ten
    seconds of a two-minute video -- dense where nobody needed it and empty
    everywhere else. That is the choppiness, in playback and in the take alike.

    `n` doubles rather than taking any integer, because only a doubling ladder
    NESTS: `fps/4` is a subset of `fps/2`, while `fps/7` shares nothing with it
    but the origin. Stepping the rate up or down therefore keeps every frame
    already made exactly on-grid, so a slowdown coarsens the grid by dropping
    targets rather than by moving them.
    """
    if fps <= 0:
        raise ValueError("fps must be > 0")
    n = 1
    while fps / n > rate and n < _MAX_GRID_DIVISOR:
        n *= 2
    return fps / n


def effective_interval(project: dict[str, Any]) -> float:
    """Spacing of the target grid, for display and keep-up arithmetic."""
    return 1.0 / grid_rate(project)


def _tolerance(rate: float) -> float:
    """Never swallow a whole grid step: at 24fps a 0.05s slop is more than one."""
    return min(TIMESTAMP_TOLERANCE, 0.4 / rate)


def target_for_time(current_time: float, interval: float) -> float:
    """Deterministic target timestamp covering `current_time`.

    Nearest previous multiple of `interval` — never a future frame.
    """
    if interval <= 0:
        raise ValueError("interval must be > 0")
    return target_for_rate(current_time, 1.0 / interval)


def target_for_rate(current_time: float, rate: float) -> float:
    """`target_for_time` on a grid expressed as targets-per-second.

    Indexing by `k / rate` rather than `k * interval` keeps a 23.976fps grid
    exact; a rounded 0.042s step would drift ~0.4s across a minute.
    """
    if rate <= 0:
        raise ValueError("rate must be > 0")
    if current_time < 0:
        return 0.0
    index = math.floor((current_time + _tolerance(rate)) * rate)
    return quantize(index / rate)


def target_timestamps(
    start: float, end: float, interval: float, duration: float | None = None
) -> list[float]:
    """All target timestamps `k * interval` inside `[start, end]`."""
    if interval <= 0:
        raise ValueError("interval must be > 0")
    return targets_for_rate(start, end, 1.0 / interval, duration)


def targets_for_rate(
    start: float, end: float, rate: float, duration: float | None = None
) -> list[float]:
    """All target timestamps `k / rate` inside `[start, end]`."""
    if rate <= 0:
        raise ValueError("rate must be > 0")
    if duration is not None:
        end = min(end, duration)
    if end < start:
        return []
    tol = _tolerance(rate)
    first = max(0, math.ceil((start - tol) * rate))
    last = math.floor((end + tol) * rate)
    if last < first:
        return []
    count = min(last - first + 1, MAX_ENQUEUE_PER_TICK)
    return [quantize((first + i) / rate) for i in range(count)]


def generation_window(
    current_time: float, lookahead: float, duration: float | None
) -> tuple[float, float]:
    """`[current, current + lookahead]`, clamped to the video."""
    start = max(0.0, float(current_time))
    end = start + max(0.0, float(lookahead))
    if duration is not None:
        end = min(end, float(duration))
    return quantize(start), quantize(end)


def priority_for(ts: float, current_time: float, lookahead: float) -> int:
    delta = ts - current_time
    if delta < 0:
        return PRIORITY_BACKGROUND  # behind playback: only useful on rewatch
    if delta <= IMMEDIATE_SPAN:
        return PRIORITY_IMMEDIATE
    if delta <= lookahead:
        return PRIORITY_LOOKAHEAD
    return PRIORITY_BACKGROUND


def adaptive_lookahead(
    base_lookahead: float,
    interval: float,
    avg_duration: float | None,
    concurrency: int,
    pending: int,
) -> float:
    """Shrink the window when generation cannot keep up with playback.

    Throughput is `concurrency / avg_duration` frames per second of wall time;
    playback consumes `1 / interval` frames per second. If we are slower, a huge
    queue only delays the frames that matter, so cap it.
    """
    if not avg_duration or avg_duration <= 0:
        return base_lookahead
    frames_per_second = concurrency / avg_duration
    consumed_per_second = 1.0 / interval if interval > 0 else 0.0
    if frames_per_second >= consumed_per_second:
        return base_lookahead
    # Keep roughly the work the queue can actually finish, min one interval.
    sustainable_frames = max(1.0, frames_per_second * base_lookahead)
    if pending > sustainable_frames * 2:
        return max(interval, sustainable_frames * interval)
    return base_lookahead


# --- per-project scheduler ---------------------------------------------------


class ProjectScheduler:
    """Owns the generation window and the worker pool for one project."""

    def __init__(self, project_id: str, db: Database) -> None:
        self.project_id = project_id
        self.db = db
        self.settings = get_settings()
        self.current_time = 0.0
        self.running = False
        self.full_video_mode = False
        self._workers: list[asyncio.Task[None]] = []
        self._planner: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._last_plan = 0.0
        self._last_counts: dict[str, int] = {}
        # Completions per wall second, measured rather than predicted.
        self._observed_rate: float | None = None
        self._last_completed: tuple[float, int] | None = None
        # Fixed span mode: `(start, end)` in seconds, independent of playback.
        self.target_range: tuple[float, float] | None = None
        self._range_cursor: float | None = None
        self.recorder: Recorder | None = None
        #: A forced stop's detached cleanup, kept so a caller that needs the
        #: encoder's file handle back can wait for it. See `wait_closed`.
        self._reaper: asyncio.Task[None] | None = None

    # -- lifecycle ------------------------------------------------------------

    async def start(
        self,
        *,
        full_video: bool | None = None,
        target_range: tuple[float, float] | None = None,
    ) -> None:
        self.target_range = target_range
        self._range_cursor = target_range[0] if target_range else None
        if self.running:
            if full_video is not None:
                self.full_video_mode = full_video
                self._wake.set()
            return
        project = await self.db.get_project(self.project_id)
        if project is None:
            raise ValueError("project not found")
        self.full_video_mode = (
            project["full_video_mode"] if full_video is None else full_video
        )
        self.running = True
        await self._start_recorder(project)
        self._planner = asyncio.create_task(
            self._plan_loop(), name=f"planner:{self.project_id}"
        )
        from backend.workers.generation_worker import run_worker

        for index in range(self.settings.generation_concurrency):
            self._workers.append(
                asyncio.create_task(
                    run_worker(self, index), name=f"worker:{self.project_id}:{index}"
                )
            )
        await self.db.update_project(self.project_id, status="running", error=None)
        hub.publish(self.project_id, {"type": "scheduler_started"})
        log.info("[SCHEDULER] project=%s started full_video=%s",
                 self.project_id, self.full_video_mode)

    async def _start_recorder(self, project: dict[str, Any]) -> None:
        """Bring up the mp4 recorder, or run without one.

        A recorder that cannot start must never take the run down with it: the
        generated frames are the product, the recording is a convenience on top.
        """
        if not self.settings.recorder_enabled:
            return
        video_path = project.get("video_path")
        if not video_path:
            return
        from backend.services import cache as _cache
        from backend.services.ffmpeg import probe

        try:
            source = _cache.to_absolute(video_path)
            info = await probe(source)
            # The display stem of the active face names the exported take
            # (D-12). `_start_recorder` reads the fresh project row, so a face
            # changed between runs is picked up with no further wiring.
            face_source = str(project.get("source_face_path") or "")
            recorder = Recorder(
                self.project_id, source,
                width=info.width, height=info.height, fps=info.fps,
                duration=info.duration,
                name=str(project.get("name") or ""),
                face=Path(face_source).stem if face_source else "",
                swap_range=self.target_range,
            )
            await recorder.start()
        except Exception as exc:
            log.warning(
                "[RECORDER] project=%s not started: %s", self.project_id, exc
            )
            self.recorder = None
            return
        self.recorder = recorder

    async def stop(self, *, force: bool = False) -> None:
        """Stop the run.

        The ordinary stop is worth waiting for: it lets a worker finish
        unwinding and it drains the recorder, so the file on disk holds
        everything that was actually generated.

        `force` is the escape hatch for when that wait is the problem — a
        worker wedged inside a model call, a backend that has stopped
        answering, an encoder that will not drain. It cancels everything and
        returns without awaiting any of it: the run is marked idle and
        announced stopped immediately, and the cleanup is left to finish on
        its own in the background. The cost is real and is the point — the
        tail of the recording may be lost, because the alternative is a stop
        button that does not stop.
        """
        self.running = False
        self._wake.set()
        tasks = [*self._workers]
        if self._planner:
            tasks.append(self._planner)
        for task in tasks:
            task.cancel()
        self._workers.clear()
        self._planner = None
        recorder, self.recorder = self.recorder, None

        if force:
            # Detached on purpose. Nothing below is allowed to hold up the
            # caller, but a cancelled worker still has a generator to close and
            # the encoder still has a pipe to shut, so the work is handed to
            # the loop rather than dropped.
            self._reaper = asyncio.create_task(self._reap(tasks, recorder))
        else:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # Flush the recording before the run is marked idle. `aclose` is
            # idempotent and never raises, so cancel, error and shutdown all
            # leave a playable file behind.
            if recorder is not None:
                self.recorder = recorder
                await self._drain_recording()
                self.recorder = None
                await recorder.aclose()

        # A forced stop leaves `processing` rows behind: their worker was
        # cancelled mid-frame and will never settle them. Left alone they
        # would pin the recording watermark under them forever and make the
        # next status read claim work is still in flight.
        if force:
            abandoned = await self.db.cancel_processing(self.project_id)
            if abandoned:
                log.info(
                    "[SCHEDULER] project=%s force cancelled in-flight=%d",
                    self.project_id, abandoned,
                )
        await self.db.update_project(self.project_id, status="idle")
        hub.publish(self.project_id, {"type": "scheduler_stopped"})
        log.info(
            "[SCHEDULER] project=%s stopped%s", self.project_id, " (forced)" if force else ""
        )
        await self._queue_compose()

    async def _queue_compose(self) -> None:
        """Hand the finished run to the offline compose queue.

        Every stop earns one. The live recording is what the run could commit in
        time; the composed take is every generated frame there is, which is not
        the same file whenever generation ran behind the writer.

        Enqueuing only -- the pass itself runs on the queue's own task, so a
        re-encode never delays the stop that asked for it. Never fatal: a stop
        that has already done its job must not fail because a convenience on top
        of it could not be scheduled.
        """
        try:
            from backend.services import cache as _cache
            from backend.services.compose_queue import queue as compose_queue

            project = await self.db.get_project(self.project_id)
            video_path = (project or {}).get("video_path")
            if not project or not video_path:
                return
            face_source = str(project.get("source_face_path") or "")
            compose_queue.enqueue(
                self.project_id,
                _cache.to_absolute(video_path),
                project_name=str(project.get("name") or ""),
                face=Path(face_source).stem if face_source else "",
            )
        except Exception:
            log.exception(
                "[SCHEDULER] project=%s could not queue a compose", self.project_id
            )

    async def wait_closed(self) -> None:
        """Block until a forced stop's detached cleanup has finished.

        `stop(force=True)` answers before the encoder has let go of its file --
        that is the whole point of it. A caller about to DELETE that file (a
        restart clears the previous recording) has to wait for the handle
        first: Windows refuses to unlink a file another process holds open.
        """
        reaper = self._reaper
        if reaper is not None:
            await asyncio.gather(reaper, return_exceptions=True)

    async def _reap(
        self, tasks: list[asyncio.Task[Any]], recorder: Recorder | None
    ) -> None:
        """Finish a forced stop's cleanup after the caller has been let go."""
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if recorder is not None:
                await recorder.aclose()
        except Exception:
            log.exception("[SCHEDULER] project=%s reap failed", self.project_id)
        finally:
            # Only clear the slot if it is still this reap's: a second forced
            # stop may already have put its own cleanup there.
            if self._reaper is asyncio.current_task():
                self._reaper = None

    # -- playback tracking ----------------------------------------------------

    async def update_playback(self, current_time: float, *, seeked: bool = False) -> None:
        """Called by the frontend as playback advances or after a seek."""
        previous = self.current_time
        self.current_time = max(0.0, float(current_time))
        if seeked or abs(self.current_time - previous) > IMMEDIATE_SPAN:
            await self._reprioritize_for_seek()
        self._wake.set()

    async def _reprioritize_for_seek(self) -> None:
        """A seek makes the old window worthless: cancel unstarted work there
        and rebuild around the new position immediately."""
        project = await self.db.get_project(self.project_id)
        if project is None:
            return
        span = (
            float(project.get("stream_buffer") or 6.0)
            if (project.get("generation_mode") or MODE_INTERVAL) == MODE_STREAM
            else project["lookahead"]
        )
        start, end = generation_window(self.current_time, span, project["duration"])
        # A range run is not following the playhead: seeking away from it must
        # not cancel the span the user explicitly asked for.
        if not self.full_video_mode and self.target_range is None:
            cancelled = await self.db.cancel_pending_outside(self.project_id, start, end)
            if cancelled:
                log.info("[SCHEDULER] project=%s seek cancelled=%d window=%.1f-%.1f",
                         self.project_id, cancelled, start, end)
        await self._plan_once(project)
        hub.publish(
            self.project_id,
            {"type": "seek_reprioritized", "current_time": self.current_time,
             "window_start": start, "window_end": end},
        )

    # -- planning -------------------------------------------------------------

    async def _plan_loop(self) -> None:
        try:
            while self.running:
                project = await self.db.get_project(self.project_id)
                if project is None:
                    return
                counts = await self.db.counts(self.project_id)
                self._note_throughput(counts.get(STATUS_COMPLETED, 0))
                await self._plan_once(project)
                await self._publish_counts()
                await self._advance_recorder(counts, project)
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                # `asyncio.TimeoutError` only became the builtin in 3.11. The
                # live backend runs on VisoMaster's 3.10, where catching the
                # builtin misses it entirely: the planner died on its very first
                # idle tick and the queue stayed empty for the whole run.
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("[SCHEDULER] project=%s planner crashed", self.project_id)
            await self.db.update_project(self.project_id, status="error")

    async def _drain_recording(self) -> None:
        """Write out everything that was generated before letting the file close.

        The writer trails generation, so at the moment Stop is pressed there is
        always a span that has been swapped but not yet muxed. `aclose` cancels
        the pump, so without this that span is simply lost — the recording ends
        wherever the writer happened to be rather than where the run got to.

        The bound is the last *completed* timestamp, not the watermark: a job
        left `processing` when its worker was cancelled never settles, and the
        watermark would sit pinned underneath it. Frames with no generated
        counterpart in the drained span fall back to the source.
        """
        if self.recorder is None:
            return
        last = await self.db.last_generated_timestamp(self.project_id)
        if last is None:
            return
        try:
            await self.recorder.drain_to(last)
        except Exception:
            # Finalizing must not depend on this: a file that stops early is
            # still playable, and raising here would skip `aclose` entirely.
            log.exception("[RECORDER] project=%s drain failed", self.project_id)

    async def _advance_recorder(
        self, counts: dict[str, int], project: dict[str, Any]
    ) -> None:
        """Feed the recorder the generation watermark, and say when work is done.

        With nothing left pending or processing, no further generated frame can
        arrive, so the recorder stops waiting on the watermark and drains the
        remaining source frames at full speed.
        """
        if self.recorder is None:
            return
        self.recorder.set_frontier(await self.db.recording_watermark(self.project_id))
        outstanding = counts.get(STATUS_PENDING, 0) + counts.get(STATUS_PROCESSING, 0)
        if outstanding == 0 and counts.get(STATUS_COMPLETED, 0) > 0:
            self.recorder.finish()

    def _note_throughput(self, completed: int) -> None:
        """Fold the completions since the last pass into `_observed_rate`.

        Samples shorter than a second are ignored: the planner also wakes on
        playback events, and a 50ms gap with one completion in it reads as
        20fps by luck. A drop in `completed` means the run was reset, so the
        baseline restarts rather than going negative.
        """
        now = time.monotonic()
        previous = self._last_completed
        self._last_completed = (now, completed)
        if previous is None or completed < previous[1]:
            return
        elapsed = now - previous[0]
        if elapsed < 1.0:
            self._last_completed = previous
            return
        sample = (completed - previous[1]) / elapsed
        alpha = 0.3
        self._observed_rate = (
            sample
            if self._observed_rate is None
            else self._observed_rate * (1 - alpha) + sample * alpha
        )

    async def _plan_once(self, project: dict[str, Any]) -> None:
        rate = grid_rate(project, self._observed_rate)
        interval = 1.0 / rate
        duration = project.get("duration")

        if self.target_range is not None:
            await self._plan_range(rate, duration)
            return

        if self.full_video_mode:
            timestamps = targets_for_rate(0.0, duration or 0.0, rate, duration)
            await self._enqueue(timestamps, PRIORITY_BACKGROUND, project)
            # Still bias whatever is near playback so watching stays smooth.
            near = targets_for_rate(
                self.current_time, self.current_time + IMMEDIATE_SPAN, rate, duration
            )
            await self._enqueue(near, PRIORITY_IMMEDIATE, project)
            return

        if (project.get("generation_mode") or MODE_INTERVAL) == MODE_STREAM:
            # Stream mode fills a short contiguous run of *consecutive* video
            # frames ahead of the playhead. A long lookahead would be pointless:
            # at ~0.6s of GPU per frame the buffer can never outrun playback, so
            # the only useful work is the frames immediately in front of it.
            buffer_span = max(interval, float(project.get("stream_buffer") or 6.0))
            start, end = generation_window(self.current_time, buffer_span, duration)
            # Drop everything still pending outside that window. Targets are all
            # PRIORITY_IMMEDIATE here, so `claim_next_frame` orders by timestamp
            # and workers keep picking the oldest one: with playback at 30s the
            # pool was still generating 0.25s, 1580 jobs deep, and the overlay
            # never showed a single frame. A frame the playhead has passed is
            # worth nothing in stream mode — only the ones in front of it are.
            if not self.full_video_mode:
                dropped = await self.db.cancel_pending_outside(self.project_id, start, end)
                if dropped:
                    log.debug(
                        "[SCHEDULER] project=%s dropped=%d behind window=%.1f-%.1f",
                        self.project_id, dropped, start, end,
                    )
            await self._enqueue(
                targets_for_rate(start, end, rate, duration),
                PRIORITY_IMMEDIATE,
                project,
            )
            self._last_plan = time.time()
            return

        counts = await self.db.counts(self.project_id)
        lookahead = adaptive_lookahead(
            float(project["lookahead"]),
            interval,
            await self.db.average_duration(self.project_id),
            self.settings.generation_concurrency,
            counts.get(STATUS_PENDING, 0),
        )
        start, end = generation_window(self.current_time, lookahead, duration)

        immediate_end = min(end, self.current_time + IMMEDIATE_SPAN)
        await self._enqueue(
            targets_for_rate(start, immediate_end, rate, duration),
            PRIORITY_IMMEDIATE,
            project,
        )
        if end > immediate_end:
            await self._enqueue(
                targets_for_rate(immediate_end, end, rate, duration),
                PRIORITY_LOOKAHEAD,
                project,
            )
        self._last_plan = time.time()

    async def _plan_range(self, rate: float, duration: float | None) -> None:
        """Walk a fixed span, a queue-full at a time.

        `targets_for_rate` returns at most `MAX_ENQUEUE_PER_TICK`, always from
        the start of the window, so a span longer than that would re-plan the
        same first 400 targets forever. The cursor advances past what has been
        enqueued instead, and only when the queue has drained enough to take
        more — otherwise a ten-minute span at 24fps would enqueue 14k rows in
        one pass and the memory and the seek-cancel scan both blow up.
        """
        assert self.target_range is not None
        start, end = self.target_range
        cursor = self._range_cursor if self._range_cursor is not None else start
        if cursor > end:
            return
        counts = await self.db.counts(self.project_id)
        outstanding = counts.get(STATUS_PENDING, 0) + counts.get(STATUS_PROCESSING, 0)
        if outstanding > MAX_ENQUEUE_PER_TICK // 2:
            return
        timestamps = targets_for_rate(cursor, end, rate, duration)
        if not timestamps:
            self._range_cursor = end + 1.0  # span exhausted
            return
        await self._enqueue(timestamps, PRIORITY_IMMEDIATE, {})
        self._range_cursor = quantize(timestamps[-1] + 1.0 / rate)

    async def _enqueue(
        self, timestamps: list[float], priority: int, project: dict[str, Any]
    ) -> None:
        if not timestamps:
            return
        # Duplicate prevention: never re-submit a timestamp that already has a
        # completed or in-flight job.
        existing = {
            row["timestamp"]: row["status"]
            for row in await self.db.list_frames(
                self.project_id, start=min(timestamps), end=max(timestamps)
            )
        }
        wanted = [
            ts
            for ts in timestamps
            if existing.get(quantize(ts))
            not in (STATUS_COMPLETED, STATUS_PROCESSING)
        ]
        if not wanted:
            return
        await self.db.enqueue_frames(self.project_id, wanted, priority)

    async def _publish_counts(self) -> None:
        counts = await self.db.counts(self.project_id)
        if counts == self._last_counts:
            return
        self._last_counts = counts
        hub.publish(
            self.project_id,
            {
                "type": "queue_updated",
                "pending": counts.get(STATUS_PENDING, 0),
                "processing": counts.get(STATUS_PROCESSING, 0),
                "completed": counts.get(STATUS_COMPLETED, 0),
                "failed": counts.get("failed", 0),
                "cancelled": counts.get(STATUS_CANCELLED, 0),
            },
        )

    def notify_worker_progress(self) -> None:
        self._wake.set()


class SchedulerRegistry:
    """One scheduler per project, created on demand."""

    def __init__(self) -> None:
        self._schedulers: dict[str, ProjectScheduler] = {}
        self._lock = asyncio.Lock()

    async def get(self, project_id: str, db: Database) -> ProjectScheduler:
        async with self._lock:
            scheduler = self._schedulers.get(project_id)
            if scheduler is None:
                scheduler = ProjectScheduler(project_id, db)
                self._schedulers[project_id] = scheduler
            return scheduler

    def peek(self, project_id: str) -> ProjectScheduler | None:
        return self._schedulers.get(project_id)

    async def stop(self, project_id: str, *, force: bool = False) -> None:
        scheduler = self._schedulers.pop(project_id, None)
        if scheduler is not None:
            await scheduler.stop(force=force)

    async def stop_all(self) -> None:
        for project_id in list(self._schedulers):
            await self.stop(project_id)


registry = SchedulerRegistry()
