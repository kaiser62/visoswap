"""SQLite persistence (aiosqlite). One app-level DB holds projects and frames.

Duplicate prevention lives here: `claim_frame` is the only way a worker may take
a job, and it is a single conditional UPDATE, so two workers can never claim the
same (project, timestamp).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiosqlite

from backend.config import get_settings

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

ALL_STATUSES = (
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    video_path        TEXT,
    video_url         TEXT,
    duration          REAL,
    width             INTEGER,
    height            INTEGER,
    fps               REAL,
    interval          REAL NOT NULL,
    lookahead         REAL NOT NULL,
    backend           TEXT NOT NULL DEFAULT 'engine',
    processing_scale  REAL NOT NULL DEFAULT 1.0,
    processing_width  INTEGER,
    -- See `Settings.generated_format`: webp costs ~0.8s per 1080p frame here.
    generated_format  TEXT NOT NULL DEFAULT 'jpeg',
    -- Absolute path of the source face image the engine swaps onto every
    -- detected target. Project-authored (not a VisoMaster folder reference);
    -- the reference backend sourced faces by name from VisoMaster's folder,
    -- which this repo drops. `EngineFrameGenerator.generate_at` reads this.
    source_face_path TEXT,
    -- Rolling seconds-per-frame, written by the worker. The stream-mode grid is
    -- planned from it, so it must survive a restart: a cold guess that is too
    -- dense floods the queue before the first measurement lands.
    measured_frame_cost REAL,
    full_video_mode   INTEGER NOT NULL DEFAULT 0,
    generation_mode   TEXT NOT NULL DEFAULT 'stream',
    stream_buffer     REAL NOT NULL DEFAULT 15.0,
    status            TEXT NOT NULL DEFAULT 'idle',
    error             TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS frames (
    project_id           TEXT NOT NULL,
    timestamp            REAL NOT NULL,
    source_frame_path    TEXT,
    generated_frame_path TEXT,
    status               TEXT NOT NULL,
    priority             INTEGER NOT NULL DEFAULT 2,
    generation_duration  REAL,
    attempts             INTEGER NOT NULL DEFAULT 0,
    error                TEXT,
    next_retry_at        REAL,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    PRIMARY KEY (project_id, timestamp),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_frames_status
    ON frames (project_id, status, priority, timestamp);
CREATE INDEX IF NOT EXISTS idx_frames_completed
    ON frames (project_id, timestamp) WHERE status = 'completed';
"""


class Database:
    """Thin async wrapper around a single shared aiosqlite connection."""

    def __init__(self, path: Path | None = None) -> None:
        # Resolved lazily. The module-level singleton is constructed at import
        # time, which happens during test collection — before fixtures repoint
        # DATA_DIR at a tmp dir. Binding the path here wrote test projects into
        # the real data/app.db.
        self._explicit_path = Path(path) if path else None
        self._conn: aiosqlite.Connection | None = None

    @property
    def _path(self) -> Path:
        return self._explicit_path or get_settings().db_path

    # -- lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._migrate()
        await self._migrate_project_names()
        await self.recover_stale_jobs()

    async def _migrate(self) -> None:
        """Additive column migration so an existing cache keeps working."""
        cur = await self.conn.execute("PRAGMA table_info(projects)")
        have = {row["name"] for row in await cur.fetchall()}
        additions = {
            "backend": "TEXT NOT NULL DEFAULT 'engine'",
            "generation_mode": "TEXT NOT NULL DEFAULT 'interval'",
            "stream_buffer": "REAL NOT NULL DEFAULT 15.0",
            "measured_frame_cost": "REAL",
            "source_face_path": "TEXT",
        }
        for column, ddl in additions.items():
            if column not in have:
                await self.conn.execute(
                    f"ALTER TABLE projects ADD COLUMN {column} {ddl}"
                )
        await self.conn.commit()

    async def _migrate_project_names(self) -> None:
        """Upgrade generic 'Untitled project' names to unique 10-15 char names derived from source."""
        from backend.services.naming import generate_project_name

        cur = await self.conn.execute(
            "SELECT id, name, video_path, video_url FROM projects"
        )
        rows = await cur.fetchall()
        existing_names = {r["name"] for r in rows if r["name"]}
        for r in rows:
            name = (r["name"] or "").strip()
            if (
                name in ("Untitled project", "My project", "")
                or len(name) < 10
                or len(name) > 15
            ):
                src = r["video_url"] or r["video_path"]
                new_name = generate_project_name(src, existing_names)
                existing_names.add(new_name)
                await self.conn.execute(
                    "UPDATE projects SET name = ? WHERE id = ?",
                    (new_name, r["id"]),
                )
        await self.conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database not connected")
        return self._conn

    async def recover_stale_jobs(self) -> int:
        """After a backend restart nothing is actually in flight.

        Anything left in `processing` is orphaned; reset it to pending so the
        cache and the queue agree again.
        """
        cur = await self.conn.execute(
            "UPDATE frames SET status=?, updated_at=? WHERE status=?",
            (STATUS_PENDING, time.time(), STATUS_PROCESSING),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    # -- projects -------------------------------------------------------------

    async def create_project(self, **fields: Any) -> dict[str, Any]:
        s = get_settings()
        now = time.time()
        project_id = fields.pop("id", None) or uuid.uuid4().hex
        row = {
            "id": project_id,
            "name": fields.get("name") or "Untitled project",
            "video_path": fields.get("video_path"),
            "video_url": fields.get("video_url"),
            "duration": fields.get("duration"),
            "width": fields.get("width"),
            "height": fields.get("height"),
            "fps": fields.get("fps"),
            "interval": float(fields.get("interval") or s.default_interval),
            "lookahead": float(fields.get("lookahead") or s.default_lookahead),
            "backend": "engine",
            "processing_scale": float(fields.get("processing_scale") or 1.0),
            "processing_width": fields.get("processing_width"),
            "generated_format": fields.get("generated_format") or s.generated_format,
            "source_face_path": fields.get("source_face_path"),
            "full_video_mode": int(bool(fields.get("full_video_mode"))),
            "generation_mode": fields.get("generation_mode") or "stream",
            "stream_buffer": float(fields.get("stream_buffer") or 15.0),
            "status": "idle",
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        await self.conn.execute(
            f"INSERT INTO projects ({cols}) VALUES ({marks})", tuple(row.values())
        )
        await self.conn.commit()
        return await self.get_project(project_id)  # type: ignore[return-value]

    async def get_project(self, project_id: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        )
        row = await cur.fetchone()
        return _project_row(row) if row else None

    async def list_projects(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC"
        )
        return [_project_row(r) for r in await cur.fetchall()]

    async def update_project(self, project_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "name", "video_path", "video_url", "duration", "width", "height",
            "fps", "interval", "lookahead", "backend", "processing_scale", "processing_width",
            "generated_format", "full_video_mode", "generation_mode",
            "stream_buffer", "status", "error", "source_face_path",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "full_video_mode" in updates:
            updates["full_video_mode"] = int(bool(updates["full_video_mode"]))
        if not updates:
            return await self.get_project(project_id)
        updates["updated_at"] = time.time()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        await self.conn.execute(
            f"UPDATE projects SET {assignments} WHERE id = ?",
            (*updates.values(), project_id),
        )
        await self.conn.commit()
        return await self.get_project(project_id)

    async def delete_project(self, project_id: str) -> bool:
        cur = await self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await self.conn.execute("DELETE FROM frames WHERE project_id = ?", (project_id,))
        await self.conn.commit()
        return bool(cur.rowcount)

    # -- frames ---------------------------------------------------------------

    async def get_frame(self, project_id: str, ts: float) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT * FROM frames WHERE project_id = ? AND timestamp = ?",
            (project_id, _q(ts)),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_frames(
        self,
        project_id: str,
        *,
        status: str | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM frames WHERE project_id = ?"
        args: list[Any] = [project_id]
        if status:
            sql += " AND status = ?"
            args.append(status)
        if start is not None:
            sql += " AND timestamp >= ?"
            args.append(start)
        if end is not None:
            sql += " AND timestamp <= ?"
            args.append(end)
        sql += " ORDER BY timestamp"
        cur = await self.conn.execute(sql, tuple(args))
        return [dict(r) for r in await cur.fetchall()]

    async def enqueue_frames(
        self, project_id: str, timestamps: Iterable[float], priority: int
    ) -> int:
        """Insert missing timestamps as pending; raise priority of existing ones.

        Never resurrects a completed/processing frame, so no duplicate job can
        be created for a timestamp that already has one.
        """
        now = time.time()
        rows = [(project_id, _q(t), STATUS_PENDING, priority, now, now) for t in timestamps]
        if not rows:
            return 0
        # sqlite reports a bad bind as "parameter 0 - probably unsupported
        # type", naming neither the row nor the value. Name it here instead.
        bad = next(
            (r for r in rows if not isinstance(r[1], float) or r[1] != r[1]), None
        )
        if bad is not None:
            raise ValueError(f"invalid target timestamp {bad[1]!r} for {project_id}")
        await self.conn.executemany(
            "INSERT OR IGNORE INTO frames "
            "(project_id, timestamp, status, priority, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self.conn.executemany(
            # A cancelled row is revived at any priority: the run that cleared
            # it may re-plan the same timestamp at the same priority, and
            # requiring a strictly better one would strand the frame forever.
            "UPDATE frames SET priority = MIN(priority, ?), "
            "status = CASE WHEN status = ? THEN ? ELSE status END, updated_at = ? "
            "WHERE project_id = ? AND timestamp = ? "
            "AND (priority > ? OR status = ?) AND status IN (?, ?)",
            [
                (priority, STATUS_CANCELLED, STATUS_PENDING, now,
                 project_id, _q(t), priority, STATUS_CANCELLED,
                 STATUS_PENDING, STATUS_CANCELLED)
                for t in timestamps
            ],
        )
        await self.conn.commit()
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM frames WHERE project_id = ? AND status = ?",
            (project_id, STATUS_PENDING),
        )
        return int((await cur.fetchone())[0])

    async def max_enqueued_timestamp(self, project_id: str) -> float | None:
        """Highest timestamp currently in the frames table."""
        cur = await self.conn.execute(
            "SELECT MAX(timestamp) FROM frames WHERE project_id = ?",
            (project_id,),
        )
        row = await cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None

    async def claim_next_frame(
        self, project_id: str, playhead: float | None = None
    ) -> dict[str, Any] | None:
        """Atomically move the best pending frame to `processing`.

        Ordering: priority ascending (1 = closest to playback), then timestamp.
        When playhead is provided, frames at or ahead of playhead are preferred
        first, ordered sequentially forward.
        """
        now = time.time()
        if playhead is not None:
            sql = (
                "SELECT timestamp FROM frames WHERE project_id = ? AND status = ? "
                "AND (next_retry_at IS NULL OR next_retry_at <= ?) "
                "ORDER BY (CASE WHEN timestamp >= ? THEN 0 ELSE 1 END) ASC, "
                "priority ASC, timestamp ASC LIMIT 1"
            )
            params = (project_id, STATUS_PENDING, now, playhead)
        else:
            sql = (
                "SELECT timestamp FROM frames WHERE project_id = ? AND status = ? "
                "AND (next_retry_at IS NULL OR next_retry_at <= ?) "
                "ORDER BY priority ASC, timestamp ASC LIMIT 1"
            )
            params = (project_id, STATUS_PENDING, now)
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        if row is None:
            return None
        ts = row["timestamp"]
        upd = await self.conn.execute(
            "UPDATE frames SET status = ?, attempts = attempts + 1, updated_at = ? "
            "WHERE project_id = ? AND timestamp = ? AND status = ?",
            (STATUS_PROCESSING, now, project_id, ts, STATUS_PENDING),
        )
        await self.conn.commit()
        if not upd.rowcount:
            return None  # lost the race; caller retries
        return await self.get_frame(project_id, ts)

    async def set_frame_status(
        self, project_id: str, ts: float, status: str, **fields: Any
    ) -> dict[str, Any] | None:
        allowed = {
            "source_frame_path", "generated_frame_path",
            "generation_duration", "error", "priority", "next_retry_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        updates["status"] = status
        updates["updated_at"] = time.time()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        await self.conn.execute(
            f"UPDATE frames SET {assignments} WHERE project_id = ? AND timestamp = ?",
            (*updates.values(), project_id, _q(ts)),
        )
        await self.conn.commit()
        return await self.get_frame(project_id, ts)

    async def cancel_pending_outside(
        self, project_id: str, start: float, end: float
    ) -> int:
        """Drop not-yet-started work outside the new window (used on seek).

        Only `pending` rows are touched: in-flight jobs finish, completed frames
        stay cached forever.
        """
        cur = await self.conn.execute(
            "UPDATE frames SET status = ?, updated_at = ? WHERE project_id = ? "
            "AND status = ? AND (timestamp < ? OR timestamp > ?)",
            (STATUS_CANCELLED, time.time(), project_id, STATUS_PENDING, start, end),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def delete_frames(self, project_id: str) -> int:
        """Drop the whole frame table for a project.

        Used at the start of a run: nothing is cached across runs, so the rows
        and the images they point at both go.
        """
        cur = await self.conn.execute(
            "DELETE FROM frames WHERE project_id = ?", (project_id,)
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def cancel_all_pending(self, project_id: str) -> int:
        """Drop every queued frame. Used when a run starts, so the new run
        plans from the current playhead instead of inheriting a stale backlog
        from the previous mode, interval or position.

        `processing` rows are left alone — they are already on the GPU — and
        completed frames stay cached.
        """
        cur = await self.conn.execute(
            "UPDATE frames SET status = ?, updated_at = ? "
            "WHERE project_id = ? AND status = ?",
            (STATUS_CANCELLED, time.time(), project_id, STATUS_PENDING),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def cancel_processing(self, project_id: str) -> int:
        """Settle rows whose worker was killed mid-frame.

        Only a forced stop can produce these. A `processing` row normally
        settles itself when its worker returns; one whose worker was cancelled
        without being waited on never will, and an unsettled row sits under the
        recording watermark forever and keeps reporting as work in flight.
        Cancelled is the honest terminal state: the frame was started and never
        produced.
        """
        cur = await self.conn.execute(
            "UPDATE frames SET status = ?, updated_at = ? "
            "WHERE project_id = ? AND status = ?",
            (STATUS_CANCELLED, time.time(), project_id, STATUS_PROCESSING),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def reset_failed(self, project_id: str) -> int:
        cur = await self.conn.execute(
            "UPDATE frames SET status = ?, attempts = 0, error = NULL, "
            "next_retry_at = NULL, updated_at = ? WHERE project_id = ? AND status = ?",
            (STATUS_PENDING, time.time(), project_id, STATUS_FAILED),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def counts(self, project_id: str) -> dict[str, int]:
        cur = await self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM frames WHERE project_id = ? "
            "GROUP BY status",
            (project_id,),
        )
        out = dict.fromkeys(ALL_STATUSES, 0)
        for row in await cur.fetchall():
            out[row["status"]] = int(row["n"])
        return out

    async def recording_watermark(self, project_id: str) -> float:
        """Timestamp below which every job has settled — what the recorder trails.

        A true watermark, not a maximum. `MAX(timestamp)` over settled rows was
        wrong: workers finish out of order, so a high completed timestamp says
        nothing about the pending rows underneath it, and the recorder would
        commit a span whose frames had not been generated yet.

        `failed` and `cancelled` count as settled just as `completed` does — no
        generated frame is coming for those timestamps either, so waiting on
        them would stall the recording forever on a run where one frame
        permanently fails.

        With nothing outstanding the watermark clears the highest settled
        timestamp, so the frame sitting exactly on it is writable rather than
        held one tick short.
        """
        cur = await self.conn.execute(
            "SELECT MIN(timestamp) FROM frames WHERE project_id = ? "
            "AND status IN (?, ?)",
            (project_id, STATUS_PENDING, STATUS_PROCESSING),
        )
        row = await cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])

        cur = await self.conn.execute(
            "SELECT MAX(timestamp) FROM frames WHERE project_id = ? "
            "AND status IN (?, ?, ?)",
            (project_id, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED),
        )
        row = await cur.fetchone()
        return float(row[0]) + 1e-6 if row and row[0] is not None else 0.0

    async def last_generated_timestamp(self, project_id: str) -> float | None:
        """Highest timestamp with a generated frame, or None if there are none.

        This is how far a stopped run is worth recording to. The watermark is
        the wrong bound at stop time: a job left `processing` when its worker
        was cancelled never settles, so the watermark stays pinned underneath
        it and everything generated above would be thrown away. Frames in the
        gap fall back to the source, which is honest — they were never swapped.
        """
        cur = await self.conn.execute(
            "SELECT MAX(timestamp) FROM frames WHERE project_id = ? AND status = ?",
            (project_id, STATUS_COMPLETED),
        )
        row = await cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None

    async def average_duration(self, project_id: str, limit: int = 20) -> float | None:
        cur = await self.conn.execute(
            "SELECT AVG(d) FROM (SELECT generation_duration AS d FROM frames "
            "WHERE project_id = ? AND status = ? AND generation_duration IS NOT NULL "
            "ORDER BY updated_at DESC LIMIT ?)",
            (project_id, STATUS_COMPLETED, limit),
        )
        row = await cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None

    async def record_frame_cost(self, project_id: str, duration: float) -> None:
        """Fold one frame's duration into the project's rolling cost.

        An exponential average rather than a window over the frames table: the
        stream-mode planner reads this on every pass, and it has to react to a
        scene change (a face entering the shot costs an order of magnitude more
        than an empty frame) without re-querying history each time.
        """
        alpha = 0.2
        await self.conn.execute(
            "UPDATE projects SET measured_frame_cost = "
            "COALESCE(measured_frame_cost * (1 - ?) + ? * ?, ?) WHERE id = ?",
            (alpha, alpha, duration, duration, project_id),
        )
        await self.conn.commit()

    async def nearest_completed_at_or_before(
        self, project_id: str, ts: float
    ) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT * FROM frames WHERE project_id = ? AND status = ? "
            "AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
            (project_id, STATUS_COMPLETED, _q(ts) + 1e-6),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


def _q(ts: float) -> float:
    """Quantise to milliseconds so float keys compare exactly in SQLite."""
    return round(float(ts), 3)


def _dump_json(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value)


def _project_row(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data["full_video_mode"] = bool(data.get("full_video_mode"))
    return data


db = Database()
