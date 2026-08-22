"""Executes one frame job at a time.

Every worker runs the same loop: claim a job atomically, extract the source
frame, hand it to the configured `FrameGenerator`, persist the result. A failure
only ever affects the frame being processed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.config import get_settings
from backend.models.database import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
)
from backend.services import cache
from backend.services.events import hub
from backend.services.ffmpeg import FFmpegError, extract_frame
from backend.services.generator import FrameGenerator, build_generator

if TYPE_CHECKING:
    from backend.services.scheduler import ProjectScheduler

log = logging.getLogger(__name__)

IDLE_SLEEP = 0.5
ERROR_BACKOFF = 3.0


async def run_worker(scheduler: ProjectScheduler, index: int) -> None:
    """Long-lived per-project worker. Survives generator outages."""
    project_id = scheduler.project_id
    generator: FrameGenerator | None = None
    generator_key: tuple[Any, ...] | None = None

    try:
        while scheduler.running:
            project = await scheduler.db.get_project(project_id)
            if project is None:
                return

            frame = await scheduler.db.claim_next_frame(project_id)
            if frame is None:
                await asyncio.sleep(IDLE_SLEEP)
                continue

            # Rebuild the generator whenever the project's config changed.
            key = (
                project.get("backend"),
                project.get("generated_format"),
            )
            if generator is None or key != generator_key:
                if generator is not None:
                    await generator.unbind()
                    await generator.close()
                generator = build_generator(project, 0.0, index)
                generator_key = key
                # Per-run setup the backend only pays for once. Binding can fail
                # on a config problem or a cold GPU; hand the claimed frame back
                # and retry rather than burning its attempt budget or killing
                # the worker, which would strand the project for the whole run.
                try:
                    await generator.bind(project)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await scheduler.db.set_frame_status(
                        project_id, float(frame["timestamp"]), STATUS_PENDING
                    )
                    await generator.close()
                    generator, generator_key = None, None
                    log.warning(
                        "[WORKER] project=%s index=%d bind failed: %s",
                        project_id, index, exc,
                    )
                    await asyncio.sleep(ERROR_BACKOFF)
                    continue

            await _process_frame(scheduler, project, frame, generator)
            scheduler.notify_worker_progress()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("[WORKER] project=%s index=%d crashed", project_id, index)
    finally:
        if generator is not None:
            await generator.unbind()
            await generator.close()


async def _process_frame(
    scheduler: ProjectScheduler,
    project: dict[str, Any],
    frame: dict[str, Any],
    generator: FrameGenerator,
) -> None:
    db = scheduler.db
    project_id = project["id"]
    ts = float(frame["timestamp"])
    started = time.monotonic()

    hub.publish(project_id, {"type": "generation_started", "timestamp": ts})

    try:
        fmt = project["generated_format"]
        # The extension carries the requested format through to the generator;
        # it used to be hardcoded to .webp regardless of the project setting.
        name = f"{project_id}_{cache.timestamp_key(ts)}.{'jpg' if fmt == 'jpeg' else fmt}"
        source_path: Path | None = None
        if generator.decodes_own_source:
            # No extraction: the generator holds the video open. Round-tripping
            # the source frame through webp costs ~0.26s, four times the swap.
            result = await generator.generate_at(ts, name)
        else:
            source_path = await _ensure_source_frame(project, frame, ts)
            result = await generator.generate(source_path.read_bytes(), name)

        dest = cache.frame_path(project_id, ts, generated=True, fmt=fmt)
        _atomic_write(dest, result.data)

        duration = time.monotonic() - started
        await db.set_frame_status(
            project_id,
            ts,
            STATUS_COMPLETED,
            generated_frame_path=cache.to_relative(dest),
            # None when the generator read the video itself: no source frame
            # was ever written to disk, so there is nothing to point at.
            source_frame_path=cache.to_relative(source_path) if source_path else None,
            generation_duration=duration,
            error=None,
        )
        # Feeds the stream-mode grid: it is planned from what the backend has
        # actually been costing, not from the video's fps.
        await db.record_frame_cost(project_id, duration)
        log.info(
            "[GENERATION] project=%s timestamp=%.3f prompt=%s status=completed duration=%.2fs",
            project_id, ts, result.prompt_id, duration,
        )
        hub.publish(
            project_id,
            {
                "type": "generation_completed",
                "timestamp": ts,
                "path": f"/api/projects/{project_id}/frame/{cache.timestamp_key(ts)}",
                "duration": round(duration, 3),
            },
        )
    except asyncio.CancelledError:
        # Shutdown mid-flight: hand the job back so it is not lost.
        await db.set_frame_status(project_id, ts, STATUS_PENDING)
        raise
    except Exception as exc:  # one frame failing must not stop the worker
        await _handle_failure(scheduler, project, ts, frame, exc)


async def _ensure_source_frame(
    project: dict[str, Any], frame: dict[str, Any], ts: float
) -> Path:
    """Extract the source frame, reusing a cached one when it already exists."""
    settings = get_settings()
    project_id = project["id"]
    existing = frame.get("source_frame_path")
    if existing:
        candidate = cache.to_absolute(existing)
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate

    dest = cache.frame_path(
        project_id, ts, generated=False, fmt=settings.source_format
    )
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    source = project.get("video_path") or project.get("video_url")
    if not source:
        raise FFmpegError("project has no video source")

    scale_width = _processing_width(project)
    return await extract_frame(source, ts, dest, scale_width=scale_width)


def _processing_width(project: dict[str, Any]) -> int | None:
    """Never upscale: only an explicit width or a scale < 1 changes anything."""
    explicit = project.get("processing_width")
    if explicit:
        return int(explicit)
    scale = float(project.get("processing_scale") or 1.0)
    width = int(project.get("width") or 0)
    if scale >= 1.0 or width <= 0:
        return None
    return max(64, int(width * scale) // 2 * 2)


async def _handle_failure(
    scheduler: ProjectScheduler,
    project: dict[str, Any],
    ts: float,
    frame: dict[str, Any],
    exc: Exception,
) -> None:
    settings = get_settings()
    project_id = project["id"]
    # `claim_next_frame` already counted this attempt; do not count it twice.
    attempts = int(frame.get("attempts") or 1)
    message = f"{type(exc).__name__}: {exc}"[:500]
    # Retrying a frame with no face in it re-runs detection on identical pixels
    # and fails identically, burning a worker slot playback needs elsewhere.
    retryable = True

    if retryable and attempts <= settings.max_retries:
        # Exponential backoff keeps an engine outage from spinning.
        delay = settings.retry_backoff * (2 ** (attempts - 1))
        await scheduler.db.set_frame_status(
            project_id, ts, STATUS_PENDING,
            error=message, next_retry_at=time.time() + delay,
        )
        log.warning(
            "[GENERATION] project=%s timestamp=%.3f status=retry attempt=%d/%d error=%s",
            project_id, ts, attempts, settings.max_retries, message,
        )
        await asyncio.sleep(min(ERROR_BACKOFF, delay))
        return

    await scheduler.db.set_frame_status(project_id, ts, STATUS_FAILED, error=message)
    log.error(
        "[GENERATION] project=%s timestamp=%.3f status=failed attempts=%d error=%s",
        project_id, ts, attempts, message,
    )
    hub.publish(
        project_id,
        {"type": "generation_failed", "timestamp": ts, "error": message},
    )


def _atomic_write(dest: Path, data: bytes) -> None:
    """A partially written cache file must never look like a valid hit."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
