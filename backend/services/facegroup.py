"""Background embedding job for similarity grouping.

Embedding a few hundred faces on a CPU is seconds to tens of seconds, which is far
too slow to hold an HTTP request open for. The plan asks for a job with progress:
past a small threshold the endpoint answers 202 and the client polls, and progress
is also announced on the existing WebSocket hub so a connected page can update
without polling at all.

One job at a time, deliberately. The embedder serialises its work anyway (one
shared CPU session behind a lock), so two concurrent grouping runs would just
queue behind each other while both held a job id; one server-side job also means
one consistent progress number to report.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.services import faceembed, facecluster, facestore

log = logging.getLogger(__name__)

#: Above this many uncached faces the work moves to a job and the endpoint
#: answers 202 instead of blocking.
SYNC_LIMIT = 25
#: The event name the frontend listens for.
PROGRESS_EVENT = "faces_embedding_progress"
#: Progress is published on a pseudo-project topic: the library is machine-global,
#: so there is no project id to hang it on and the page must not invent one.
GROUPING_TOPIC = "__faces__"


class EmbeddingBusy(RuntimeError):
    """A grouping job is already running."""


@dataclass
class EmbedJob:
    """One embedding run. `snapshot` is the wire shape."""

    id: str
    state: str = "pending"        # pending | running | done | failed | cancelled
    total: int = 0
    done: int = 0
    skipped: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    #: (face_id -> embedding) collected while it ran, dropped once consumed.
    result: Optional[list[str]] = None
    _task: Optional[asyncio.Task] = None
    _cancel: Optional[threading.Event] = None

    @property
    def active(self) -> bool:
        return self.state in ("pending", "running")

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "state": self.state,
            "total": self.total,
            "done": self.done,
            "skipped": self.skipped,
            "error": self.error,
            "percent": round(100.0 * self.done / self.total, 1) if self.total else 100.0,
        }


_JOBS: dict[str, EmbedJob] = {}
_CURRENT: Optional[EmbedJob] = None
_LOCK = threading.Lock()


def current() -> Optional[EmbedJob]:
    """The running job, if any."""
    with _LOCK:
        job = _CURRENT
        return job if job is not None and job.active else None


def get(job_id: str) -> Optional[EmbedJob]:
    with _LOCK:
        return _JOBS.get(job_id)


def _publish(job: EmbedJob) -> None:
    """Announce progress. Fire-and-forget: a WebSocket consumer must never be
    able to slow the job down, and the hub is already non-blocking."""
    from backend.services.events import hub

    try:
        hub.publish(GROUPING_TOPIC, {"type": PROGRESS_EVENT, **job.snapshot()})
    except Exception:  # pragma: no cover - defensive, publishing cannot fail the job
        log.debug("could not publish embedding progress", exc_info=True)


def _run(face_ids: list[str], job: EmbedJob) -> None:
    """Embed every id in the list. Runs in a thread; never raises."""
    try:
        for face_id in face_ids:
            if job._cancel is not None and job._cancel.is_set():
                job.state = "cancelled"
                return
            try:
                vec = faceembed.embed_face_id(face_id)
            except faceembed.EmbedderUnavailable:
                raise
            except Exception as exc:      # one bad image must not kill the run
                log.warning("[FACEGROUP] %s failed to embed: %s", face_id, exc)
                vec = None
            if vec is None:
                job.skipped += 1
            job.done += 1
            _publish(job)
        job.state = "done"
        job.result = [f for f in face_ids if faceembed.has_embedding(f)]
    except faceembed.EmbedderUnavailable as exc:
        job.state = "failed"
        job.error = str(exc)
        log.warning("[FACEGROUP] embeddings unavailable: %s", exc)
    except Exception as exc:  # pragma: no cover - defensive
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        log.exception("[FACEGROUP] embedding job failed")
    finally:
        job.finished_at = time.time()
        _publish(job)


def start(face_ids: list[str]) -> EmbedJob:
    """Kick off a background embedding run. One at a time."""
    global _CURRENT
    with _LOCK:
        if _CURRENT is not None and _CURRENT.active:
            raise EmbeddingBusy("an embedding job is already running")
        job = EmbedJob(id=uuid.uuid4().hex, state="running", total=len(face_ids))
        job._cancel = threading.Event()
        _CURRENT = job
        _JOBS[job.id] = job
        for stale in list(_JOBS)[:-20]:      # keep a small history, not a leak
            _JOBS.pop(stale, None)
    job._task = asyncio.get_event_loop().run_in_executor(None, _run, face_ids, job)
    _publish(job)
    return job


def cancel(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or not job.active or job._cancel is None:
            return False
        job._cancel.set()
        return True


def reset() -> None:
    """Drop all state. Tests only."""
    global _CURRENT
    with _LOCK:
        _JOBS.clear()
        _CURRENT = None


# --------------------------------------------------------------------------- the shared path
def uncached(face_ids: list[str]) -> list[str]:
    """Those with neither an embedding nor a "no face" marker."""
    return [
        f for f in face_ids
        if not faceembed.has_embedding(f) and not faceembed.is_marked_none(f)
    ]
