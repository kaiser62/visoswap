"""Shared-secret access control (plan B3).

One key, set with ``VISOSWAP_API_KEY``. Empty means auth is off, which keeps LAN
development and the existing tests unchanged. When set, every API, WebSocket and
``/url=`` shortcut request must carry it, either as the ``X-VisoSwap-Key``
header (native clients) or the ``visoswap_key`` cookie (browsers: ``<video>``,
``<img>``, ``<a download>`` and ``WebSocket`` cannot send headers).

This is a *pure ASGI* middleware on purpose. Starlette's ``BaseHTTPMiddleware``
never sees WebSocket scopes, so it would leave ``/ws/`` open. The key is never
accepted as a query parameter (it would land in tunnel and proxy logs) and is
never logged.
"""

from __future__ import annotations

import hmac
import math
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.config import get_settings

KEY_HEADER = "x-visoswap-key"
KEY_COOKIE = "visoswap_key"
COOKIE_MAX_AGE = 30 * 24 * 3600
DENIED_DETAIL = "missing or invalid access key"

HEALTH_PATH = "/api/health"
LOGIN_PATH = "/api/auth/session"
#: FastAPI's schema UI and JSON: open by default, and a public tunnel makes them a
#: map of the whole API. Protected whenever a key is set.
PROTECTED_DOCS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
#: failed-login throttle: this many wrong keys per IP inside the window answers 429
LOGIN_MAX_FAILS = 10
LOGIN_WINDOW_S = 60.0
#: cap on tracked addresses: a client that rotates spoofed headers must not grow the dict forever
LOGIN_MAX_TRACKED_IPS = 4096

router = APIRouter(tags=["auth"])


def auth_required() -> bool:
    return bool(get_settings().api_key)


def _matches(candidate: str | None, expected: str) -> bool:
    if not candidate:
        return False
    # Compare bytes: hmac.compare_digest raises TypeError on non-ASCII str, which
    # would turn a garbage header into a 500 instead of a 401.
    return hmac.compare_digest(
        candidate.encode("utf-8", "surrogatepass"), expected.encode("utf-8")
    )


def key_ok(conn: HTTPConnection) -> bool:
    """True when auth is off or the connection carries the right key."""
    expected = get_settings().api_key
    if not expected:
        return True
    return _matches(conn.headers.get(KEY_HEADER), expected) or _matches(
        conn.cookies.get(KEY_COOKIE), expected
    )


def is_protected(path: str, method: str, query: str) -> bool:
    """Whether a request to ``path`` needs the key. Exemptions are exact path + method."""
    method = method.upper()
    if method == "OPTIONS":  # CORS preflight
        return False
    if path == HEALTH_PATH and method in ("GET", "HEAD"):
        return False
    if path == LOGIN_PATH and method == "POST":
        return False
    if path in PROTECTED_DOCS:
        # FastAPI's own schema UI. Left open it advertises every route on a public
        # tunnel; nothing there is needed unauthenticated.
        return True
    if path == "/api" or path.startswith("/api/"):
        return True
    if path.startswith("/ws/"):
        return True
    if path.startswith("/url"):  # the /url=<link> shortcut
        return True
    if path == "/" and any(
        k == "url" for k, _ in parse_qsl(query, keep_blank_values=True)
    ):
        return True
    return False


class AuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket") or not get_settings().api_key:
            await self.app(scope, receive, send)
            return

        method = "GET" if kind == "websocket" else scope.get("method", "GET")
        query = scope.get("query_string", b"").decode("latin-1")
        if not is_protected(scope["path"], method, query) or key_ok(
            HTTPConnection(scope)
        ):
            await self.app(scope, receive, send)
            return

        if kind == "websocket":
            # Reject before accept: the client sees HTTP 403 on the handshake.
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        resp = JSONResponse({"detail": DENIED_DETAIL}, status_code=401)
        await resp(scope, receive, send)


class LoginBody(BaseModel):
    key: str = ""


class _LoginLimiter:
    """Failed-login throttle, keyed by client IP. In-memory and per-process: it exists to
    stop online guessing against a public tunnel, not to be a shared quota across workers.

    Only *failures* are recorded, and the key is compared before the limiter is consulted,
    so a caller holding the right key is never blocked (a slow retry after a typo still works).
    """

    def __init__(self, max_fails: int = LOGIN_MAX_FAILS, window_s: float = LOGIN_WINDOW_S) -> None:
        self.max_fails = max_fails
        self.window_s = window_s
        self._lock = threading.Lock()
        self._fails: Dict[str, List[float]] = {}

    @staticmethod
    def client_ip(request: Request) -> str:
        """The tunnel sits in front of us, so the socket peer is always the tunnel.

        CF-Connecting-IP is set by Cloudflare and is the only value worth trusting when
        present; X-Forwarded-For is the fallback for other proxies and its first hop is the
        original client. Both are spoofable by anyone who reaches the app directly, which is
        why this is a throttle and not an authorization decision.
        """
        for header in ("cf-connecting-ip", "x-forwarded-for"):
            raw = request.headers.get(header, "")
            if raw:
                first = raw.split(",")[0].strip()
                if first:
                    return first
        return request.client.host if request.client else "unknown"

    def retry_after(self, ip: str, now: Optional[float] = None) -> int:
        """0 when allowed, else whole seconds to wait before the next attempt."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._prune(ip, now)
            if len(hits) < self.max_fails:
                return 0
            # the window is a sliding one: the block lifts when the oldest failure ages out
            return max(1, int(math.ceil(self.window_s - (now - hits[0]))))

    def fail(self, ip: str, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._prune(ip, now)
            hits.append(now)
            if len(hits) > self.max_fails * 4:      # bounded: a flood cannot grow this forever
                del hits[: -self.max_fails]
            self._fails[ip] = hits
            # A guesser can rotate spoofed headers, so the number of tracked addresses has to
            # be bounded too, or the throttle becomes its own memory leak. Evict the least
            # recently active buckets; the flood loses its own history first.
            if len(self._fails) > LOGIN_MAX_TRACKED_IPS:
                for stale in sorted(self._fails, key=lambda k: self._fails[k][-1])[: len(self._fails) - LOGIN_MAX_TRACKED_IPS]:
                    del self._fails[stale]

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

    def clear(self) -> None:
        with self._lock:
            self._fails.clear()

    def _prune(self, ip: str, now: float) -> List[float]:
        hits = [t for t in self._fails.get(ip, ()) if now - t < self.window_s]
        if hits:
            self._fails[ip] = hits
        else:
            self._fails.pop(ip, None)
        return hits


login_limiter = _LoginLimiter()


@router.post(LOGIN_PATH)
async def login(body: LoginBody, request: Request) -> JSONResponse:
    """Trade the key for an HttpOnly cookie so media elements and sockets work."""
    expected = get_settings().api_key
    if not expected:
        return JSONResponse({"ok": True, "auth": "off"})
    ip = _LoginLimiter.client_ip(request)
    # Compare first: a caller with the right key is never throttled, and a guesser cannot
    # reach the cookie path without beating the constant-time comparison anyway.
    if not _matches(body.key, expected):
        wait = login_limiter.retry_after(ip)
        if wait:
            return JSONResponse(
                {"detail": "too many failed attempts"},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
        login_limiter.fail(ip)
        return JSONResponse({"detail": DENIED_DETAIL}, status_code=401)
    login_limiter.reset(ip)
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    secure = (proto or request.url.scheme) == "https"
    resp = JSONResponse({"ok": True, "auth": "required"})
    resp.set_cookie(
        KEY_COOKIE,
        body.key,
        max_age=COOKIE_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return resp
