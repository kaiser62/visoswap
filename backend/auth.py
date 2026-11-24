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


@router.post(LOGIN_PATH)
async def login(body: LoginBody, request: Request) -> JSONResponse:
    """Trade the key for an HttpOnly cookie so media elements and sockets work."""
    expected = get_settings().api_key
    if not expected:
        return JSONResponse({"ok": True, "auth": "off"})
    if not _matches(body.key, expected):
        return JSONResponse({"detail": DENIED_DETAIL}, status_code=401)
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
