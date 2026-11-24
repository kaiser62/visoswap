#!/usr/bin/env python3
"""Host bridge: lets the containerised dashboard drive the launchers ON THE HOST (the Colab CLI keeps its Google login in
the macOS Keychain, which a Linux container cannot reach).

Server side (run on the Mac, `run.sh bridge start`): a tiny job server. The container sends STRUCTURED requests
{target, op, options, confirm}; the bridge re-validates them with argspec, builds the launcher flags itself (host paths,
host key file) and runs the launcher the same way the dashboard would natively. It never executes client-supplied argv.
Auth: the dashboard access token (the shared ~/.visoswap/webui_token), constant-time compare, failure lockout, loopback
bind by default. It logs nothing per request (no paths, no bodies); pasted secrets only travel as the launcher's stdin.

Client side (imported by app.py): BridgeProc, a Popen look-alike (iterable stdout, writable stdin, poll/wait/kill).
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Deque, Dict, Optional
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import argspec  # noqa: E402
import execution  # noqa: E402

MAX_BODY = 70_000
MAX_LINES = 5000


# =========================================================================== server
class Run:
    def __init__(self, target: str, op: str, proc, redact):
        self.id = uuid.uuid4().hex[:16]
        self.target, self.op, self.proc, self.redact = target, op, proc, redact
        self.lines: list = []
        self.done = False
        self.rc: Optional[int] = None
        self.cond = threading.Condition()
        self.finished_at = 0.0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        try:
            for raw in self.proc.stdout:
                with self.cond:
                    self.lines.append(self.redact(raw.rstrip("\n")))
                    del self.lines[:-MAX_LINES]
                    self.cond.notify_all()
        finally:
            self.rc = self.proc.wait()
            with self.cond:
                self.done, self.finished_at = True, time.time()
                self.cond.notify_all()


class BridgeState:
    def __init__(self, token: str, home: Path):
        from vs_common import Redactor, load_env_file, secrets_of
        self.token, self.home = token, home
        self.runs: Dict[str, Run] = {}
        self.busy: Dict[str, str] = {}
        self.lock = threading.Lock()
        self.fails: Dict[str, Deque[float]] = {}
        self._redactor_args = (Redactor, load_env_file, secrets_of)

    def redactor(self):
        Redactor, load_env_file, secrets_of = self._redactor_args
        r = Redactor([self.token])
        for t in argspec.TARGETS:
            for s in secrets_of(load_env_file(self.home / f"{t}.env")):
                r.add(s)
        k = self.home / argspec.API_KEY_FILE
        try:
            r.add(k.read_text().strip())
        except OSError:
            pass
        return r

    def start(self, body: dict) -> Run:
        target, op = body.get("target"), body.get("op")
        options, confirm = body.get("options") or {}, body.get("confirm") is True
        stdin_text = body.get("stdin")
        flags = argspec.build_flags(str(target), str(op), options, self.home, confirm)      # ValueError -> 400
        if op in argspec.STDIN_OPS:
            if not isinstance(stdin_text, str) or not stdin_text.strip() or len(stdin_text) > 65536:
                raise ValueError("this action needs its secret on stdin")
            stdin_text = stdin_text.rstrip("\n") + "\n"
        else:
            stdin_text = None
        with self.lock:
            if op in argspec.JOB_OPS:
                cur = self.runs.get(self.busy.get(target, ""))
                if cur and not cur.done:
                    raise RuntimeError("busy")
            red = self.redactor()
            if stdin_text:
                red.add(stdin_text.strip())
            proc = execution.spawn_local(target, flags, stdin_text=stdin_text, keep_stdin=op in argspec.RELAY_OPS)
            run = Run(target, op, proc, red)
            self.runs[run.id] = run
            if op in argspec.JOB_OPS:
                self.busy[target] = run.id
            for k in [k for k, r in self.runs.items() if r.done and time.time() - r.finished_at > 900][:50]:
                self.runs.pop(k, None)
            return run


def make_handler(st: BridgeState):
    class H(BaseHTTPRequestHandler):
        server_version = "visoswap-bridge"
        sys_version = ""

        def log_message(self, *a):            # silence: no request line (ids, paths) is ever logged
            pass

        def _send(self, code: int, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _auth(self) -> bool:
            ip = self.client_address[0]
            now = time.time()
            f = st.fails.setdefault(ip, deque())
            while f and now - f[0] > 300:
                f.popleft()
            if len(f) >= 10:
                self._send(429, {"detail": "too many failed attempts"})
                return False
            h = self.headers.get("Authorization", "")
            given = h[7:] if h.lower().startswith("bearer ") else ""
            if not given or not hmac.compare_digest(given.encode(), st.token.encode()):
                f.append(now)
                self._send(401, {"detail": "invalid or missing token"})
                return False
            return True

        def _body(self) -> Optional[dict]:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    raise ValueError
                d = json.loads(self.rfile.read(n).decode() or "{}")
                if not isinstance(d, dict):
                    raise ValueError
                return d
            except (ValueError, OSError):
                self._send(400, {"detail": "malformed request"})
                return None

        def do_GET(self):
            if not self._auth():
                return
            u = urlparse(self.path)
            if u.path == "/v1/ping":
                return self._send(200, {"ok": True, "platform": sys.platform, "fake": execution.fake()})
            parts = u.path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["v1", "run"]:
                run = st.runs.get(parts[2])
                if not run:
                    return self._send(404, {"detail": "unknown run"})
                q = parse_qs(u.query)
                try:
                    since = max(0, int(q.get("since", ["0"])[0]))
                    wait = min(10.0, max(0.0, float(q.get("wait", ["0"])[0])))
                except ValueError:
                    return self._send(400, {"detail": "malformed request"})
                with run.cond:
                    if len(run.lines) <= since and not run.done and wait:
                        run.cond.wait(wait)
                    return self._send(200, {"lines": run.lines[since:], "next": len(run.lines), "done": run.done, "rc": run.rc})
            self._send(404, {"detail": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            body = self._body()
            if body is None:
                return
            parts = urlparse(self.path).path.strip("/").split("/")
            if parts == ["v1", "run"]:
                try:
                    run = st.start(body)
                except ValueError as e:
                    return self._send(400, {"detail": str(e)})
                except RuntimeError:
                    return self._send(409, {"detail": "an action is already running for this target"})
                except Exception:
                    return self._send(500, {"detail": "internal error"})
                return self._send(200, {"run": run.id})
            if len(parts) == 4 and parts[:2] == ["v1", "run"]:
                run = st.runs.get(parts[2])
                if not run:
                    return self._send(404, {"detail": "unknown run"})
                if parts[3] == "kill":
                    if not run.done:
                        run.proc.kill()
                    return self._send(200, {"ok": True})
                if parts[3] == "input":
                    line = body.get("line")
                    if run.done or run.op not in argspec.RELAY_OPS or not isinstance(line, str) or not line or len(line) > 4096 or "\n" in line or "\r" in line:
                        return self._send(409, {"detail": "not accepting input"})
                    run.redact.add(line)
                    try:
                        run.proc.stdin.write(line + "\n")
                        run.proc.stdin.flush()
                    except (OSError, ValueError):
                        return self._send(409, {"detail": "the process has ended"})
                    return self._send(200, {"ok": True})
            self._send(404, {"detail": "not found"})

    return H


def serve(host: str, port: int) -> None:
    from vs_common import visoswap_home
    home = visoswap_home()
    tp = home / "webui_token"
    try:
        token = tp.read_text().strip()
    except OSError:
        token = ""
    if not token:
        print(f"bridge: no dashboard token at {tp}; run tools/launchers/webui/run.sh token first", file=sys.stderr)
        raise SystemExit(1)
    httpd = ThreadingHTTPServer((host, port), make_handler(BridgeState(token, home)))
    httpd.daemon_threads = True
    print(f"visoswap host bridge listening on http://{host}:{port} (authenticates with the dashboard token)", flush=True)
    httpd.serve_forever()


# =========================================================================== client
class BridgeError(Exception):
    pass


def request(base: str, token: str, method: str, path: str, body: Optional[dict] = None, timeout: float = 30.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "visoswap-dashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(2_000_000).decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read(4096).decode()).get("detail", "")
        except Exception:
            detail = ""
        raise BridgeError(f"host bridge refused ({e.code}): {detail}" if detail else f"host bridge refused ({e.code})")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise BridgeError("host bridge unreachable: " + type(e).__name__)


class _RemoteStdin:
    def __init__(self, proc: "BridgeProc"):
        self.p = proc

    def write(self, s: str) -> None:
        line = s.strip()
        try:
            request(self.p.base, self.p.token, "POST", f"/v1/run/{self.p.id}/input", {"line": line}, timeout=15)
        except BridgeError as e:
            raise OSError(str(e))

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class BridgeProc:
    """Popen look-alike over the bridge. Construction starts the run (BridgeError if the bridge refuses)."""

    def __init__(self, base: str, token: str, payload: dict):
        self.base, self.token = base, token
        self.id = request(base, token, "POST", "/v1/run", payload)["run"]
        self.stdin = _RemoteStdin(self)
        self.stdout = self._lines()
        self.returncode: Optional[int] = None

    def _lines(self):
        since, errors = 0, 0
        while True:
            try:
                r = request(self.base, self.token, "GET", f"/v1/run/{self.id}?since={since}&wait=2", timeout=20)
                errors = 0
            except BridgeError as e:
                errors += 1
                if errors >= 5:
                    self.returncode = 1
                    yield json.dumps({"t": "result", "ok": False, "error": str(e)})
                    return
                time.sleep(1)
                continue
            for line in r["lines"]:
                yield line + "\n"
            since = r["next"]
            if r["done"]:
                self.returncode = r.get("rc")
                return

    def poll(self) -> Optional[int]:
        return self.returncode

    def wait(self) -> int:
        for _ in self.stdout:        # drain if the caller stopped early
            pass
        return self.returncode if self.returncode is not None else 1

    def kill(self) -> None:
        try:
            request(self.base, self.token, "POST", f"/v1/run/{self.id}/kill", {}, timeout=10)
        except BridgeError:
            pass


def ping(base: str, token: str) -> dict:
    return request(base, token, "GET", "/v1/ping", timeout=4)


if __name__ == "__main__":
    serve(os.environ.get("BRIDGE_HOST", "127.0.0.1"), int(os.environ.get("BRIDGE_PORT", "8766")))
