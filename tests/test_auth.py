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


# --- login throttle -----------------------------------------------------------
from backend import auth as _auth  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_limiter():
    """The limiter is process-global and time-based; never let one test leak into the next."""
    _auth.login_limiter.clear()
    yield
    _auth.login_limiter.clear()


def _login(c, key, ip=None, **headers):
    h = dict(headers)
    if ip:
        h["CF-Connecting-IP"] = ip
    return c.post("/api/auth/session", json={"key": key}, headers=h)


def test_login_throttled_after_ten_failures(on):
    for i in range(_auth.LOGIN_MAX_FAILS):
        assert _login(on, "wrong", ip="203.0.113.7").status_code == 401, i
    r = _login(on, "wrong", ip="203.0.113.7")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_correct_key_still_works_while_another_ip_is_limited(on):
    for _ in range(_auth.LOGIN_MAX_FAILS + 1):
        _login(on, "wrong", ip="203.0.113.9")
    assert _login(on, "wrong", ip="203.0.113.9").status_code == 429
    # same IP, right key: the comparison happens before the limiter, so it must succeed
    r = _login(on, ASCII_KEY, ip="203.0.113.9")
    assert r.status_code == 200 and "set-cookie" in r.headers
    # and a different IP was never affected
    assert _login(on, ASCII_KEY, ip="198.51.100.4").status_code == 200


def test_success_resets_the_counter_for_that_ip(on):
    for _ in range(_auth.LOGIN_MAX_FAILS - 1):
        _login(on, "wrong", ip="203.0.113.11")
    assert _login(on, ASCII_KEY, ip="203.0.113.11").status_code == 200
    # counter cleared: the next failures start from zero again, not from the limit
    for i in range(_auth.LOGIN_MAX_FAILS - 1):
        assert _login(on, "wrong", ip="203.0.113.11").status_code == 401, i


def test_throttle_is_per_ip(on):
    for _ in range(_auth.LOGIN_MAX_FAILS + 1):
        _login(on, "wrong", ip="203.0.113.20")
    assert _login(on, "wrong", ip="203.0.113.20").status_code == 429
    assert _login(on, "wrong", ip="203.0.113.21").status_code == 401


def test_forwarded_for_first_hop_is_the_bucket(on):
    for _ in range(_auth.LOGIN_MAX_FAILS + 1):
        _login(on, "wrong", **{"X-Forwarded-For": "203.0.113.30, 10.0.0.1, 10.0.0.2"})
    assert _login(on, "wrong", **{"X-Forwarded-For": "203.0.113.30, 10.0.0.1"}).status_code == 429
    # a different first hop is a different client
    assert _login(on, "wrong", **{"X-Forwarded-For": "203.0.113.31, 10.0.0.1"}).status_code == 401


def test_cf_connecting_ip_beats_x_forwarded_for(on):
    for _ in range(_auth.LOGIN_MAX_FAILS + 1):
        _login(on, "wrong", **{"CF-Connecting-IP": "203.0.113.40", "X-Forwarded-For": "1.1.1.1"})
    # same CF ip, different XFF: still the same bucket
    assert _login(on, "wrong", **{"CF-Connecting-IP": "203.0.113.40", "X-Forwarded-For": "2.2.2.2"}).status_code == 429
    assert _login(on, "wrong", **{"CF-Connecting-IP": "203.0.113.41", "X-Forwarded-For": "1.1.1.1"}).status_code == 401


def test_keyed_api_requests_are_never_throttled(on):
    for _ in range(_auth.LOGIN_MAX_FAILS + 2):
        _login(on, "wrong", ip="203.0.113.50")
    assert _login(on, "wrong", ip="203.0.113.50").status_code == 429
    # the throttle is on the login endpoint only: normal keyed traffic is untouched
    assert on.get("/api/projects", headers={H: ASCII_KEY}).status_code == 200


def test_window_expiry_lifts_the_block():
    lim = _auth._LoginLimiter(max_fails=2, window_s=60.0)
    assert lim.retry_after("ip", now=0.0) == 0
    lim.fail("ip", now=0.0)
    lim.fail("ip", now=0.0)
    assert lim.retry_after("ip", now=1.0) > 0
    # three attempts inside the window are blocked, and the value counts down
    assert lim.retry_after("ip", now=30.0) <= 31
    assert lim.retry_after("ip", now=61.0) == 0


def test_off_mode_login_is_never_throttled(off):
    for _ in range(_auth.LOGIN_MAX_FAILS + 5):
        r = _login(off, "whatever", ip="203.0.113.60")
        assert r.status_code == 200 and r.json()["auth"] == "off"


def test_attempt_limit_does_not_leak_the_key(on, caplog):
    import logging as _logging
    caplog.set_level(_logging.DEBUG)
    for _ in range(_auth.LOGIN_MAX_FAILS + 2):
        _login(on, "wrong-key-value", ip="203.0.113.70")
    assert "wrong-key-value" not in caplog.text and ASCII_KEY not in caplog.text


def test_tracker_is_bounded_under_ip_rotation():
    """Spoofed headers let one client look like many; the dict must not grow without limit."""
    lim = _auth._LoginLimiter(max_fails=2, window_s=60.0)
    for i in range(_auth.LOGIN_MAX_TRACKED_IPS + 50):
        lim.fail(f"10.0.{i // 256}.{i % 256}", now=0.0)
    assert len(lim._fails) <= _auth.LOGIN_MAX_TRACKED_IPS
    # and the limiter still works afterwards
    lim.fail("192.0.2.1", now=0.0)
    lim.fail("192.0.2.1", now=0.0)
    assert lim.retry_after("192.0.2.1", now=0.0) > 0
