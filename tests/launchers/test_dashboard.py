"""Dashboard additions: launcher flags (--quota, --logs, --kaggle-json-stdin, --colab-token-stdin) and the web UI endpoints."""
import json, os, subprocess, sys
from pathlib import Path
import pytest

L = Path(__file__).resolve().parents[2] / "tools" / "launchers"
sys.path.insert(0, str(L))
import vs_preflight as P, kaggle_launch as K, colab_launch as CL  # noqa: E402

FAKES = Path(__file__).parent / "fakes"
JWT = "eyJhbGciOiJIUzI1NiJ9abcdefghijklmnopqrstuv"


def cli(script, args, tmp_path, env=None, stdin=None):
    home = tmp_path / "home"; home.mkdir(exist_ok=True)
    e = {**os.environ, "VISOSWAP_HOME": str(home), "FAKE_DIR": str(tmp_path), "HOME": str(tmp_path), "KAGGLE_CONFIG_DIR": str(tmp_path / ".kaggle"), **(env or {})}
    r = subprocess.run([sys.executable, str(L / script), *args, "--json"], capture_output=True, text=True, env=e, timeout=60, input=stdin if stdin is not None else "")
    lines = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
    return r, lines[-1], lines


def test_parse_kaggle_quota_real_table():
    out = ("resource  used    remaining  total   refreshAt\n--------  ------  ---------  ------  -------------------------\n"
           "GPU       11.08h  18.92h     30.00h  2026-11-28T00:00:00+00:00\nTPU       0.00h   20.00h     20.00h  2026-11-28T00:00:00+00:00\n")
    q = P.parse_kaggle_quota(out)
    assert q["gpu"] == {"used": 11.08, "remaining": 18.92, "total": 30.0, "refresh_at": "2026-11-28T00:00:00+00:00"} and q["tpu"]["remaining"] == 20.0
    assert P.parse_kaggle_quota("nothing here") is None


def test_parse_usage_assignments():
    assert P.parse_usage("Current balance: 233.55 compute units\nUsage rate: 1.90/hr\nActive assignments: 2") == {"balance": 233.55, "rate": 1.9, "assignments": 2}


def test_kaggle_quota_flag(tmp_path):
    r, res, _ = cli("kaggle_launch.py", ["--quota"], tmp_path, {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_GPU_LEFT": "2.5"})
    assert res["ok"] and res["quota"]["gpu"]["remaining"] == 2.5
    r, res, _ = cli("kaggle_launch.py", ["--quota"], tmp_path, {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_QUOTA_FAIL": "1"})
    assert res["ok"] and res["quota"] is None


def test_colab_quota_never_starts_login_without_stored_login(tmp_path):
    env = {"COLAB_BIN": str(FAKES / "fake_colab.py"), "FAKE_SECURITY_RC": "1", "PATH": str(FAKES) + os.pathsep + os.environ["PATH"]}   # fake `security`: no Keychain entry
    r, res, _ = cli("colab_launch.py", ["--quota"], tmp_path, env)
    assert res["ok"] and res["quota"] is None and res["login_required"] is True
    assert not (tmp_path / "calls.log").exists() or "usage" not in (tmp_path / "calls.log").read_text()      # `colab usage` was not even run
    r, res, _ = cli("colab_launch.py", ["--quota"], tmp_path, {"COLAB_BIN": str(FAKES / "fake_colab.py"), "FAKE_UNITS": "7.25", "PATH": str(FAKES) + os.pathsep + os.environ["PATH"], "FAKE_SECURITY_RC": "0"})
    assert res["quota"]["balance"] == 7.25 and res["quota"]["assignments"] == 1


def test_kaggle_logs_flag_redacts_and_tails(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "kaggle.env").write_text("KAGGLE_KERNEL=faker/visoswap-studio\nCF_TUNNEL_TOKEN=s3cretvalue123\nVISOSWAP_HOSTNAME=h.example.org\n")
    r, res, _ = cli("kaggle_launch.py", ["--logs", "--lines", "3"], tmp_path, {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KSTATE": "RUNNING"})
    assert res["ok"] and len(res["lines"]) == 3 and "s3cretvalue123" not in r.stdout and JWT not in r.stdout
    r, res, _ = cli("kaggle_launch.py", ["--logs"], tmp_path, {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KSTATE": "MISSING"})
    assert res["ok"] is False and "unavailable" in res["error"]


def test_colab_logs_returned_in_result_and_redacted(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "colab.env").write_text("VISOSWAP_HOSTNAME=h.example.org\n")
    (tmp_path / "sessions.txt").write_text("[l4] gpu-l4-fake | Hardware: L4 | Shape: Standard | Variant: GPU\n")
    r, res, _ = cli("colab_launch.py", ["--logs", "uvicorn", "--lines", "20"], tmp_path, {"COLAB_BIN": str(FAKES / "fake_colab.py"), "FAKE_LOGS": "1"})
    assert res["ok"] and any("INFO started" in l for l in res["lines"])
    assert JWT not in r.stdout and "s3cretvalue123" not in r.stdout                  # pattern-redacted: VISOSWAP_API_KEY=... and JWT shapes
    assert r.stdout.count("INFO started") == 1                                       # once in the result, not also as log events


def test_kaggle_status_started_at_from_last_run_time(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "kaggle.env").write_text("KAGGLE_KERNEL=faker/visoswap-studio\n")
    r, res, _ = cli("kaggle_launch.py", ["--status"], tmp_path, {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KSTATE": "RUNNING"})
    assert res["state"] == "RUNNING" and res["started_at"] == 1795514400.0 and res["started_source"] == "kaggle lastRunTime"
    r, res, _ = cli("kaggle_launch.py", ["--status"], tmp_path, {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KSTATE": "MISSING"})
    assert res["started_at"] is None


def test_kernel_slug_falls_back_to_cli_username_for_token_only_login(tmp_path):
    (tmp_path / "home").mkdir()
    r, res, _ = cli("kaggle_launch.py", ["--status"], tmp_path, {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KSTATE": "RUNNING"})
    assert res["kernel"] == "faker/visoswap-studio"        # no kaggle.json / KAGGLE_KERNEL: `kaggle config view`
    r, res, _ = cli("kaggle_launch.py", ["--status"], tmp_path, {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_NO_USER": "1"})
    assert res["ok"] is False and "username" in res["error"]


def test_kaggle_json_backup_login(tmp_path):
    env = {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py")}
    bad = json.dumps({"username": "faker", "key": "BADKEY-1234567890"})
    r, res, _ = cli("kaggle_launch.py", ["--kaggle-json-stdin"], tmp_path, env, stdin=bad)
    assert res["ok"] is False and "BADKEY" not in r.stdout and not (tmp_path / ".kaggle" / "kaggle.json").exists()
    good = json.dumps({"username": "faker", "key": "GOODKEY-1234567890"})
    r, res, _ = cli("kaggle_launch.py", ["--kaggle-json-stdin"], tmp_path, env, stdin=good)
    f = tmp_path / ".kaggle" / "kaggle.json"
    assert res["ok"] and "GOODKEY" not in r.stdout and oct(f.stat().st_mode & 0o777) == "0o600" and oct((tmp_path / ".kaggle").stat().st_mode & 0o777) == "0o700"
    r, res, _ = cli("kaggle_launch.py", ["--kaggle-json-stdin"], tmp_path, env, stdin="not json")
    assert res["ok"] is False


def test_colab_token_file_import(tmp_path):
    env = {"COLAB_BIN": str(FAKES / "fake_colab.py"), "FAKE_COLAB_EXPIRED": "1"}
    bad = json.dumps({"client_id": "cid", "client_secret": "csecret-xyz", "refresh_token": "BADREFRESH-123", "type": "authorized_user"})
    r, res, _ = cli("colab_launch.py", ["--colab-token-stdin"], tmp_path, env, stdin=bad)
    p = tmp_path / ".config" / "colab-cli" / "token.json"
    assert res["ok"] is False and not p.exists() and "BADREFRESH" not in r.stdout          # rejected by the CLI: removed again
    good = json.dumps({"client_id": "cid", "client_secret": "csecret-xyz", "refresh_token": "GOODREFRESH-123", "type": "authorized_user"})
    r, res, _ = cli("colab_launch.py", ["--colab-token-stdin"], tmp_path, env, stdin=good)
    assert res["ok"] and oct(p.stat().st_mode & 0o777) == "0o600" and "GOODREFRESH" not in r.stdout and "csecret-xyz" not in r.stdout
    r, res, _ = cli("colab_launch.py", ["--colab-token-stdin"], tmp_path, env, stdin=good)
    assert res["ok"] is False and "not overwriting" in res["error"]
    r, res, _ = cli("colab_launch.py", ["--colab-token-stdin"], tmp_path, {**env, "HOME": str(tmp_path / "other")}, stdin='{"client_id": "a"}')
    assert res["ok"] is False


def test_colab_status_reports_uptime_and_settings(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "colab.env").write_text("VISOSWAP_HOSTNAME=h.example.org\nCF_TUNNEL_TOKEN=eyJxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n")
    (tmp_path / "sessions.txt").write_text("[l4] gpu-l4-fake | Hardware: L4 | Shape: Standard | Variant: GPU\n")
    r, res, _ = cli("colab_launch.py", ["--status"], tmp_path, {"COLAB_BIN": str(FAKES / "fake_colab.py"), "FAKE_UVICORN": "1"})
    assert res["uptime_s"] == 600 and res["settings"] == {"VISOSWAP_PROCESS_WORKERS": "1"}


# =========================================================================== web UI (fake launchers)
import threading, time  # noqa: E402
from .test_launchers import _webui, _wait, GOODTOK  # noqa: E402

sys.path.insert(0, str(L / "webui"))
import argspec, dashboard as D  # noqa: E402


def dash(c, H, refresh=0, tries=100):
    for _ in range(tries):
        d = c.get(f"/api/dashboard?refresh={refresh}", headers=H).json()
        if not d["pending"]:
            return d
        time.sleep(0.1)
    raise AssertionError("dashboard stayed pending")


def run_job(c, H, target, action, body=None):
    r = c.post(f"/api/targets/{target}/{action}", headers=H, json=body or {})
    assert r.status_code == 200, r.text
    return _wait(c, H, r.json()["job"], ("done", "failed"))


def fake_state(tmp_path, target):
    return json.loads((tmp_path / f"fake_state_{target}.json").read_text())


# ---------- argspec: the only door to launcher flags
def test_argspec_maps_options_and_rejects_everything_else(tmp_path):
    (tmp_path / "api_key").write_text("k" * 20)
    f = argspec.build_flags("colab", "restart", {"gpu": "T4", "concurrency": 3, "profile": False, "api_key": "on"}, tmp_path)
    assert f == ["--restart", "--gpu", "T4", "--concurrency", "3", "--no-profile", "--api-key-file", str(tmp_path / "api_key")]
    assert argspec.build_flags("kaggle", "launch", {"max_minutes": 0, "concurrency": "auto", "profile": True}, tmp_path) == ["--concurrency", "auto", "--profile", "--max-minutes", "0"]
    assert argspec.build_flags("colab", "launch", {"api_key": "off"}, tmp_path) == ["--no-api-key"]
    assert argspec.build_flags("colab", "logs", {"source": "tunnel", "lines": 50}, tmp_path) == ["--logs", "tunnel", "--lines", "50"]
    assert argspec.build_flags("kaggle", "logs", {"source": "kernel"}, tmp_path) == ["--logs", "--lines", "200"]
    bad = [("kaggle", "launch", {"gpu": "L4"}), ("colab", "launch", {"max_minutes": 5}), ("colab", "launch", {"gpu": "L4; rm -rf /"}),
           ("colab", "launch", {"concurrency": 99}), ("colab", "launch", {"concurrency": True}), ("colab", "launch", {"profile": "yes"}),
           ("colab", "launch", {"bogus": 1}), ("kaggle", "launch", {"api_key": "on"}), ("colab", "launch", {"api_key": "/etc/passwd"}),
           ("kaggle", "launch", {"max_minutes": 9999}), ("colab", "stop", {"gpu": "L4"}), ("kaggle", "colab-token", {}), ("nope", "launch", {}),
           ("colab", "logs", {"source": "../etc"}), ("colab", "logs", {"source": "uvicorn", "lines": 5}), ("kaggle", "status", {"x": 1})]
    for t, a, o in bad:
        with pytest.raises(ValueError):
            argspec.build_flags(t, a, o, tmp_path)
    with pytest.raises(ValueError, match="confirm"):
        argspec.build_flags("colab", "stop", {}, tmp_path)
    assert argspec.build_flags("colab", "stop", {}, tmp_path, confirm=True) == ["--down", "--yes"]
    with pytest.raises(ValueError, match="API key file"):
        argspec.build_flags("colab", "launch", {"api_key": "on"}, tmp_path / "empty")


# ---------- pure view logic
def test_compose_states_and_kaggle_time_left():
    now = 1_000_000.0
    run = {"ok": True, "state": "RUNNING", "healthy": True, "url": "https://k.example", "started_at": now - 40 * 60, "started_source": "kaggle lastRunTime"}
    c = D.compose_target("kaggle", run, {"concurrency": 2, "auth": "off"}, {"max_minutes": 60}, None, now)
    assert c["state"] == "healthy" and c["uptime_s"] == 2400 and c["time_left_s"] == 1200 and c["max_minutes"] == 60
    assert c["concurrency"] == 2 and c["auth"] == "off" and "estimate" in c["uptime_source"] and c["gpu"].startswith("T4")
    c = D.compose_target("kaggle", run, None, {}, None, now)
    assert c["max_minutes"] == 90 and c["max_minutes_source"]                         # not launched from here: assumed default, labelled
    c = D.compose_target("kaggle", {**run, "started_at": now - 85 * 60}, None, {}, None, now)
    assert c["warnings"] and "stops itself" in c["warnings"][0]
    assert D.compose_target("kaggle", {**run, "healthy": False}, None, {}, None, now)["state"] == "running"
    assert D.compose_target("kaggle", {"ok": True, "state": "QUEUED"}, None, {}, None, now)["state"] == "queued"
    assert D.compose_target("kaggle", {"ok": True, "state": "MISSING"}, None, {}, None, now)["state"] == "down"
    assert D.compose_target("kaggle", {"ok": False, "error": "x"}, None, {}, None, now)["state"] == "error"
    assert D.compose_target("kaggle", {"ok": True, "state": "ERROR"}, None, {}, None, now)["state"] == "error"
    assert D.compose_target("kaggle", None, None, {}, None, now)["state"] == "unknown"
    assert D.compose_target("kaggle", run, None, {}, "restart", now)["state"] == "starting"
    assert D.compose_target("kaggle", run, None, {}, "stop", now)["state"] == "stopping"
    no_start = D.compose_target("kaggle", {**run, "started_at": None}, None, {}, None, now)
    assert no_start["uptime_s"] is None and no_start["time_left_s"] is None            # unknown stays unknown


def test_compose_colab_process_mode_and_session_only_warning():
    now = 5.0
    r = {"ok": True, "state": "RUNNING", "healthy": True, "url": "https://c.example", "gpu": "NVIDIA L4", "uptime_s": 125, "settings": {"VISOSWAP_PROCESS_WORKERS": "0"}}
    c = D.compose_target("colab", r, None, {}, None, now)
    assert c["gpu"] == "NVIDIA L4" and c["uptime_s"] == 125 and c["process_mode"] == "threads" and c["time_left_s"] is None
    assert D.process_mode({}) .startswith("auto") and D.process_mode({"VISOSWAP_PROCESS_WORKERS": "1"}) == "processes" and D.process_mode(None) is None
    so = D.compose_target("colab", {"ok": True, "state": "SESSION_ONLY", "healthy": False, "gpu": "NVIDIA L4", "settings": {}}, None, {}, None, now)
    assert so["state"] == "down" and "compute units" in so["warnings"][0]


def test_quota_view_thresholds():
    ok = lambda rem: {"ok": True, "quota": {"gpu": {"used": 30 - rem, "remaining": rem, "total": 30.0, "refresh_at": "x"}}}
    assert D.quota_view("kaggle", ok(18.9))["warn"] is None
    assert D.quota_view("kaggle", ok(2.0))["warn"] == "low" and D.quota_view("kaggle", ok(0.0))["warn"] == "empty"
    assert D.quota_view("kaggle", ok(2.9))["percent_left"] == 10
    col = lambda b, r: {"ok": True, "quota": {"balance": b, "rate": r, "assignments": 1}}
    v = D.quota_view("colab", col(100, 1.9))
    assert v["warn"] is None and v["hours_left"] == 52.6
    assert D.quota_view("colab", col(5, 0.0))["warn"] == "low" and D.quota_view("colab", col(0, 0))["warn"] == "empty"
    assert D.quota_view("colab", col(4.0, 1.9))["warn"] == "low"
    assert D.quota_view("colab", {"ok": True, "quota": None, "login_required": True})["login_required"] is True
    assert D.quota_view("colab", None)["pending"] is True


def test_cache_single_flight_and_stale_while_revalidate():
    c, calls_ = D.Cache(), []
    def slow():
        calls_.append(1); time.sleep(0.2); return {"n": len(calls_)}
    ts = [threading.Thread(target=lambda: c.get("k", 60, slow, block=True)) for _ in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(calls_) == 1                                                   # five clients, one CLI call
    v, age, pending = c.get("k", 60, slow)
    assert v == {"n": 1} and not pending and len(calls_) == 1                 # fresh: served from cache
    c.drop("k")
    v, age, pending = c.get("k", 60, slow)
    assert v == {"n": 1} and pending                                          # stale value served while refreshing
    time.sleep(0.4)
    assert c.get("k", 60, slow)[0] == {"n": 2}


# ---------- dashboard endpoint: the first real view is both backends down
def test_dashboard_requires_auth_and_starts_down(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    assert c.get("/api/dashboard").status_code == 401
    d = dash(c, H)
    assert d["targets"]["kaggle"]["state"] == "down" and d["targets"]["colab"]["state"] == "down"
    assert d["targets"]["kaggle"]["uptime_s"] is None and d["targets"]["colab"]["gpu"] is None
    assert d["bridge"]["configured"] is False and d["fake"] is True
    assert A.state().token not in json.dumps(d)


def test_dashboard_start_restart_stop_cycle_with_options(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    j = run_job(c, H, "colab", "start", {"options": {"gpu": "T4", "concurrency": 2, "profile": True}})
    assert j["state"] == "done"
    flags = fake_state(tmp_path, "colab")["flags"]
    assert "--gpu" in flags and "T4" in flags and "--concurrency" in flags and "2" in flags and "--profile" in flags
    d = dash(c, H, refresh=1)["targets"]["colab"]
    assert d["state"] == "healthy" and d["gpu"] == "Tesla T4" and d["health"]["auth"] == "required" and d["concurrency"] == 3
    assert d["process_mode"] == "processes" and d["url"] == "https://colab.example.org" and d["uptime_s"] is not None
    assert d["last_options"]["gpu"] == "T4"                                          # remembered for the form
    j = run_job(c, H, "colab", "restart", {"options": {"concurrency": "auto"}})
    assert j["state"] == "done" and "--restart" in fake_state(tmp_path, "colab")["flags"]
    assert c.post("/api/targets/colab/stop", headers=H, json={}).status_code == 400   # confirm required
    assert run_job(c, H, "colab", "stop", {"confirm": True})["state"] == "done"
    assert dash(c, H, refresh=1)["targets"]["colab"]["state"] == "down"


def test_dashboard_kaggle_start_records_time_left(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    assert run_job(c, H, "kaggle", "start", {"options": {"max_minutes": 45}})["state"] == "done"
    d = dash(c, H, refresh=1)["targets"]["kaggle"]
    assert d["state"] == "healthy" and d["max_minutes"] == 45 and 0 < d["time_left_s"] <= 45 * 60 and "estimate" in d["uptime_source"]
    assert oct((tmp_path / "dashboard.json").stat().st_mode & 0o777) == "0o600" and "token" not in (tmp_path / "dashboard.json").read_text().lower()


def test_start_rejects_bad_options_with_400(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    for t, o in [("kaggle", {"gpu": "L4"}), ("colab", {"concurrency": 500}), ("colab", {"x": 1}), ("colab", {"api_key": "on"})]:
        r = c.post(f"/api/targets/{t}/start", headers=H, json={"options": o})
        assert r.status_code == 400, (t, o, r.text)
    assert not (tmp_path / "fake_state_colab.json").exists() and A.state().busy == {}      # nothing started
    (tmp_path / "api_key").write_text("plainaccesskey-12345")
    assert run_job(c, H, "colab", "start", {"options": {"api_key": "on"}})["state"] == "done"
    assert "--api-key-file" in fake_state(tmp_path, "colab")["flags"]
    assert "plainaccesskey" not in json.dumps(c.get("/api/dashboard", headers=H).json())


def test_dashboard_states_queued_error_session_only(tmp_path, monkeypatch):
    for force, t, want in (("queued", "kaggle", "queued"), ("error", "kaggle", "error"), ("session_only", "colab", "down")):
        monkeypatch.setenv("FAKE_FORCE_STATE", force)
        A, c, H = _webui(tmp_path, monkeypatch)
        d = dash(c, H)["targets"][t]
        assert d["state"] == want, (force, d)
        if force == "error":
            assert d["error"] and d["next"]
        if force == "session_only":
            assert d["warnings"] and "compute units" in d["warnings"][0]


def test_dashboard_busy_shows_starting_and_does_not_probe(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    jid = c.post("/api/targets/colab/start", headers=H, json={}).json()["job"]
    d = c.get("/api/dashboard", headers=H).json()["targets"]["colab"]
    assert d["state"] == "starting" and d["busy"] is True
    _wait(c, H, jid, ("done",))


# ---------- quotas
def test_quotas_values_warning_cache_and_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_GPU_LEFT", "2.0")
    A, c, H = _webui(tmp_path, monkeypatch)
    assert c.get("/api/quotas").status_code == 401
    def get(refresh=0):
        for _ in range(100):
            q = c.get(f"/api/quotas?refresh={refresh}", headers=H).json()
            if not q["pending"]:
                return q
            time.sleep(0.1)
    q = get()
    assert q["kaggle"]["gpu"]["remaining"] == 2.0 and q["kaggle"]["warn"] == "low" and "2" in q["kaggle"]["warn_text"] and q["kaggle"]["gpu"]["refresh_at"].startswith("2026-11-28")
    assert q["colab"]["balance"] == 233.55 and q["colab"]["warn"] is None
    seen = []
    orig = A.run_launcher
    monkeypatch.setattr(A, "run_launcher", lambda *a, **k: (seen.append(a[:2]), orig(*a, **k))[1])
    get(); get(refresh=1); get(refresh=1)
    assert seen == []                                      # cached 10 min; a manual refresh within 30 s does not re-run the CLIs


def test_quotas_unavailable_and_colab_login_required(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_QUOTA_FAIL", "1")
    A, c, H = _webui(tmp_path, monkeypatch)
    for _ in range(100):
        q = c.get("/api/quotas", headers=H).json()
        if not q["pending"]: break
        time.sleep(0.1)
    assert q["kaggle"]["available"] is False and q["colab"]["available"] is False and q["kaggle"]["warn"] is None
    monkeypatch.delenv("FAKE_QUOTA_FAIL"); monkeypatch.setenv("FAKE_NEEDS_LOGIN", "1")
    A, c, H = _webui(tmp_path, monkeypatch)
    for _ in range(100):
        q = c.get("/api/quotas", headers=H).json()
        if not q["pending"]: break
        time.sleep(0.1)
    assert q["colab"]["login_required"] is True


# ---------- logs
def test_job_log_is_persistent_and_redacted(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    assert c.get("/api/logs/colab/job").status_code == 401
    run_job(c, H, "colab", "start")
    r = c.get("/api/logs/colab/job?lines=50", headers=H).json()
    txt = "\n".join(r["lines"])
    assert "colab start" in txt and "fake step 1" in txt and "DONE" in txt and "eyJhbGci" not in txt
    A2, c2, H2 = _webui(tmp_path, monkeypatch)                                       # a restarted dashboard still has it
    assert "fake step 1" in "\n".join(c2.get("/api/logs/colab/job", headers=H2).json()["lines"])
    assert oct((tmp_path / "webui_logs" / "colab.log").stat().st_mode & 0o777) == "0o600"
    assert c.get("/api/logs/colab/job?lines=3", headers=H).status_code == 400


def test_remote_logs_kaggle_and_colab_tail_and_redact(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    (tmp_path / "api_key").write_text("fakeAccessKey456")
    run_job(c, H, "kaggle", "start"); run_job(c, H, "colab", "start")
    k = c.get("/api/logs/kaggle/kernel?lines=20", headers=H).json()
    assert k["ok"] and len(k["lines"]) == 20
    blob = json.dumps(k) + json.dumps(c.get("/api/logs/colab/uvicorn?lines=20", headers=H).json())
    for secret in ("fakeSecretValue123", "fakeAccessKey456", "eyJhbGciOiJIUzI1NiJ9abcdefghijklmnopqrstuv"):
        assert secret not in blob
    assert "***" in blob
    assert c.get("/api/logs/colab/kernel", headers=H).status_code == 404 and c.get("/api/logs/kaggle/uvicorn", headers=H).status_code == 404
    assert c.get("/api/logs/colab/uvicorn?lines=99999", headers=H).status_code == 400
    assert c.get("/api/logs/nope/job", headers=H).status_code == 404
    A.state().busy["colab"] = "job"
    assert c.get("/api/logs/colab/tunnel", headers=H).status_code == 409                # no exec queued behind a running action
    A.state().busy.pop("colab")


def test_remote_logs_error_when_nothing_runs(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    r = c.get("/api/logs/kaggle/kernel", headers=H).json()
    assert r["ok"] is False and "unavailable" in r["error"] and r["lines"] == []


def test_webui_redactor_knows_the_api_key_file(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    (tmp_path / "api_key").write_text("my plain key value 12345\n")
    assert "my plain key value 12345" not in A.state().redactor()("x my plain key value 12345 y")


def test_docker_does_not_print_the_token(tmp_path, monkeypatch, capsys):
    A, c, H = _webui(tmp_path, monkeypatch)
    (tmp_path / "webui_token").unlink()
    capsys.readouterr()
    monkeypatch.setattr(A, "IN_DOCKER", True)
    A.S = None
    tok = A.state().token
    assert tok not in capsys.readouterr().out and (tmp_path / "webui_token").read_text().strip() == tok


def test_docker_token_hint_is_a_working_command(tmp_path, monkeypatch, capsys):
    """The named volume does not live at ~/.visoswap, so the hint must be a `docker exec` on the container."""
    A, c, H = _webui(tmp_path, monkeypatch)
    (tmp_path / "webui_token").unlink()
    capsys.readouterr()
    monkeypatch.setattr(A, "IN_DOCKER", True)
    monkeypatch.setenv("HOSTNAME", "abc123def456")
    A.S = None
    tok = A.state().token
    out = capsys.readouterr().out
    assert tok not in out
    assert "docker exec abc123def456 cat" in out
    assert "cat ~/.visoswap/webui_token" not in out


def test_docker_token_hint_without_hostname(tmp_path, monkeypatch, capsys):
    """No HOSTNAME (native run of the docker branch) still yields a placeholder instead of a broken path."""
    A, c, H = _webui(tmp_path, monkeypatch)
    (tmp_path / "webui_token").unlink()
    capsys.readouterr()
    monkeypatch.setattr(A, "IN_DOCKER", True)
    monkeypatch.delenv("HOSTNAME", raising=False)
    A.S = None
    tok = A.state().token
    out = capsys.readouterr().out
    assert tok not in out and "docker exec <container> cat" in out


def test_dockerfile_pins_six_for_the_kaggle_cli():
    """kaggle 2.2.5 imports six without declaring it; the image must install it or every kaggle call dies."""
    df = (L / "webui" / "Dockerfile").read_text()
    pip_line = next(l for l in df.splitlines() if "pip install" in l)
    assert " kaggle " in pip_line + " " or pip_line.rstrip().endswith("kaggle \\")
    assert "six==1.17.0" in pip_line
    # the build must prove the CLIs import, not just that pip succeeded
    assert "kaggle --version" in df
    assert "colab version" in df


# ---------- backup logins
def test_kaggle_json_backup_login_endpoint(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    good = json.dumps({"username": "faker", "key": "GOODKEY-abcdef123456"})
    assert c.post("/api/targets/kaggle/kaggle-json", json={"json": good}).status_code == 401
    assert c.post("/api/targets/kaggle/kaggle-json", headers=H, json={"json": "not json"}).status_code == 400
    assert c.post("/api/targets/kaggle/kaggle-json", headers=H, json={"json": '{"username": "a"}'}).status_code == 400
    r = c.post("/api/targets/kaggle/kaggle-json", headers=H, json={"json": json.dumps({"username": "faker", "key": "BADKEY-abcdef123456"})})
    assert r.json()["ok"] is False and "BADKEY" not in r.text
    r = c.post("/api/targets/kaggle/kaggle-json", headers=H, json={"json": good})
    assert r.json()["ok"] is True and "GOODKEY" not in r.text
    assert c.post("/api/targets/colab/kaggle-json", headers=H, json={"json": good}).status_code in (400, 404)      # kaggle only
    assert "GOODKEY" not in json.dumps(c.get("/api/logs/kaggle/job", headers=H).json())


def test_colab_token_file_backup_login_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_NEEDS_LOGIN", "1")
    A, c, H = _webui(tmp_path, monkeypatch)
    tok = lambda r: json.dumps({"client_id": "cid-123", "client_secret": "csecret-123456", "refresh_token": r, "type": "authorized_user"})
    assert c.post("/api/targets/colab/colab-token", json={"json": tok("GOOD-1")}).status_code == 401
    assert c.post("/api/targets/colab/colab-token", headers=H, json={"json": "[]"}).status_code == 400
    assert c.post("/api/targets/colab/colab-token", headers=H, json={"json": '{"client_id": "x"}'}).status_code == 400
    r = c.post("/api/targets/colab/colab-token", headers=H, json={"json": tok("BADREFRESH-123456")})
    assert r.json()["ok"] is False and "BADREFRESH" not in r.text
    r = c.post("/api/targets/colab/colab-token", headers=H, json={"json": tok("GOODREFRESH-123456")})
    assert r.json()["ok"] is True and "GOODREFRESH" not in r.text and "csecret" not in r.text
    assert c.get("/api/checks?refresh=1", headers=H).json()["targets"]["colab"]["checks"][0]["state"] == "ok"
    assert c.post("/api/targets/kaggle/colab-token", headers=H, json={"json": tok("GOOD-1")}).status_code in (400, 404)


def test_login_methods_listed_per_service(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    m = c.get("/api/checks", headers=H).json()["login_methods"]
    assert [x["id"] for x in m["kaggle"]] == ["token", "json", "cli"] and [x["id"] for x in m["colab"]] == ["relay", "token-file", "cli"]
    assert [x["id"] for x in m["cloudflare"]] == ["token", "api-token", "cli"]
    assert all(x.get("command") for x in m["colab"] if x["id"] in ("token-file", "cli"))


# ---------- host bridge
@pytest.fixture
def bridged(tmp_path, monkeypatch):
    """A real bridge server (fake launchers) on a free port, and a dashboard that sends colab through it."""
    import bridge as B
    A, c, H = _webui(tmp_path, monkeypatch)
    srv = B.ThreadingHTTPServer(("127.0.0.1", 0), B.make_handler(B.BridgeState(A.state().token, tmp_path)))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("LAUNCHER_BRIDGE_URL", base)
    yield A, c, H, B, base
    srv.shutdown()


def test_bridge_runs_colab_jobs_on_the_host_side(bridged, tmp_path):
    A, c, H, B, base = bridged
    d = dash(c, H)
    assert d["bridge"]["configured"] and d["bridge"]["ok"] is True and d["bridge"]["targets"] == ["colab"]
    j = run_job(c, H, "colab", "start", {"options": {"gpu": "T4"}})
    assert j["state"] == "done" and any("fake step" in l for l in j["lines"]) and "eyJhbGci" not in json.dumps(j)
    assert "T4" in fake_state(tmp_path, "colab")["flags"]
    assert dash(c, H, refresh=1)["targets"]["colab"]["state"] == "healthy"
    assert "fakeSecretValue123" not in json.dumps(c.get("/api/logs/colab/uvicorn", headers=H).json())
    assert run_job(c, H, "colab", "stop", {"confirm": True})["state"] == "done"


def test_bridge_relay_login_and_secret_flow(bridged, tmp_path, monkeypatch):
    A, c, H, B, base = bridged
    monkeypatch.setenv("FAKE_NEEDS_LOGIN", "1")
    monkeypatch.setattr(A, "IN_DOCKER", True)                      # in Docker the relay login is allowed once the bridge is configured
    jid = c.post("/api/targets/colab/login", headers=H).json()["job"]
    j = _wait(c, H, jid, ("waiting", "failed", "done"))
    assert j["state"] == "waiting" and j["prompt"]["kind"] == "code"
    assert c.post(f"/api/jobs/{jid}/input", headers=H, json={"value": "GOODCODE-12345678"}).status_code == 200
    j = _wait(c, H, jid, ("done", "failed"))
    assert j["state"] == "done" and "GOODCODE" not in json.dumps(j)
    assert c.get("/api/checks", headers=H).json()["login_ui"]["colab"] is True
    cf = "eyJhIjoiZmFrZXRva2VuMiIsInQiOiJmYWtlLXR1bm5lbC1pZC0xMjM0NTYiLCJzIjoiYWJjZGVmZ2hpamtsbW5vcCJ9"
    r = c.post("/api/targets/colab/cloudflare-token", headers=H, json={"token": cf})
    assert r.json()["ok"] is True and cf not in r.text


def test_bridge_server_enforces_everything_itself(bridged, tmp_path):
    A, c, H, B, base = bridged
    tok = A.state().token
    with pytest.raises(B.BridgeError, match="401"):
        B.request(base, "wrong", "GET", "/v1/ping")
    bad = [{"target": "colab", "op": "stop", "options": {}},                                   # no confirm
           {"target": "colab", "op": "launch", "options": {"gpu": "L4; touch /tmp/pwned"}},
           {"target": "colab", "op": "launch", "options": {"argv": ["--down"]}},
           {"target": "colab", "op": "rm -rf", "options": {}}, {"target": "../x", "op": "launch"},
           {"target": "kaggle", "op": "colab-token", "stdin": "x"}, {"target": "colab", "op": "cf-token"},      # secret op without its secret
           {"target": "colab", "op": "launch", "flags": ["--down"], "argv": ["rm"]}]
    codes = []
    for body in bad:
        try:
            B.request(base, tok, "POST", "/v1/run", body)
            codes.append(200)
        except B.BridgeError as e:
            codes.append(int(str(e).split("(")[1][:3]))
    assert codes[:7] == [400] * 7 and codes[7] == 200        # unknown keys are ignored: flags only ever come from argspec's table
    assert not (tmp_path / "pwned").exists()
    time.sleep(1.5)                                         # let the harmless launch above finish
    with pytest.raises(B.BridgeError, match="404"):
        B.request(base, tok, "GET", "/v1/nope")


def test_bridge_unreachable_is_a_clear_job_error(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    monkeypatch.setenv("LAUNCHER_BRIDGE_URL", "http://127.0.0.1:9")
    j = run_job(c, H, "colab", "start")
    assert j["state"] == "failed" and "bridge" in j["result"]["error"] and "bridge start" in j["result"]["next"]
    d = dash(c, H)
    assert d["bridge"]["ok"] is False and "unreachable" in d["bridge"]["detail"]
    assert dash(c, H)["targets"]["kaggle"]["state"] == "down"          # kaggle is not bridged: unaffected
