"""Dashboard logic that is independent of FastAPI: persistence, non-blocking single-flight cache, state/uptime/quota views.

Nothing here reads the environment at import time and nothing here prints or stores a secret: the persisted state holds
non-secret options and timestamps, the persisted job log holds only lines that already passed the redactor.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from vs_common import http_get, write_private

KAGGLE_DEFAULT_MAX_MIN = 90        # MAX_RUNTIME_MIN in visoswap_kaggle.ipynb
HEALTH_KEYS = ("status", "concurrency", "auth", "default_backend", "version", "ffmpeg", "ytdlp")
STATES = ("down", "starting", "queued", "running", "healthy", "stopping", "error", "unknown")
STATE_HELP = {
    "down": "not running", "starting": "an action is starting it", "queued": "waiting for a GPU", "running": "running, backend not answering yet",
    "healthy": "running and answering", "stopping": "being stopped", "error": "needs attention", "unknown": "checking...",
}


# --------------------------------------------------------------------------- persistence
class Store:
    """~/.visoswap/dashboard.json: last non-secret start options and when a backend was last seen starting."""

    def __init__(self, home: Path):
        self.path = home / "dashboard.json"
        self.lock = threading.Lock()

    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, target: str) -> dict:
        with self.lock:
            return dict(self._read().get(target) or {})

    def update(self, target: str, **kw) -> dict:
        with self.lock:
            d = self._read()
            rec = dict(d.get(target) or {})
            for k, v in kw.items():
                if v is None:
                    rec.pop(k, None)
                else:
                    rec[k] = v
            d[target] = rec
            try:
                write_private(self.path, json.dumps(d))
            except OSError:
                pass
            return rec


class JobLog:
    """Persistent launcher job log per target (already redacted lines), size-capped with one rotation."""
    MAX_BYTES = 512 * 1024

    def __init__(self, home: Path):
        self.dir = home / "webui_logs"
        self.lock = threading.Lock()

    def path(self, target: str) -> Path:
        return self.dir / f"{target}.log"

    def append(self, target: str, lines: List[str]) -> None:
        if not lines:
            return
        with self.lock:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                p = self.path(target)
                if p.exists() and p.stat().st_size > self.MAX_BYTES:
                    os.replace(p, p.with_suffix(".log.1"))
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write("\n".join(l.replace("\r", "") for l in lines) + "\n")
            except OSError:
                pass

    def tail(self, target: str, n: int) -> List[str]:
        p = self.path(target)
        try:
            with open(p, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 256 * 1024))
                data = f.read().decode("utf-8", "replace")
        except OSError:
            return []
        return data.splitlines()[-n:]


# --------------------------------------------------------------------------- non-blocking single-flight cache
class Cache:
    """get(key, ttl, fn): fresh -> value; stale -> value + refresh in the background (one at a time per key, however many
    clients ask); never computed -> None + pending (or wait for it with block=True). One CLI call serves every client."""

    def __init__(self):
        self.lock = threading.Lock()
        self.d: Dict[str, dict] = {}

    def peek(self, key: str) -> Optional[Tuple[Any, float]]:
        e = self.d.get(key)
        return (e["v"], time.time() - e["t"]) if e and e["v"] is not None else None

    def drop(self, key: str) -> None:
        with self.lock:
            e = self.d.get(key)
            if e:
                e["t"] = 0.0          # stale, but the last value stays visible while it refreshes

    def get(self, key: str, ttl: float, fn: Callable[[], Any], block: bool = False, timeout: float = 200.0):
        """-> (value or None, age seconds or None, pending bool)"""
        with self.lock:
            e = self.d.setdefault(key, {"v": None, "t": 0.0, "run": None})
            age = time.time() - e["t"]
            if e["v"] is not None and age < ttl:
                return e["v"], age, e["run"] is not None
            ev = e["run"]
            if ev is None:
                ev = e["run"] = threading.Event()
                threading.Thread(target=self._do, args=(e, fn, ev), daemon=True).start()
        if block:
            ev.wait(timeout)
        v = e["v"]
        return v, (time.time() - e["t"]) if v is not None else None, e["run"] is not None

    @staticmethod
    def _do(e: dict, fn: Callable[[], Any], ev: threading.Event) -> None:
        try:
            v = fn()
        except Exception as exc:  # never leak internals into the cache
            v = {"ok": False, "error": f"internal error: {type(exc).__name__}"}
        e["v"], e["t"] = v, time.time()
        e["run"] = None
        ev.set()


# --------------------------------------------------------------------------- health detail
def fetch_health_detail(url: str, fake: bool = False, timeout: float = 8.0) -> Optional[dict]:
    """Whitelisted fields of the backend's own /api/health (reachable without the access key). None = not answering."""
    if fake:
        return {"status": "ok", "concurrency": 3, "auth": "required", "default_backend": "cuda", "version": "fake", "ffmpeg": True, "ytdlp": True}
    try:
        with http_get(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            d = json.loads(r.read(8192).decode("utf-8", "replace"))
    except Exception:
        return None
    return {k: d[k] for k in HEALTH_KEYS if isinstance(d, dict) and k in d and isinstance(d[k], (str, int, float, bool))}


def process_mode(settings: Optional[dict]) -> Optional[str]:
    """VISOSWAP_PROCESS_WORKERS of the running uvicorn: unset/auto = the backend's own policy (processes on an NVIDIA GPU)."""
    if settings is None:
        return None
    v = str(settings.get("VISOSWAP_PROCESS_WORKERS", "auto")).strip().lower()
    return {"1": "processes", "true": "processes", "0": "threads", "false": "threads"}.get(v, "auto (processes on a GPU)")


# --------------------------------------------------------------------------- per-target view
def normalize_state(target: str, status: Optional[dict], busy_action: Optional[str]) -> str:
    if busy_action in ("launch", "start", "restart", "provision"):
        return "starting"
    if busy_action == "stop":
        return "stopping"
    if not status:
        return "unknown"
    if status.get("ok") is False:
        return "error"
    raw = str(status.get("state") or "").upper()
    if raw == "QUEUED":
        return "queued"
    if raw == "RUNNING":
        return "healthy" if status.get("healthy") else "running"
    if raw in ("MISSING", "COMPLETE", "CANCEL", "NO_SESSION", "SESSION_ONLY", "DRY-RUN"):
        return "down"
    if raw in ("ERROR", "AUTH", "UNKNOWN"):
        return "error"
    return "unknown"


def compose_target(target: str, status: Optional[dict], health: Optional[dict], rec: dict, busy_action: Optional[str], now: float,
                   age: Optional[float] = None, pending: bool = False) -> dict:
    """One backend card. Anything not known stays None (the UI shows 'unknown', never a guess)."""
    st = normalize_state(target, status, busy_action)
    status = status or {}
    raw = str(status.get("state") or "").upper()
    out: Dict[str, Any] = {
        "target": target, "state": st, "state_help": STATE_HELP[st], "raw_state": raw or None, "pending": pending,
        "summary": status.get("summary") or "", "error": status.get("error") or "", "next": status.get("next") or "",
        "url": status.get("url"), "mobile_url": (status.get("url") or "") + "/mobile" if status.get("url") else None,
        "healthy": bool(status.get("healthy")), "busy": bool(busy_action), "busy_action": busy_action, "age_s": None if age is None else round(age, 1),
        "health": health, "concurrency": (health or {}).get("concurrency"), "auth": (health or {}).get("auth"),
        "gpu": None, "process_mode": None, "settings": None, "session": status.get("session"), "kernel": status.get("kernel"),
        "uptime_s": None, "uptime_source": None, "max_minutes": None, "time_left_s": None, "last_options": rec.get("last_options") or {},
        "warnings": [],
    }
    live = st in ("healthy", "running", "starting") and not busy_action
    if target == "colab":
        out["gpu"] = status.get("gpu")
        if status.get("settings") is not None and raw in ("RUNNING", "SESSION_ONLY"):
            out["settings"] = status.get("settings")
            out["process_mode"] = process_mode(status.get("settings"))
        if live and status.get("uptime_s") is not None:
            out["uptime_s"], out["uptime_source"] = int(status["uptime_s"]), "backend process age"
        if raw == "SESSION_ONLY":
            out["warnings"].append("A Colab GPU session exists but the backend is not running: it still uses compute units. Start (or Stop) it.")
    else:
        out["gpu"] = "T4 x2 (requested)" if raw in ("RUNNING", "QUEUED") else None
        start = status.get("started_at") if raw == "RUNNING" else None
        src = status.get("started_source") if start else None
        if raw == "RUNNING" and not start and rec.get("started_at"):
            start, src = rec["started_at"], rec.get("started_source") or "dashboard"
        if live and start:
            out["uptime_s"], out["uptime_source"] = max(0, int(now - start)), (src or "estimate") + " (estimate)"
            mm = rec.get("max_minutes")
            out["max_minutes"] = KAGGLE_DEFAULT_MAX_MIN if mm is None else int(mm)
            if out["max_minutes"] > 0:
                out["time_left_s"] = max(0, int(start + out["max_minutes"] * 60 - now))
                if out["time_left_s"] < 600:
                    out["warnings"].append(f"The kernel stops itself in about {out['time_left_s'] // 60 + 1} min (max {out['max_minutes']} min).")
                if rec.get("max_minutes") is None:
                    out["max_minutes_source"] = f"assumed default {KAGGLE_DEFAULT_MAX_MIN} min (not launched from here)"
    if st == "running" and not busy_action and out["uptime_s"] is not None and out["uptime_s"] > 900:
        out["warnings"].append("The backend process is running but the public URL does not answer: check the tunnel log.")
    return out


# --------------------------------------------------------------------------- quotas
def quota_view(target: str, res: Optional[dict], low_hours: float = 3.0, low_units: float = 10.0, now: Optional[float] = None) -> dict:
    """-> {available, summary, warn: None|low|empty, warn_text, ...} from a launcher --quota result."""
    if res is None:
        return {"available": False, "pending": True, "summary": "checking...", "warn": None}
    q = res.get("quota")
    base = {"available": bool(q), "pending": False, "summary": res.get("summary") or res.get("error") or "", "warn": None, "warn_text": "",
            "login_required": bool(res.get("login_required"))}
    if not q:
        return base
    if target == "kaggle":
        g = q.get("gpu") or {}
        rem, tot = g.get("remaining"), g.get("total")
        base.update(gpu={"used": g.get("used"), "remaining": rem, "total": tot, "refresh_at": g.get("refresh_at")},
                    tpu=q.get("tpu"), unit="h", percent_left=round(100 * rem / tot) if rem is not None and tot else None)
        if rem is not None:
            if rem <= 0:
                base["warn"], base["warn_text"] = "empty", "No Kaggle GPU hours left until the weekly reset."
            elif rem < low_hours or (tot and rem / tot < 0.1):
                base["warn"], base["warn_text"] = "low", f"Only {rem:g} GPU hours left this week."
    else:
        bal, rate = q.get("balance"), q.get("rate")
        hours = round(bal / rate, 1) if bal is not None and rate else None
        base.update(balance=bal, rate=rate, assignments=q.get("assignments"), hours_left=hours, unit="units")
        if bal is not None:
            if bal <= 0:
                base["warn"], base["warn_text"] = "empty", "No Colab compute units left: an L4 cannot start (T4 may still work)."
            elif bal < low_units or (hours is not None and hours < low_hours):
                base["warn"], base["warn_text"] = "low", f"Only {bal:g} compute units left" + (f" (about {hours:g} h at the current rate)." if hours is not None else ".")
    return base
