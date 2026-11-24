"""VisoSwap launcher dashboard: monitor and control the Kaggle and Colab backends from any device.

It runs the SAME launcher scripts (`--json` mode) as subprocesses (locally, or on the Mac through the host bridge); no
launcher logic lives here. Security model: see docs/launchers.md. In short: bearer token required for everything under /api,
fixed (target, action) allowlist with validated options (argspec), one job per target, rate limits, secrets redacted from
all output, no endpoint ever returns env-file or credential contents.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(os.environ.get("LAUNCHER_DIR") or HERE.parent)))
from vs_common import Redactor, load_env_file, secrets_of, visoswap_home  # noqa: E402
import argspec  # noqa: E402
import bridge as B  # noqa: E402
import dashboard as D  # noqa: E402
import execution  # noqa: E402

FAKE = os.environ.get("LAUNCHER_FAKE") == "1"
TARGETS = {"kaggle": "kaggle_launch.py", "colab": "colab_launch.py"}
ACTIONS = argspec.JOB_OPS
RELAY_ACTIONS = argspec.RELAY_OPS      # jobs that keep stdin open and may ask the browser for a secret / code
GOOGLE_HOSTS = ("accounts.google.com",)
IN_DOCKER = os.environ.get("WEBUI_IN_DOCKER") == "1"
DOCKER_COLAB_MSG = ("Colab cannot keep its Google login inside this container (no file-based keyring is configured and no host bridge). Set "
                    "PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring (the image does), run the host bridge on a Mac "
                    "(tools/launchers/webui/run.sh bridge start) or import a token file below.")
#: server-side constants shown when a login cannot run from the UI (browser/Keychain/terminal needed)
LOGIN_CMD = {"kaggle": "tools/launchers/kaggle-launch.sh --login", "colab": "tools/launchers/colab-launch.sh --login"}   # run from the repo root
COLAB_EXPORT_CMD = "security find-generic-password -s google-colab-cli -a oauth_token -w > colab-token.json"       # macOS, on a machine where Colab is logged in
CHECK_FIELDS = ("id", "service", "state", "detail", "fix", "runnable", "blocking")
RESULT_FIELDS = ("ok", "state", "healthy", "url", "mobile_url", "summary", "error", "next", "session", "gpu", "kernel",
                 "configured", "log_url", "tunnel", "quota", "lines", "started_at", "started_source", "uptime_s", "settings",
                 "login_required", "sha", "uvicorn")
MAX_LINES = 2000
STATUS_TTL = 20.0
QUOTA_TTL = 600.0
QUOTA_MIN_AGE = 30.0
LOG_TTL = 10.0


def token_path() -> Path:
    return visoswap_home() / "webui_token"


def load_or_create_token() -> str:
    p = token_path()
    if p.is_file():
        return p.read_text().strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    if IN_DOCKER:     # container stdout lands in `docker logs`: never print the token there
        print(f"\n  VisoSwap dashboard: access token created in {p} (read it on the host: cat ~/.visoswap/webui_token)\n", flush=True)
    else:             # shown ONCE, here only; it is never returned by any endpoint
        print(f"\n  VisoSwap launcher web UI access token (also in {p}):\n  {tok}\n", flush=True)
    return tok


def bridge_url() -> str:
    return os.environ.get("LAUNCHER_BRIDGE_URL", "").strip()


def file_keyring() -> bool:
    """True when the colab CLI's keyring is a plain file under the persisted volume (Linux/Docker: no Keychain, no Secret Service)."""
    return "keyrings.alt" in os.environ.get("PYTHON_KEYRING_BACKEND", "")


def colab_login_possible() -> bool:
    return (not IN_DOCKER) or file_keyring() or use_bridge("colab")


def use_bridge(target: str) -> bool:
    """Targets whose launcher runs on the host through the bridge (default: colab only; LAUNCHER_BRIDGE_TARGETS=colab,kaggle)."""
    return bool(bridge_url()) and target in [x.strip() for x in os.environ.get("LAUNCHER_BRIDGE_TARGETS", "colab").split(",")]


class State:
    def __init__(self):
        self.token = load_or_create_token()
        home = visoswap_home()
        self.lock = threading.Lock()
        self.jobs: Dict[str, dict] = {}
        self.busy: Dict[str, str] = {}              # target -> job id
        self.cache = D.Cache()                      # status:<t> check:<t> quota:<t> logs:... bridge
        self.store = D.Store(home)
        self.joblog = D.JobLog(home)
        self.fail_times: Dict[str, Deque[float]] = {}
        self.action_times: Deque[float] = deque()

    def redactor(self) -> Redactor:
        r = Redactor([self.token])
        for t in TARGETS:
            for s in secrets_of(load_env_file(visoswap_home() / f"{t}.env")):
                r.add(s)
        try:                                        # the backend access key (the file the launcher uploads) must never reach a log view
            r.add((visoswap_home() / argspec.API_KEY_FILE).read_text().strip())
        except OSError:
            pass
        return r


S: Optional[State] = None


def state() -> State:
    global S
    if S is None:
        S = State()
    return S


def clean_result(d: dict) -> dict:
    out = {k: d[k] for k in RESULT_FIELDS if k in d}
    if isinstance(d.get("checks"), list):   # nested whitelist
        out["checks"] = [{k: (str(c[k]).replace(str(execution.launcher_dir()), "tools/launchers") if k == "fix" else c[k]) for k in CHECK_FIELDS if k in c}
                         for c in d["checks"] if isinstance(c, dict)]
    return out


def redact_obj(red, o):
    """Redact every string inside a parsed JSON value (per field: a secret cannot be split by JSON escaping)."""
    if isinstance(o, str):
        return red(o)
    if isinstance(o, list):
        return [redact_obj(red, x) for x in o]
    if isinstance(o, dict):
        return {k: redact_obj(red, v) for k, v in o.items()}
    return o


def safe_google_url(u) -> str:
    try:
        p = urlparse(str(u))
    except ValueError:
        return ""
    return str(u) if p.scheme == "https" and p.hostname in GOOGLE_HOSTS else ""


def spawn(target: str, op: str, options: Optional[dict], stdin_text: Optional[str], relay: bool, confirm: bool):
    """A running launcher: a local Popen, or a BridgeProc when this target runs on the host. ValueError / BridgeError on refusal."""
    if use_bridge(target):
        tok = state().token
        return B.BridgeProc(bridge_url(), tok, {"target": target, "op": op, "options": options or {}, "confirm": confirm,
                                                **({"stdin": stdin_text} if stdin_text else {})})
    flags = argspec.build_flags(target, op, options, visoswap_home(), confirm)
    return execution.spawn_local(target, flags, stdin_text=stdin_text, keep_stdin=relay)


def run_launcher(target: str, op: str, options: Optional[dict] = None, on_line=None, timeout: float = 4 * 3600,
                 stdin_text: Optional[str] = None, job: Optional[dict] = None, extra_secrets=(), confirm: bool = False) -> dict:
    """job != None: relay mode. stdin stays open so the input endpoint can answer the launcher's prompt events."""
    st = state()
    red = st.redactor()
    for s in (stdin_text or "", *extra_secrets):
        red.add(s.strip())
    result: dict = {"ok": False, "error": "launcher produced no result"}
    try:
        p = spawn(target, op, options, stdin_text, job is not None, confirm)
    except ValueError as e:
        return {"ok": False, "error": red(str(e))}
    except B.BridgeError as e:
        return {"ok": False, "error": red(str(e)), "next": "start the host bridge: tools/launchers/webui/run.sh bridge start"}
    if job is not None:
        job["proc"] = p
        job["redactor"] = red
    t0 = time.time()
    try:
        for raw in p.stdout:
            raw = raw.rstrip("\n")
            try:
                obj = json.loads(raw)
            except ValueError:
                obj = {"t": "log", "msg": raw}
            if not isinstance(obj, dict):
                obj = {"t": "log", "msg": raw}
            if obj.get("t") == "result":
                result = clean_result(redact_obj(red, obj))
            elif obj.get("t") == "prompt" and job is not None:
                job["prompt"] = {"kind": "code" if obj.get("kind") == "code" else "secret", "msg": red(str(obj.get("msg", "")))[:300],
                                 "url": safe_google_url(obj.get("url"))}
                job["state"] = "waiting"
            elif on_line:
                on_line(red(str(obj.get("msg", ""))), obj.get("t"))
            if time.time() - t0 > timeout:
                p.kill()
                result = {"ok": False, "error": "timed out"}
                break
    finally:
        if p.poll() is None and job is not None and job.get("cancelled"):
            p.kill()
        p.wait()
        if job is not None:
            job.pop("proc", None)
            job["prompt"] = None
    return result


def busy_action(target: str) -> Optional[str]:
    st = state()
    jid = st.busy.get(target)
    if not jid:
        return None
    j = st.jobs.get(jid)
    return j["action"] if j else "config"


def drop_caches(target: str, quota: bool = False) -> None:
    st = state()
    for k in (f"status:{target}", f"check:{target}") + ((f"quota:{target}",) if quota else ()):
        st.cache.drop(k)


def start_job(target: str, action: str, options: Optional[dict] = None, confirm: bool = False) -> str:
    st = state()
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "target": target, "action": action, "state": "running", "lines": [], "result": None, "started": time.time(), "prompt": None}
    st.jobs[jid] = job
    st.busy[target] = jid
    peek = st.cache.peek(f"status:{target}")
    was_up = bool(peek and peek[0].get("healthy"))
    if len(st.jobs) > 50:
        for k in sorted(st.jobs, key=lambda k: st.jobs[k]["started"])[:10]:
            if st.jobs[k]["state"] not in ("running", "waiting"):
                st.jobs.pop(k, None)
    st.joblog.append(target, [f"---- {time.strftime('%Y-%m-%d %H:%M:%S')} {target} {action} ----"])

    def work():
        try:
            def on_line(msg, t):
                line = ("== " if t == "step" else "") + msg
                job["lines"].append(line)
                del job["lines"][:-MAX_LINES]
                st.joblog.append(target, [line])
            job["result"] = run_launcher(target, action, options, on_line, job=job if action in RELAY_ACTIONS else None, confirm=confirm)
            job["state"] = "done" if job["result"].get("ok") else "failed"
            if job.get("cancelled"):
                job["state"], job["result"] = "failed", {"ok": False, "error": "cancelled"}
            r = job["result"]
            st.joblog.append(target, [("DONE" if r.get("ok") else "FAILED: " + str(r.get("error", ""))) + (("  Next: " + r["next"]) if r.get("next") else "")])
            if r.get("ok"):
                if action in ("launch", "start", "restart") and (action == "restart" or not was_up):
                    mm = (options or {}).get("max_minutes") if target == "kaggle" else None
                    st.store.update(target, started_at=time.time(), started_source="launched from the dashboard", max_minutes=mm)
                elif action == "stop":
                    st.store.update(target, started_at=None, max_minutes=None, started_source=None)
        except Exception as e:  # never leak internals
            job["result"] = {"ok": False, "error": f"web UI error: {type(e).__name__}"}
            job["state"] = "failed"
        finally:
            with st.lock:
                st.busy.pop(target, None)
            drop_caches(target, quota=True)
    threading.Thread(target=work, daemon=True).start()
    return jid


# --------------------------------------------------------------------------- app
app = FastAPI(title="VisoSwap launchers", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def require_auth(request: Request) -> None:
    st = state()
    ip = request.client.host if request.client else "?"
    now = time.time()
    fails = st.fail_times.setdefault(ip, deque())
    while fails and now - fails[0] > 300:
        fails.popleft()
    if len(fails) >= 10:
        raise HTTPException(429, "too many failed attempts; wait a few minutes")
    auth = request.headers.get("authorization", "")
    given = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not given or not hmac.compare_digest(given.encode(), st.token.encode()):
        fails.append(now)
        raise HTTPException(401, "invalid or missing token")


def _status_fn(target: str):
    """One launcher --status plus the backend's own /api/health, run in the background by the cache (single flight)."""
    st = state()
    res = run_launcher(target, "status", timeout=150)
    now = time.time()
    raw = str(res.get("state") or "").upper()
    health = None
    if res.get("ok") is not False and res.get("url") and raw in ("RUNNING", "SESSION_ONLY"):
        health = D.fetch_health_detail(res["url"], fake=FAKE)
    if res.get("ok") is not False:                    # remember when we first saw it healthy (fallback for the uptime display)
        rec = st.store.get(target)
        if raw == "RUNNING" and res.get("healthy") and not rec.get("started_at"):
            st.store.update(target, started_at=now, started_source="first seen healthy by the dashboard")
        elif raw in ("MISSING", "COMPLETE", "CANCEL", "NO_SESSION") and rec.get("started_at"):
            st.store.update(target, started_at=None, started_source=None, max_minutes=None)
    return {**res, "health_detail": health, "ts": now}


def _status(target: str, force: bool = False, block: bool = True):
    """-> (data dict, age, pending, busy job id). data is {} until the first result exists."""
    st = state()
    busy = st.busy.get(target)
    peek = st.cache.peek(f"status:{target}")
    if busy:                                          # a running job holds the Colab kernel: do not queue a probe behind it
        return (peek[0] if peek else {"state": "BUSY", "summary": "an action is running"}), (peek[1] if peek else None), False, busy
    v, age, pending = st.cache.get(f"status:{target}", 5 if force else STATUS_TTL, lambda: _status_fn(target), block=block)
    return (v or {}), age, pending, None


@app.get("/api/targets", dependencies=[Depends(require_auth)])
def targets(refresh: int = 0):
    out = {}
    for t in TARGETS:
        v, age, pending, busy = _status(t, bool(refresh))
        out[t] = {**{k: x for k, x in v.items() if k not in ("health_detail", "ts")}, "busy": bool(busy), "job": busy}
    return out


def login_methods() -> dict:
    """Which login routes exist per service and whether each works in THIS deployment (the UI renders from this)."""
    colab_bridge = use_bridge("colab")
    colab_relay = colab_login_possible()
    return {
        "kaggle": [
            {"id": "token", "label": "Paste API token", "available": True, "hint": "kaggle.com > Settings > API > Create New Token"},
            {"id": "json", "label": "Upload or paste kaggle.json", "available": True, "hint": "kaggle.com > Settings > API > Create Legacy API Key (downloads kaggle.json)"},
            {"id": "cli", "label": "Native command", "available": True, "command": LOGIN_CMD["kaggle"]}],
        "colab": [
            {"id": "relay", "label": "Google sign-in (paste the code)", "available": colab_relay,
             "hint": "runs on the host through the bridge" if colab_bridge else ("the login is kept in a file inside the state volume" if IN_DOCKER and file_keyring() else ""), "why": "" if colab_relay else DOCKER_COLAB_MSG},
            {"id": "token-file", "label": "Import a token file", "available": True,
             "hint": "On a Mac where `colab` is already logged in, run the command below, upload the file here, then delete it. It holds a long-lived credential.",
             "command": COLAB_EXPORT_CMD},
            {"id": "cli", "label": "Native command", "available": True, "command": LOGIN_CMD["colab"]}],
        "cloudflare": [
            {"id": "token", "label": "Paste tunnel token", "available": True, "hint": "Zero Trust > Networks > Tunnels > your tunnel > token (stored mode 600, never shown)"},
            {"id": "api-token", "label": "Cloudflare API token", "available": True, "hint": "Account: Cloudflare Tunnel Edit, Zone: DNS Edit (used once, not stored)"},
            {"id": "cli", "label": "Native command", "available": True, "command": "cloudflared tunnel login"}],
    }


def _check_fn(t: str):
    return run_launcher(t, "check", timeout=180)


@app.get("/api/checks", dependencies=[Depends(require_auth)])
def checks(refresh: int = 0):
    """Per-service login/token state (read-only `--check`), cached briefly. Returns whitelisted fields only."""
    st = state()
    out = {}
    for t in TARGETS:
        if st.busy.get(t):      # a running job holds the Colab kernel; do not queue a probe behind it
            peek = st.cache.peek(f"check:{t}")
            out[t] = peek[0] if peek else {"ok": True, "summary": "an action is running; checks resume afterwards", "checks": []}
            continue
        v, _, _ = st.cache.get(f"check:{t}", 5 if refresh else STATUS_TTL, lambda t=t: _check_fn(t), block=True)
        out[t] = v or {"ok": True, "checks": []}
    cb = use_bridge("colab")
    return {"targets": out, "login_commands": LOGIN_CMD, "in_docker": IN_DOCKER, "login_methods": login_methods(),
            "login_ui": {"kaggle": True, "colab": colab_login_possible()},
            "login_note": {"kaggle": "", "colab": "" if colab_login_possible() else DOCKER_COLAB_MSG}}


def _begin(target: str):
    st = state()
    with st.lock:
        now = time.time()
        while st.action_times and now - st.action_times[0] > 60:
            st.action_times.popleft()
        if len(st.action_times) >= 6:
            raise HTTPException(429, "too many actions; wait a minute")
        if target in st.busy:
            raise HTTPException(409, f"{target} already has a running action")
        st.action_times.append(now)
        st.busy[target] = "pending"


def _secret_op(target: str, op: str, stdin_text: str, timeout: float, secrets_=()):
    _begin(target)
    st = state()
    try:
        return run_launcher(target, op, timeout=timeout, stdin_text=stdin_text, extra_secrets=secrets_)
    finally:
        st.busy.pop(target, None)
        drop_caches(target)


@app.post("/api/targets/{target}/cloudflare-token", dependencies=[Depends(require_auth)])
def cloudflare_token(target: str, body: dict):
    """Store a pasted tunnel token (mode 600, via the launcher's stdin). Never echoed; refused if one is stored."""
    if target not in TARGETS:
        raise HTTPException(404, "unknown target")
    tok = str((body or {}).get("token", "")).strip()
    if not (40 <= len(tok) <= 4096) or any(ch.isspace() for ch in tok):
        raise HTTPException(400, "does not look like a tunnel token")
    res = _secret_op(target, "cf-token", tok + "\n", 60)
    return {"ok": bool(res.get("ok")), "error": res.get("error", "") if not res.get("ok") else ""}


@app.post("/api/targets/{target}/cloudflare-api-token", dependencies=[Depends(require_auth)])
def cloudflare_api_token(target: str, body: dict):
    """Create the tunnel + DNS from a Cloudflare API token. It is piped to the launcher once, used in memory and
    never stored; only the resulting tunnel token is (mode 600)."""
    if target not in TARGETS:
        raise HTTPException(404, "unknown target")
    tok = str((body or {}).get("token", "")).strip()
    if not (20 <= len(tok) <= 512) or any(ch.isspace() for ch in tok):
        raise HTTPException(400, "does not look like a Cloudflare API token")
    res = _secret_op(target, "cf-api-token", tok + "\n", 180)
    return {"ok": bool(res.get("ok")), "summary": res.get("summary", ""), "error": res.get("error", "") if not res.get("ok") else ""}


def _json_secret(body: dict, keys, what: str) -> tuple:
    """-> (raw text, [secret values]). The file content is parsed only to learn which values to redact."""
    raw = (body or {}).get("json")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 8192:
        raise HTTPException(400, f"missing {what}")
    try:
        d = json.loads(raw)
        vals = [str(d[k]) for k in keys]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, f"that is not a valid {what}")
    return raw.strip() + "\n", vals


@app.post("/api/targets/kaggle/kaggle-json", dependencies=[Depends(require_auth)])
def kaggle_json(body: dict):
    """Backup Kaggle login: a kaggle.json ({username, key}). Validated by the CLI in isolation, then stored mode 600."""
    raw, vals = _json_secret(body, ("username", "key"), "kaggle.json")
    res = _secret_op("kaggle", "kaggle-json", raw, 120, [vals[1]])
    return {"ok": bool(res.get("ok")), "summary": res.get("summary", ""), "error": res.get("error", "") if not res.get("ok") else ""}


@app.post("/api/targets/colab/colab-token", dependencies=[Depends(require_auth)])
def colab_token(body: dict):
    """Backup Colab login: import an authorized-user token file (no browser, no keyring needed)."""
    raw, vals = _json_secret(body, ("client_id", "client_secret", "refresh_token"), "Colab token file")
    res = _secret_op("colab", "colab-token", raw, 120, vals)
    return {"ok": bool(res.get("ok")), "summary": res.get("summary", ""), "error": res.get("error", "") if not res.get("ok") else ""}


@app.get("/api/dashboard", dependencies=[Depends(require_auth)])
def dashboard(refresh: int = 0):
    """Everything one card needs, from caches (never blocks on a CLI). pending=true: a refresh is in flight, poll again."""
    st = state()
    now = time.time()
    out, any_pending = {}, False
    for t in TARGETS:
        v, age, pending, busy = _status(t, bool(refresh), block=False)
        ba = busy_action(t) if busy else None
        out[t] = D.compose_target(t, v or None, (v or {}).get("health_detail"), st.store.get(t), ba, now, age, pending and not v)
        any_pending = any_pending or pending
    br = {"configured": bool(bridge_url()), "targets": [t for t in TARGETS if use_bridge(t)], "ok": None, "detail": ""}
    if br["configured"]:
        def ping():
            try:
                return {"ok": True, "info": B.ping(bridge_url(), st.token)}
            except B.BridgeError as e:
                return {"ok": False, "detail": str(e)}
        pv, _, _ = st.cache.get("bridge", 15, ping, block=True, timeout=6)
        br["ok"], br["detail"] = (pv or {}).get("ok"), (pv or {}).get("detail", "")
    return {"targets": out, "bridge": br, "now": now, "pending": any_pending, "in_docker": IN_DOCKER, "fake": FAKE,
            "login_methods": login_methods()}


def _quota_fn(t: str):
    return run_launcher(t, "quota", timeout=150)


@app.get("/api/quotas", dependencies=[Depends(require_auth)])
def quotas(refresh: int = 0):
    """Kaggle GPU hours and Colab compute units. Cached 10 min; refresh=1 re-queries but never more often than every 30 s,
    so the CLIs are not hammered. Non-blocking: pending=true means a refresh is running, poll again."""
    st = state()
    low_h = float(os.environ.get("QUOTA_LOW_HOURS", "3"))
    low_u = float(os.environ.get("QUOTA_LOW_UNITS", "10"))
    out, pending_any = {}, False
    for t in TARGETS:
        v, age, pending = st.cache.get(f"quota:{t}", QUOTA_MIN_AGE if refresh else QUOTA_TTL, lambda t=t: _quota_fn(t), block=False)
        view = D.quota_view(t, v, low_h, low_u)
        view["age_s"] = None if age is None else round(age, 1)
        view["pending"] = pending
        pending_any = pending_any or pending
        out[t] = view
    return {**out, "pending": pending_any}


@app.get("/api/logs/{target}/{source}", dependencies=[Depends(require_auth)])
def logs(target: str, source: str, lines: int = 200, refresh: int = 0):
    """Persistent launcher job log (source=job) or the remote backend's log (Kaggle: kernel; Colab: uvicorn, tunnel). Tail only, redacted."""
    st = state()
    if target not in TARGETS:
        raise HTTPException(404, "unknown target")
    if not argspec.MIN_LINES <= lines <= argspec.MAX_LINES:
        raise HTTPException(400, f"lines must be {argspec.MIN_LINES}..{argspec.MAX_LINES}")
    if source == "job":
        red = st.redactor()
        return {"target": target, "source": "job", "ok": True, "lines": [red(l) for l in st.joblog.tail(target, lines)], "age_s": 0}
    if source not in argspec.LOG_SOURCES[target]:
        raise HTTPException(404, "unknown log source")
    if target == "colab" and st.busy.get(target):
        raise HTTPException(409, "an action is running; remote logs resume afterwards")
    v, age, _ = st.cache.get(f"logs:{target}:{source}:{lines}", 3 if refresh else LOG_TTL,
                             lambda: run_launcher(target, "logs", {"source": source, "lines": lines}, timeout=170), block=True, timeout=180)
    v = v or {"ok": False, "error": "no result"}
    red = st.redactor()
    return {"target": target, "source": source, "ok": bool(v.get("ok")), "error": red(str(v.get("error", ""))) if not v.get("ok") else "",
            "lines": [red(str(l)) for l in (v.get("lines") or [])][-lines:], "age_s": None if age is None else round(age, 1)}


@app.post("/api/targets/{target}/{action}", dependencies=[Depends(require_auth)])
def act(target: str, action: str, body: Optional[dict] = None):
    body = body or {}
    options = body.get("options") or {}
    if target not in TARGETS or action not in ACTIONS:
        raise HTTPException(404, "unknown target or action")
    try:
        argspec.validate(target, action, options, body.get("confirm") is True)
        if not use_bridge(target):       # also checks host-side things (the API key file) for a local run; the bridge does its own
            argspec.build_flags(target, action, options, visoswap_home(), body.get("confirm") is True)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if action == "login" and target == "colab" and not colab_login_possible():
        raise HTTPException(400, DOCKER_COLAB_MSG)
    st = state()
    _begin(target)
    try:
        if action in ("launch", "start", "restart") and options:
            st.store.update(target, last_options=options)
        return {"job": start_job(target, action, options, body.get("confirm") is True)}
    except Exception:
        st.busy.pop(target, None)
        raise


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def job(job_id: str, since: int = 0):
    j = state().jobs.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    return {"id": j["id"], "target": j["target"], "action": j["action"], "state": j["state"],
            "lines": j["lines"][since:], "next": len(j["lines"]), "result": j["result"], "prompt": j.get("prompt")}


@app.post("/api/jobs/{job_id}/input", dependencies=[Depends(require_auth)])
def job_input(job_id: str, body: dict):
    """Answer the running login job's prompt (a pasted token or sign-in code). The value is written to the launcher's
    stdin and nowhere else: not logged, not returned, not kept."""
    j = state().jobs.get(job_id)
    if not j or j.get("prompt") is None or not j.get("proc"):
        raise HTTPException(409, "this job is not waiting for input")
    val = str((body or {}).get("value", "")).strip()
    if not val or len(val) > 4096 or "\n" in val or "\r" in val:
        raise HTTPException(400, "empty or malformed value")
    j["redactor"].add(val)
    try:
        j["proc"].stdin.write(val + "\n")
        j["proc"].stdin.flush()
    except OSError:
        raise HTTPException(409, "the login process has already ended")
    j["prompt"] = None
    j["state"] = "running"
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
def job_cancel(job_id: str):
    j = state().jobs.get(job_id)
    if not j or j["state"] not in ("running", "waiting"):
        raise HTTPException(409, "not running")
    j["cancelled"] = True
    p = j.get("proc")
    if p:
        p.kill()
    return {"ok": True}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.exception_handler(RequestValidationError)
async def bad_request(request, exc):
    # the default 422 body echoes the submitted input, which may be a token
    return JSONResponse({"detail": "malformed request"}, status_code=422)


@app.exception_handler(Exception)
async def boom(request, exc):
    return JSONResponse({"detail": "internal error"}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    state()
    uvicorn.run(app, host=os.environ.get("WEBUI_HOST", "127.0.0.1"), port=int(os.environ.get("WEBUI_PORT", "8765")),
                log_level="warning", server_header=False)
