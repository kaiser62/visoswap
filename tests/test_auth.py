"""Shared-secret auth (backend/auth.py): header or cookie, HTTP and WebSocket."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.config import get_settings
from backend.main import create_app
from backend.models.database import db

KEY = "s3cret-Key-ünï-42"
ASCII_KEY = "s3cret-key-42"
H = "X-VisoSwap-Key"


def _app(monkeypatch, tmp_path, key):
    monkeypatch.setenv("MODELS_VERIFY_MODE", "fast")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///{}".format(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    if key is None:
        monkeypatch.delenv("VISOSWAP_API_KEY", raising=False)
    else:
        monkeypatch.setenv("VISOSWAP_API_KEY", key)
    get_settings.cache_clear()
    db._conn = None
    return create_app()


@pytest.fixture()
def off(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, None)
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture()
def on(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, ASCII_KEY)
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_key_unset_everything_open(off):
    assert off.get("/api/projects").status_code == 200
    assert off.get("/api/health").json()["auth"] == "off"
    r = off.post("/api/auth/session", json={"key": "anything"})
    assert r.status_code == 200 and "set-cookie" not in r.headers


def test_health_open_and_reports_required(on):
    body = on.get("/api/health").json()
    assert body["auth"] == "required" and body["status"] == "ok" and body["version"]


def test_no_key_401(on):
    r = on.get("/api/projects")
    assert r.status_code == 401
    assert r.json() == {"detail": "missing or invalid access key"}


def test_wrong_key_401(on):
    assert on.get("/api/projects", headers={H: "nope"}).status_code == 401


def test_header_works(on):
    assert on.get("/api/projects", headers={H: ASCII_KEY}).status_code == 200


def test_non_ascii_header_is_401_not_500(on):
    r = on.get("/api/projects", headers={H: "ünï".encode("utf-8")})
    assert r.status_code == 401


def test_non_ascii_key_refused_at_config_load(monkeypatch, tmp_path):
    with pytest.raises(Exception, match="printable ASCII"):
        _app(monkeypatch, tmp_path, KEY)
    get_settings.cache_clear()


def test_punctuated_key_roundtrips_via_header_and_cookie(monkeypatch, tmp_path):
    key = "aB3-_.~!@#$%^&*()+=/:?[]{}|<>"
    app = _app(monkeypatch, tmp_path, key)
    with TestClient(app) as c:
        assert c.get("/api/projects", headers={H: key.encode("utf-8")}).status_code == 200
        assert c.post("/api/auth/session", json={"key": key}).status_code == 200
        assert c.get("/api/projects").status_code == 200
    get_settings.cache_clear()


def test_query_param_key_not_accepted(on):
    assert on.get(f"/api/projects?key={ASCII_KEY}").status_code == 401


def test_login_sets_cookie_and_cookie_works(on):
    assert on.post("/api/auth/session", json={"key": "bad"}).status_code == 401
    r = on.post("/api/auth/session", json={"key": ASCII_KEY})
    assert r.status_code == 200
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc and "path=/" in sc
    assert "max-age=2592000" in sc and "secure" not in sc
    assert on.get("/api/projects").status_code == 200  # cookie jar


def test_cookie_secure_when_forwarded_https(on):
    r = on.post(
        "/api/auth/session", json={"key": ASCII_KEY},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert "secure" in r.headers["set-cookie"].lower()


def test_health_exemption_is_exact(on):
    assert on.get("/api/healthx").status_code == 401
    assert on.post("/api/health").status_code == 401
    assert on.get("/api/auth/session").status_code == 401


def test_url_shortcuts_protected_and_create_nothing(on):
    assert on.get("/url=https://example.com/a.mp4", follow_redirects=False).status_code == 401
    r = on.get("/?url=https%3A%2F%2Fexample.com%2Fa.mp4", follow_redirects=False)
    assert r.status_code == 401
    assert on.get("/api/projects", headers={H: ASCII_KEY}).json() == []


def test_ui_shell_open(on):
    assert on.get("/").status_code == 200
    assert on.get("/mobile").status_code == 200


def test_ws_rejected_without_key_and_ok_with(on):
    with pytest.raises(WebSocketDisconnect):
        with on.websocket_connect("/ws/projects/abc"):
            pass
    pid = on.post("/api/projects", json={"name": "x"}, headers={H: ASCII_KEY}).json()["id"]
    with on.websocket_connect(f"/ws/projects/{pid}", headers={H: ASCII_KEY}) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_ws_cookie_works(on):
    on.post("/api/auth/session", json={"key": ASCII_KEY})
    pid = on.post("/api/projects", json={"name": "x"}).json()["id"]
    with on.websocket_connect(f"/ws/projects/{pid}") as ws:
        assert ws.receive_json()["type"] == "hello"


def test_cors_outermost(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, ASCII_KEY)
    origin = get_settings().cors_origin_list[0]
    with TestClient(app) as c:
        pre = c.options(
            "/api/projects",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert pre.status_code == 200
        r = c.get("/api/projects", headers={"Origin": origin})
        assert r.status_code == 401
        assert r.headers.get("access-control-allow-origin") == origin


def test_frame_and_face_cache_headers_private():
    import inspect

    from backend.api import faces, playback

    assert "private, max-age=31536000, immutable" in inspect.getsource(playback)
    assert faces._IMMUTABLE["Cache-Control"].startswith("private")


def test_no_key_leak_in_logs_or_repr(on, caplog):
    caplog.set_level(logging.DEBUG)
    on.get("/api/projects", headers={H: ASCII_KEY})
    on.get("/api/projects", headers={H: "wrong-key-value"})
    on.post("/api/auth/session", json={"key": ASCII_KEY})
    on.post("/api/auth/session", json={"key": "wrong-key-value"})
    with pytest.raises(WebSocketDisconnect):
        with on.websocket_connect("/ws/projects/zzz"):
            pass
    assert ASCII_KEY not in caplog.text and "wrong-key-value" not in caplog.text
    assert ASCII_KEY not in repr(get_settings())


def test_key_stripped(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path, ASCII_KEY + "\n")
    with TestClient(app) as c:
        assert c.get("/api/projects", headers={H: ASCII_KEY}).status_code == 200


# --- docs/schema: open when auth is off, gated when it is on -------------------
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def test_docs_open_when_auth_is_off(off):
    for p in DOC_PATHS:
        assert off.get(p).status_code == 200, p


def test_docs_require_the_key_when_auth_is_on(on):
    for p in DOC_PATHS:
        r = on.get(p)
        assert r.status_code == 401, p
        assert r.json() == {"detail": "missing or invalid access key"}
        assert on.get(p, headers={H: "nope"}).status_code == 401, p


def test_docs_reachable_with_the_key(on):
    for p in DOC_PATHS:
        assert on.get(p, headers={H: ASCII_KEY}).status_code == 200, p
    assert on.get("/openapi.json", headers={H: ASCII_KEY}).json()["info"]["version"]


def test_docs_cookie_login_also_opens_them(on):
    on.post("/api/auth/session", json={"key": ASCII_KEY})
    assert on.get("/docs").status_code == 200


def test_oauth2_redirect_is_protected_too(on):
    # /docs/oauth2-redirect is a real route; leaving it open would be a hole in the same UI
    assert on.get("/docs/oauth2-redirect").status_code == 401
    assert on.get("/docs/oauth2-redirect", headers={H: ASCII_KEY}).status_code == 200


def test_docs_exemption_does_not_leak_to_similar_paths(on):
    """Lookalike paths must not inherit the docs exemption: they are either 404 (no such
    route, so they reveal nothing) or 401 (under /api/*), never 200."""
    for p in ("/docsx", "/redocx", "/openapi.jsonx", "/api/openapi.json"):
        assert on.get(p).status_code != 200, p
