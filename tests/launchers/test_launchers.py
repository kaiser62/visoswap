import json, os, subprocess, sys
from pathlib import Path
import pytest

L = Path(__file__).resolve().parents[2] / "tools" / "launchers"
sys.path.insert(0, str(L))
import vs_common as C, vs_cf, vs_remote, kaggle_launch as K, colab_launch as CL  # noqa: E402

FAKES = Path(__file__).parent / "fakes"
TOKEN = "eyJhIjoiZmFrZWFjY291bnQiLCJ0IjoiZmFrZXR1bm5lbCIsInMiOiJzZWNyZXQifQ"


# ---------- env / redaction
def test_env_merge_keeps_existing_and_is_0600(tmp_path):
    p = tmp_path / "k.env"
    p.write_text("# c\nCF_TUNNEL_TOKEN=old\nKAGGLE_BIN='/x/kaggle'\n")
    w = C.merge_env_file(p, {"CF_TUNNEL_TOKEN": "new", "VISOSWAP_HOSTNAME": "h.example.org"})
    assert w == ["VISOSWAP_HOSTNAME"]
    env = C.parse_env(p.read_text())
    assert env["CF_TUNNEL_TOKEN"] == "old" and env["KAGGLE_BIN"] == "/x/kaggle" and "# c" in p.read_text()
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_load_config_env_overrides_file(tmp_path):
    (tmp_path / "kaggle.env").write_text("VISOSWAP_HOSTNAME=a.example.org\n")
    cfg = C.load_config("kaggle", {"VISOSWAP_HOSTNAME": "b.example.org"}, tmp_path)
    assert cfg["VISOSWAP_HOSTNAME"] == "b.example.org"


def test_redactor():
    r = C.Redactor(["supersecretvalue"])
    s = r(f"x supersecretvalue y --token {TOKEN} CF_TUNNEL_TOKEN={TOKEN} Authorization: Bearer abcdefghijk")
    assert "supersecretvalue" not in s and TOKEN not in s and "abcdefghijk" not in s


def test_reporter_json_redacts(capsys):
    rep = C.Reporter(True, C.Redactor([TOKEN]))
    rep.log("tok " + TOKEN)
    rep.result(True, url="https://h")
    out = capsys.readouterr().out.strip().splitlines()
    assert TOKEN not in "".join(out) and json.loads(out[-1])["t"] == "result"


def test_health_ok():
    assert C.health_ok('{"status":"ok"}') and C.health_ok('{"status": "ok", "x": 1}')
    assert not C.health_ok("error code: 1033") and not C.health_ok('{"status":"starting"}')


def test_dry_run_runner_skips_mutating():
    run = C.Runner(C.Reporter(), dry_run=True)
    r = run.run(["definitely-not-a-binary", "--token", TOKEN], mutating=True)
    assert r.skipped
    assert run.run(["definitely-not-a-binary"]).rc == 127


# ---------- kaggle
@pytest.mark.parametrize("out,exp", [
    ('x has status "KernelWorkerStatus.RUNNING"', "RUNNING"), ("KernelWorkerStatus.QUEUED", "QUEUED"),
    ("KernelWorkerStatus.COMPLETE", "COMPLETE"), ("Authentication required", "AUTH"),
    ("Kernel 'a/b' was denied", "MISSING"), ("something weird", "UNKNOWN")])
def test_classify(out, exp):
    assert K.classify_status(out) == exp


@pytest.mark.parametrize("state,healthy,restart,knobs,exp", [
    ("RUNNING", True, False, False, "reuse"), ("RUNNING", False, False, False, "wait_health"),
    ("QUEUED", False, False, False, "wait_gpu"), ("RUNNING", True, True, False, "relaunch"),
    ("RUNNING", True, False, True, "knobs_need_restart"), ("MISSING", False, False, False, "launch"),
    ("ERROR", False, False, False, "launch"), ("UNKNOWN", False, False, False, "abort_unknown"),
    ("AUTH", False, False, False, "abort_auth")])
def test_decide(state, healthy, restart, knobs, exp):
    assert K.decide(state, healthy, restart, knobs) == exp


def test_inject_notebook_does_not_mutate_source():
    nb = json.loads(K.NOTEBOOK.read_text())
    before = json.dumps(nb)
    out = K.inject_notebook(nb, "TOK123456789", "h.example.org", 3, True, 30)
    assert json.dumps(nb) == before
    txt = json.dumps(out)
    assert "TOK123456789" in txt and "h.example.org" in txt and "MAX_RUNTIME_MIN = 30" in txt and "'3'" in txt
    with pytest.raises(C.LaunchError):
        K.inject_notebook(nb, "tok", "")


def test_push_cmd_and_accel():
    assert K.push_cmd("kaggle", Path("/b"), K.resolve_accelerator("t4x2")) == ["kaggle", "kernels", "push", "-p", "/b", "--accelerator", "NvidiaTeslaT4"]
    assert K.resolve_accelerator("Foo") == "Foo"


# ---------- cloudflare
def test_zone_candidates():
    assert vs_cf.zone_candidates("swap.a.b.example.org") == ["a.b.example.org", "b.example.org", "example.org"]


class FakeCF:
    def __init__(self, tunnels=(), dns=()):
        self.calls, self.tunnels, self.dns = [], list(tunnels), list(dns)

    def __call__(self, method, url, headers, body):
        path = url.split("/client/v4")[1]
        self.calls.append((method, path))
        ok = lambda r: (200, {"success": True, "result": r})
        if path.startswith("/zones?name="):
            return ok([{"id": "z1", "account": {"id": "a1"}}])
        if "cfd_tunnel?name=" in path:
            return ok(self.tunnels)
        if method == "POST" and path.endswith("/cfd_tunnel"):
            return ok({"id": "newid"})
        if method == "PUT":
            return ok({})
        if "dns_records?name" in path:
            return ok(self.dns)
        if method == "POST" and "dns_records" in path:
            return ok({})
        if path.endswith("/token"):
            return ok(TOKEN)
        raise AssertionError(path)


def test_cf_api_creates_when_missing(tmp_path):
    f = FakeCF()
    rep = C.Reporter()
    cfg = vs_cf.ensure_tunnel("kaggle", {"CF_API_TOKEN": "apitok12345", "VISOSWAP_HOSTNAME": "swap.example.org"}, rep,
                              C.Runner(rep), fetch=f, home=tmp_path)
    assert cfg["CF_TUNNEL_TOKEN"] == TOKEN and cfg["TUNNEL_MODE"] == "named"
    assert ("POST", "/accounts/a1/cfd_tunnel") in f.calls and any(m == "PUT" for m, _ in f.calls)
    assert C.parse_env((tmp_path / "kaggle.env").read_text())["CF_TUNNEL_TOKEN"] == TOKEN
    assert oct((tmp_path / "kaggle.env").stat().st_mode & 0o777) == "0o600"


def test_cf_api_existing_tunnel_untouched_and_never_deletes(tmp_path):
    f = FakeCF(tunnels=[{"id": "tid"}], dns=[{"content": "tid.cfargotunnel.com"}])
    rep = C.Reporter()
    vs_cf.ensure_tunnel("colab", {"CF_API_TOKEN": "apitok12345", "VISOSWAP_HOSTNAME": "cl.example.org"}, rep, C.Runner(rep), fetch=f, home=tmp_path)
    assert not [c for c in f.calls if c[0] in ("PUT", "DELETE", "PATCH")] and ("POST", "/accounts/a1/cfd_tunnel") not in f.calls


def test_cf_api_dns_pointing_elsewhere_is_error(tmp_path):
    f = FakeCF(tunnels=[{"id": "tid"}], dns=[{"content": "other.cfargotunnel.com"}])
    rep = C.Reporter()
    with pytest.raises(C.LaunchError):
        vs_cf.ensure_tunnel("colab", {"CF_API_TOKEN": "apitok12345", "VISOSWAP_HOSTNAME": "cl.example.org"}, rep, C.Runner(rep), fetch=f, home=tmp_path)


def test_cf_shortcircuit_and_quick_fallback(tmp_path, monkeypatch):
    rep = C.Reporter()
    f = FakeCF()
    cfg = vs_cf.ensure_tunnel("kaggle", {"CF_TUNNEL_TOKEN": "t" * 20, "VISOSWAP_HOSTNAME": "h"}, rep, C.Runner(rep), fetch=f, home=tmp_path)
    assert f.calls == [] and cfg["TUNNEL_MODE"] == "named"
    monkeypatch.setattr(vs_cf, "cloudflared_bin", lambda c: None)
    assert vs_cf.ensure_tunnel("kaggle", {}, rep, C.Runner(rep), home=tmp_path)["TUNNEL_MODE"] == "quick"
    with pytest.raises(C.LaunchError):
        vs_cf.ensure_tunnel("kaggle", {}, rep, C.Runner(rep), mode="named", home=tmp_path)


# ---------- colab
SESS = "[t4] gpu-t4-s-x | Hardware: T4 | Shape: Standard | Variant: GPU\n"


def test_parse_and_choose():
    s = CL.parse_sessions(SESS + "garbage\n")
    assert s == [{"name": "t4", "id": "gpu-t4-s-x", "hardware": "T4"}]
    assert CL.choose_session(s, "t4")["action"] == "reuse"
    assert CL.choose_session(s, "other")["action"] == "reuse"          # adopt the only one
    assert CL.choose_session([], "t4")["action"] == "create"
    # the real CLI prints this when nothing runs; it must not be adopted as a session named "colab"
    assert CL.parse_sessions("[colab] No active sessions found on server.\n") == []
    two = s + [{"name": "b", "id": "i", "hardware": "T4"}]
    assert CL.choose_session(two, "zzz")["action"] == "abort"


def test_plan_steps():
    assert CL.plan_steps({}, True, False, False, False) == []
    assert CL.plan_steps({"deps_missing": ["kornia"]}, False, False, False, False) == ["deps", "repo", "models", "start"]
    assert CL.plan_steps({"deps_missing": []}, False, False, False, True) == ["repo", "models"]


def test_remote_scripts_compile_and_hold_no_token():
    for name in ("PROBE", "DEPS", "REPO_STEP", "MODELS", "START", "LOGS"):
        src = vs_remote.render(getattr(vs_remote, name), repo_dir="/r", token_file="/t", ref="master", repo_url="u", subset="default",
                               restart=False, profile=False, concurrency=None, wait=1, which=["uvicorn"], lines=5)
        compile(src, name, "exec")


class Clock:
    t = 0.0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s


def _colab(tmp_path, monkeypatch, sessions="", **env):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    (tmp_path / "sessions.txt").write_text(sessions) if sessions else None
    for k, v in env.items(): monkeypatch.setenv(k, v)
    rep = C.Reporter()
    run = C.Runner(rep)
    clk = Clock()
    return CL.Colab({"COLAB_BIN": str(FAKES / "fake_colab.py")}, rep, run, sleep=clk.sleep, clock=clk, health=lambda u: sum(c.startswith('exec') for c in calls(tmp_path)) >= 4), clk, tmp_path


def calls(d): return (d / "calls.log").read_text().splitlines() if (d / "calls.log").exists() else []


def test_retry_loop_times_out_with_clear_message(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, FAKE_NEW_FAIL="1")
    with pytest.raises(C.LaunchError, match="no L4 became available"):
        co.ensure_session(100)
    assert sum(1 for c in calls(d) if c.startswith("new")) >= 2 and clk.t > 0


def test_never_creates_second_session(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS)
    assert co.ensure_session(60) == "t4"
    assert not any(c.startswith("new") for c in calls(d))


def test_stop_only_via_down(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS)
    args = CL.build_parser().parse_args(["--yes", "--no-clipboard"])
    cfg = {"VISOSWAP_HOSTNAME": "h.example.org", "CF_TUNNEL_TOKEN": TOKEN}
    monkeypatch.setenv("VISOSWAP_HOME", str(tmp_path / "home"))
    CL.launch(args, cfg, co.rep, co.run, co, home=tmp_path / "home")
    CL.restart(args, cfg, co.rep, co.run, co)
    CL.status(args, cfg, co.rep, co.run, co)
    assert not any(c.startswith("stop") for c in calls(d))
    CL.down(args, cfg, co.rep, co.run, co)
    assert [c for c in calls(d) if c.startswith("stop")] == ["stop -s t4"]
    import inspect
    src = inspect.getsource(CL)
    assert src.count('"stop"') == 1


def test_down_needs_confirmation_non_interactive(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS)
    co.rep.json_mode = True
    with pytest.raises(C.LaunchError):
        CL.down(CL.build_parser().parse_args([]), {}, co.rep, co.run, co)
    assert not any(c.startswith("stop") for c in calls(d))


def test_token_never_in_exec_argv_or_scripts(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS)
    monkeypatch.setenv("VISOSWAP_HOME", str(tmp_path / "home"))
    CL.launch(CL.build_parser().parse_args(["--yes", "--no-clipboard"]), {"VISOSWAP_HOSTNAME": "h.example.org", "CF_TUNNEL_TOKEN": TOKEN},
              co.rep, co.run, co, home=tmp_path / "home")
    assert TOKEN not in "".join(calls(d))
    assert any(c.startswith("upload") for c in calls(d))


# ---------- end-to-end CLI with fake binaries, dry-run, py3 any
def _cli(script, args, tmp_path, extra_env):
    env = dict(os.environ, VISOSWAP_HOME=str(tmp_path / "home"), FAKE_DIR=str(tmp_path), HOME=str(tmp_path), **extra_env)
    return subprocess.run([sys.executable, str(L / script), *args], capture_output=True, text=True, env=env, timeout=60)


def test_cli_colab_dry_run_no_session(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "colab.env").write_text(f"CF_TUNNEL_TOKEN={TOKEN}\nVISOSWAP_HOSTNAME=h.example.org\n")
    r = _cli("colab_launch.py", ["--dry-run", "--json"], tmp_path, {"COLAB_BIN": str(FAKES / "fake_colab.py")})
    assert r.returncode == 0, r.stdout + r.stderr
    assert TOKEN not in r.stdout + r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["ok"] is True
    assert not any(c.startswith("new") for c in calls(tmp_path))
    assert not (tmp_path / "home" / "colab_url").exists()


def test_cli_kaggle_status_and_dry_run(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "kaggle.env").write_text(f"CF_TUNNEL_TOKEN={TOKEN}\nVISOSWAP_HOSTNAME=h.example.org\nKAGGLE_KERNEL=faker/visoswap-studio\n")
    env = {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KSTATE": "MISSING"}
    r = _cli("kaggle_launch.py", ["--status", "--json"], tmp_path, env)
    assert json.loads(r.stdout.strip().splitlines()[-1])["state"] == "MISSING", r.stdout + r.stderr
    r = _cli("kaggle_launch.py", ["--dry-run", "--json", "--models-dir", str(tmp_path / "nomodels")], tmp_path, env)
    last = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 0 and last["state"] == "dry-run", r.stdout + r.stderr
    assert TOKEN not in r.stdout
    assert not any(c.startswith("kernels push") for c in calls(tmp_path))


def test_kaggle_unknown_and_auth_never_push(tmp_path):
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "kaggle.env").write_text("KAGGLE_KERNEL=faker/visoswap-studio\n")
    r = _cli("kaggle_launch.py", ["--json", "--tunnel", "quick"], tmp_path,
             {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KSTATE": "MISSING", "FAKE_KLISTED": "1"})
    assert r.returncode == 1
    assert not any(c.startswith("kernels push") or c.startswith("kernels delete") for c in calls(tmp_path))


def test_gitignore_covers_secrets():
    gi = (L.parent.parent / ".gitignore").read_text()
    for pat in ("kaggle.env", "colab.env", "webui_token", "kaggle.json"):
        assert pat in gi


# ---------- web UI (fake launchers)
def _webui(tmp_path, monkeypatch):
    pytest.importorskip("fastapi"); pytest.importorskip("httpx")
    monkeypatch.setenv("VISOSWAP_HOME", str(tmp_path)); monkeypatch.setenv("LAUNCHER_FAKE", "1")
    sys.path.insert(0, str(L / "webui"))
    import importlib, app as A
    A = importlib.reload(A)
    from fastapi.testclient import TestClient
    return A, TestClient(A.app), {"Authorization": "Bearer " + A.state().token}


def test_webui_auth_token_file_and_flow(tmp_path, monkeypatch, capsys):
    import time
    A, c, H = _webui(tmp_path, monkeypatch)
    assert oct((tmp_path / "webui_token").stat().st_mode & 0o777) == "0o600"
    assert c.get("/api/targets").status_code == 401
    assert c.get("/api/targets", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/targets?token=" + A.state().token).status_code == 401      # never via query string
    assert c.get("/api/targets", headers=H).json()["kaggle"]["state"] == "RUNNING"
    jid = c.post("/api/targets/colab/launch", headers=H).json()["job"]
    assert c.post("/api/targets/colab/restart", headers=H).status_code == 409      # serialized per target
    assert c.post("/api/targets/colab/rm", headers=H).status_code == 404           # fixed allowlist
    assert c.post("/api/targets/kaggle/stop", headers=H).status_code == 400        # needs confirm
    for _ in range(40):
        j = c.get(f"/api/jobs/{jid}", headers=H).json()
        if j["state"] != "running": break
        time.sleep(0.2)
    assert j["state"] == "done" and "eyJ" not in json.dumps(j)                      # redacted
    assert A.state().token not in c.get("/", headers=H).text


def test_webui_rate_limits_failed_auth(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    codes = [c.get("/api/targets", headers={"Authorization": "Bearer x"}).status_code for _ in range(12)]
    assert codes[-1] == 429


# ---------- review fixes
def test_plan_steps_locally_healthy_does_not_touch_processes():
    assert CL.plan_steps({"health": True, "tunnel": True}, False, False, False, False) == []
    assert CL.plan_steps({"health": True, "tunnel": True}, False, True, False, False)  # --restart still acts


def test_colab_no_env_token_on_vm_restart_and_status_resolve_url(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS, FAKE_TOKEN_FILE="1")
    monkeypatch.setenv("VISOSWAP_HOME", str(tmp_path / "home"))
    a = CL.build_parser().parse_args(["--yes", "--no-clipboard"])
    co.health = lambda u: True
    st = CL.status(a, {}, co.rep, co.run, co)
    assert st["url"] == "https://" + C.DEFAULT_HOSTNAMES["colab"]
    r = CL.restart(a, {}, co.rep, co.run, co)
    assert r["healthy"] and r["url"] == st["url"]
    assert not any(c.startswith(("stop", "new")) for c in calls(d))


def test_kaggle_launch_does_not_prompt_for_dataset(tmp_path):
    class FakeKg:
        slug = "u/visoswap-studio"; cfg = {}
        def __init__(self): self.rep = C.Reporter(True); self.run = C.Runner(self.rep)
        def dataset(self): return None
    models = tmp_path / "m"; models.mkdir(); (models / "a.onnx").write_bytes(b"x")
    args = K.build_parser().parse_args(["--json", "--models-dir", str(models)])
    kg = K.Kaggle({}, C.Reporter(True), C.Runner(C.Reporter(True)))
    kg.slug = "u/visoswap-studio"; kg.dataset = lambda: None
    assert kg.ensure_dataset(args, create=False) is None      # no LaunchError("needs confirmation")


def test_result_redacts_before_json(capsys):
    rep = C.Reporter(True, C.Redactor())
    rep.result(False, error='boom --token ' + TOKEN)
    assert TOKEN not in capsys.readouterr().out


# ================= preflight / login / token (new requirement) =================
import vs_preflight as P  # noqa: E402

FAKE_TOKEN2 = "eyJhIjoiZmFrZXRva2VuMiIsInQiOiJmYWtlLXR1bm5lbC1pZC0xMjM0NTYiLCJzIjoiYWJjZGVmZ2hpamtsbW5vcCJ9"
RO_SUBCMDS = {"kernels list", "quota", "sessions", "usage", "exec", "kernels status", "datasets status", "security find-generic-password"}


def _kg(tmp_path, monkeypatch, cfg=None, **env):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    for k, v in env.items(): monkeypatch.setenv(k, v)
    rep = C.Reporter()
    c = {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "KAGGLE_KERNEL": "faker/visoswap-studio", **(cfg or {})}
    return c, rep, C.Runner(rep)


def test_kaggle_expired_noninteractive_gives_exact_fix_and_never_logs_in(tmp_path, monkeypatch):
    cfg, rep, run = _kg(tmp_path, monkeypatch, FAKE_KAUTH="expired")
    with pytest.raises(P.PreflightFailed) as e:
        P.resolve(lambda: P.kaggle_checks(cfg, run), {"kaggle.auth": lambda: run.interactive([cfg["KAGGLE_BIN"], "auth", "login"])}, False, rep)
    assert "kaggle-launch.sh --login" in e.value.next_action
    assert not any(c.startswith("auth login") for c in calls(tmp_path))


def test_kaggle_expired_interactive_runs_login_then_ok(tmp_path, monkeypatch):
    cfg, rep, run = _kg(tmp_path, monkeypatch, FAKE_KAUTH="expired")
    checks = P.resolve(lambda: P.kaggle_checks(cfg, run), {"kaggle.auth": lambda: run.interactive([cfg["KAGGLE_BIN"], "auth", "login", "--force"])}, True, rep)
    assert [c for c in calls(tmp_path) if c.startswith("auth login")] == ["auth login --force"]
    assert next(c for c in checks if c.id == "kaggle.auth").state == "ok"
    assert any(c.id == "kaggle.quota" for c in checks)


def test_network_blip_is_unknown_and_never_starts_login(tmp_path, monkeypatch):
    cfg, rep, run = _kg(tmp_path, monkeypatch, FAKE_KAUTH="flaky")
    checks = P.resolve(lambda: P.kaggle_checks(cfg, run), {"kaggle.auth": lambda: run.interactive(["false"])}, True, rep)
    assert next(c for c in checks if c.id == "kaggle.auth").state == "unknown"
    assert not any(c.startswith("auth login") for c in calls(tmp_path))


def test_kaggle_cli_missing_reports_install_command(tmp_path, monkeypatch):
    rep = C.Reporter()
    cs = P.kaggle_checks({"KAGGLE_BIN": "/nonexistent/kaggle"}, C.Runner(rep))
    assert cs[0].state == "missing" and cs[0].fix == "pip install kaggle"


def _colab_cfg(): return {"COLAB_BIN": str(FAKES / "fake_colab.py")}


def test_colab_not_logged_in_does_not_start_cli_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path)); monkeypatch.setenv("FAKE_SECURITY_RC", "44")
    monkeypatch.setenv("PATH", str(FAKES) + os.pathsep + os.environ["PATH"]); monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(P.platform, "system", lambda: "Darwin")
    cs = P.colab_checks(_colab_cfg(), C.Runner(C.Reporter()))
    login = next(c for c in cs if c.id == "colab.login")
    assert login.state == "invalid" and "colab-launch.sh --login" in login.fix and login.runnable
    assert not any(c.startswith("usage") for c in calls(tmp_path))     # the CLI (which would prompt) was never invoked


def test_colab_expired_login_detected_then_guided_login(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path)); monkeypatch.setenv("FAKE_COLAB_EXPIRED", "1")
    monkeypatch.setenv("PATH", str(FAKES) + os.pathsep + os.environ["PATH"]); monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(P.platform, "system", lambda: "Darwin")
    cfg = _colab_cfg(); rep = C.Reporter(); run = C.Runner(rep)
    assert next(c for c in P.colab_checks(cfg, run) if c.id == "colab.login").state == "invalid"
    # non-interactive: exact command, no login attempted
    with pytest.raises(P.PreflightFailed):
        P.resolve(lambda: P.colab_checks(cfg, run), {"colab.login": lambda: 0}, False, rep)
    # interactive: the fixer runs `colab usage` with inherited stdio, then re-checks
    cs = P.resolve(lambda: P.colab_checks(cfg, run), {"colab.login": lambda: (os.environ.__setitem__("FAKE_USAGE_LOGIN", "1"), run.interactive([cfg["COLAB_BIN"], "usage"]))}, True, rep)
    os.environ.pop("FAKE_USAGE_LOGIN", None)
    assert next(c for c in cs if c.id == "colab.login").state == "ok"
    assert "12.50 compute units" in next(c for c in cs if c.id == "colab.units").detail


def test_colab_zero_units_warns_not_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path)); monkeypatch.setenv("FAKE_UNITS", "0.00")
    monkeypatch.setenv("PATH", str(FAKES) + os.pathsep + os.environ["PATH"]); monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(P.platform, "system", lambda: "Linux")
    cs = P.colab_checks(_colab_cfg(), C.Runner(C.Reporter()))
    u = next(c for c in cs if c.id == "colab.units")
    assert "not guaranteed" in u.detail and u.good


def test_cloudflare_stored_token_is_reused_and_never_prompted(tmp_path):
    rep = C.Reporter(); called = []
    cfg = {"CF_TUNNEL_TOKEN": FAKE_TOKEN2, "VISOSWAP_HOSTNAME": "h.example.org"}
    out = vs_cf.ensure_tunnel("kaggle", cfg, rep, C.Runner(rep), interactive=True, prompt=lambda l: called.append(l) or "x", home=tmp_path)
    assert not called and out["TUNNEL_MODE"] == "named"
    chk = P.cloudflare_check("kaggle", cfg)
    assert chk.state == "ok" and FAKE_TOKEN2 not in json.dumps(chk.as_dict())


def test_cloudflare_missing_noninteractive_is_quick_and_interactive_prompts_and_stores_600(tmp_path, monkeypatch):
    monkeypatch.setattr(vs_cf, "cloudflared_bin", lambda c: None)
    rep = C.Reporter()
    assert vs_cf.ensure_tunnel("kaggle", {}, rep, C.Runner(rep), interactive=False, home=tmp_path)["TUNNEL_MODE"] == "quick"
    assert P.cloudflare_check("kaggle", {}, home=tmp_path).state == "missing"
    seen = []
    out = vs_cf.ensure_tunnel("kaggle", {}, rep, C.Runner(rep), interactive=True, home=tmp_path,
                              prompt=lambda l: seen.append(l) or FAKE_TOKEN2)
    assert out["TUNNEL_MODE"] == "named" and FAKE_TOKEN2 not in "".join(seen)
    f = tmp_path / "kaggle.env"
    assert C.parse_env(f.read_text())["CF_TUNNEL_TOKEN"] == FAKE_TOKEN2 and oct(f.stat().st_mode & 0o777) == "0o600"
    # empty answer -> quick tunnel; garbage -> error
    assert vs_cf.ensure_tunnel("colab", {}, rep, C.Runner(rep), interactive=True, home=tmp_path, prompt=lambda l: "")["TUNNEL_MODE"] == "quick"
    with pytest.raises(C.LaunchError):
        vs_cf.ensure_tunnel("colab", {}, rep, C.Runner(rep), interactive=True, home=tmp_path, prompt=lambda l: "short")


def test_store_token_never_overwrites_and_validates(tmp_path):
    rep = C.Reporter()
    vs_cf.store_token("kaggle", FAKE_TOKEN2, "h.example.org", rep, home=tmp_path)
    with pytest.raises(C.LaunchError, match="already stored"):
        vs_cf.store_token("kaggle", FAKE_TOKEN2 + "x", "h.example.org", rep, home=tmp_path)
    with pytest.raises(C.LaunchError):
        vs_cf.store_token("colab", "nope", "h", rep, home=tmp_path)


def _snapshot(d):
    return {str(p.relative_to(d)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode) for p in sorted(d.rglob("*")) if p.is_file()}


def test_cli_check_is_read_only_and_prints_no_secret(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    (home / "kaggle.env").write_text(f"CF_TUNNEL_TOKEN={FAKE_TOKEN2}\nVISOSWAP_HOSTNAME=h.example.org\nKAGGLE_KERNEL=faker/visoswap-studio\n")
    (home / "colab.env").write_text(f"CF_TUNNEL_TOKEN={FAKE_TOKEN2}\nVISOSWAP_HOSTNAME=c.example.org\n")
    (tmp_path / "sessions.txt").write_text(SESS)
    before = _snapshot(home)
    env = {"PATH": str(FAKES) + os.pathsep + os.environ["PATH"], "FAKE_SECURITY_RC": "0"}
    k = _cli("kaggle_launch.py", ["--check", "--json"], tmp_path, {**env, "KAGGLE_BIN": str(FAKES / "fake_kaggle.py")})
    c = _cli("colab_launch.py", ["--check", "--json"], tmp_path, {**env, "COLAB_BIN": str(FAKES / "fake_colab.py")})
    for r in (k, c):
        assert r.returncode == 0, r.stdout + r.stderr
        assert FAKE_TOKEN2 not in r.stdout + r.stderr
    kr, cr = json.loads(k.stdout.splitlines()[-1]), json.loads(c.stdout.splitlines()[-1])
    assert {x["id"] for x in kr["checks"]} >= {"kaggle.cli", "kaggle.auth", "cloudflare"}
    assert {x["id"] for x in cr["checks"]} >= {"colab.cli", "colab.login", "colab.units", "colab.session", "cloudflare"}
    assert _snapshot(home) == before                       # byte-identical, no lock file, no url files
    for line in calls(tmp_path):
        assert any(line.startswith(p) for p in RO_SUBCMDS), line


def test_cli_setup_json_everything_missing_returns_promptly_with_fix(tmp_path):
    (tmp_path / "home").mkdir()
    r = _cli("kaggle_launch.py", ["--setup", "--json"], tmp_path, {"KAGGLE_BIN": "/nonexistent/kaggle"})
    last = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 1 and last["ok"] is False and "pip install kaggle" in last["next"]
    r = _cli("colab_launch.py", ["--setup", "--json"], tmp_path, {"COLAB_BIN": "/nonexistent/colab"})
    last = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 1 and "google-colab-cli" in last["next"]


def test_check_mode_runner_rejects_mutation():
    run = C.Runner(C.Reporter(), readonly=True)
    with pytest.raises(AssertionError):
        run.run(["x"], mutating=True)
    with pytest.raises(AssertionError):
        run.interactive(["x"])


def test_can_prompt_gate(monkeypatch):
    import argparse
    ns = lambda **k: argparse.Namespace(json=False, dry_run=False, check=False, **k)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False); monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    assert C.can_prompt(ns())
    for flag in ("json", "dry_run", "check"):
        a = ns(); setattr(a, flag, True); assert not C.can_prompt(a)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    assert not C.can_prompt(ns())


# ---------- restart preserves settings
def test_merge_settings_precedence():
    M = vs_remote.merge_settings
    live = {"GENERATION_CONCURRENCY": "3", "VISOSWAP_PROFILE": "1", "MODELS_SUBSET": "default", "MODELS_VERIFY_MODE": "fast"}
    cfg = {"subset": "default", "concurrency": None, "profile": None}
    s = M(live, "running process", cfg)
    assert s["GENERATION_CONCURRENCY"] == "3" and s["VISOSWAP_PROFILE"] == "1" and "VISOSWAP_PROCESS_WORKERS" not in s
    assert "GENERATION_CONCURRENCY" not in M(live, "running process", {**cfg, "concurrency": "auto"})
    assert M(live, "running process", {**cfg, "concurrency": 5})["GENERATION_CONCURRENCY"] == "5"
    assert M(live, "running process", {**cfg, "profile": False})["VISOSWAP_PROFILE"] == "0"
    d = M({}, "defaults", cfg)
    assert d["VISOSWAP_PROFILE"] == "0" and "VISOSWAP_PROCESS_WORKERS" not in d and "GENERATION_CONCURRENCY" not in d


def test_settings_overrides_flags_beat_env_file():
    ov = CL.settings_overrides
    p = CL.build_parser().parse_args
    assert ov(p([]), {}) == {"concurrency": None, "profile": None}
    assert ov(p([]), {"GENERATION_CONCURRENCY": "3", "VISOSWAP_PROFILE": "1"}) == {"concurrency": "3" and 3, "profile": True}
    assert ov(p(["--concurrency", "auto", "--no-profile"]), {"GENERATION_CONCURRENCY": "3", "VISOSWAP_PROFILE": "1"}) == {"concurrency": "auto", "profile": False}
    assert ov(p(["--concurrency", "2", "--profile"]), {})["concurrency"] == 2


def test_restart_and_launch_pass_preserving_settings(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS)
    a = CL.build_parser().parse_args(["--restart", "--no-clipboard"])
    sent = []
    orig = co.step
    co.step = lambda name, script, timeout, **cfg: (sent.append((name, cfg)), orig(name, script, timeout, **cfg))[1]
    co.health = lambda u: True
    monkeypatch.setenv("VISOSWAP_HOME", str(tmp_path / "h"))
    CL.restart(a, {"VISOSWAP_HOSTNAME": "h.example.org"}, co.rep, co.run, co)
    start = next(c for n, c in sent if n == "start")
    assert start["concurrency"] is None and start["profile"] is None and start["restart"] is True   # None = keep what runs now
    sent.clear()
    CL.launch(CL.build_parser().parse_args(["--yes", "--no-clipboard", "--concurrency", "3"]),
              {"VISOSWAP_HOSTNAME": "h.example.org", "CF_TUNNEL_TOKEN": TOKEN, "VISOSWAP_PROFILE": "1"}, co.rep, co.run, co, home=tmp_path / "h")
    st = [c for n, c in sent if n == "start"]
    assert st and st[0]["concurrency"] == 3 and st[0]["profile"] is True


# ---------- web UI: checks, login, token form
def test_webui_checks_login_and_token_endpoints(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    r = c.get("/api/checks", headers=H).json()
    assert r["targets"]["kaggle"]["checks"][0]["state"] == "ok" and set(r["targets"]["kaggle"]["checks"][0]) <= set(A.CHECK_FIELDS)
    assert c.get("/api/checks").status_code == 401
    assert c.post("/api/targets/colab/nologin", headers=H).status_code == 404
    assert c.post("/api/targets/kaggle/cloudflare-token", headers=H, json={"token": "short"}).status_code == 400
    r = c.post("/api/targets/kaggle/cloudflare-token", headers=H, json={"token": FAKE_TOKEN2})
    assert r.json() == {"ok": True, "error": ""} and FAKE_TOKEN2 not in r.text
    assert c.post("/api/targets/kaggle/cloudflare-token", json={"token": FAKE_TOKEN2}).status_code == 401
    monkeypatch.setattr(A, "IN_DOCKER", True)
    assert c.post("/api/targets/kaggle/login", headers=H).status_code == 400
    assert A.clean_result({"ok": True, "checks": [{"id": "x", "secret": "s", "state": "ok"}], "token": "t"}) == {"ok": True, "checks": [{"id": "x", "state": "ok"}]}


# ---------- review round 2
FAKE_CF = FAKES / "fake_cloudflared.py"


def test_cert_path_reads_stdout_only_despite_stderr_warnings(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    (tmp_path / "home").mkdir(); (tmp_path / "home" / ".cloudflared").mkdir(); (tmp_path / "home" / ".cloudflared" / "cert.pem").write_text("x")
    rep = C.Reporter(); run = C.Runner(rep)
    cfg = {"CLOUDFLARED_BIN": str(FAKE_CF), "VISOSWAP_HOSTNAME": "swap.example.org"}
    out = vs_cf.ensure_tunnel("kaggle", cfg, rep, run, home=tmp_path / "home")
    assert out["TUNNEL_MODE"] == "named" and out["CF_TUNNEL_TOKEN"] == FAKE_TOKEN2
    assert not any(c.startswith("tunnel create") for c in calls(tmp_path))          # found by name: not created
    assert not any("--overwrite-dns" in c or " delete" in c for c in calls(tmp_path))


def test_cert_path_zero_time_deleted_at_means_live(tmp_path, monkeypatch):
    """Live run: cloudflared reports deleted_at "0001-01-01T00:00:00Z" for live tunnels; that must not trigger a create."""
    assert not vs_cf.is_deleted({"deleted_at": "0001-01-01T00:00:00Z"})
    assert not vs_cf.is_deleted({}) and vs_cf.is_deleted({"deleted_at": "2026-01-01T00:00:00Z"})
    monkeypatch.setenv("FAKE_DIR", str(tmp_path)); monkeypatch.setenv("FAKE_CF_DELETED", "1")
    (tmp_path / "home" / ".cloudflared").mkdir(parents=True); (tmp_path / "home" / ".cloudflared" / "cert.pem").write_text("x")
    rep = C.Reporter()
    vs_cf.ensure_tunnel("kaggle", {"CLOUDFLARED_BIN": str(FAKE_CF), "VISOSWAP_HOSTNAME": "s.example.org"}, rep, C.Runner(rep), home=tmp_path / "home")
    assert any(c.startswith("tunnel create") for c in calls(tmp_path))     # only a really deleted one is recreated


def test_cert_path_rejects_garbage_token_and_unparsable_list(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path)); monkeypatch.setenv("FAKE_CF_BAD_TOKEN", "1")
    (tmp_path / "home" / ".cloudflared").mkdir(parents=True); (tmp_path / "home" / ".cloudflared" / "cert.pem").write_text("x")
    rep = C.Reporter()
    with pytest.raises(C.LaunchError, match="does not look valid"):
        vs_cf.ensure_tunnel("kaggle", {"CLOUDFLARED_BIN": str(FAKE_CF), "VISOSWAP_HOSTNAME": "s.example.org"}, rep, C.Runner(rep), home=tmp_path / "home")
    assert not (tmp_path / "home" / "kaggle.env").exists()
    monkeypatch.setenv("FAKE_CF_BAD_LIST", "1")
    with pytest.raises(C.LaunchError, match="could not parse"):
        vs_cf.ensure_tunnel("kaggle", {"CLOUDFLARED_BIN": str(FAKE_CF), "VISOSWAP_HOSTNAME": "s.example.org"}, rep, C.Runner(rep), home=tmp_path / "home")


def test_start_script_waits_for_old_pids_and_notes_defaults():
    src = vs_remote.render(vs_remote.START, repo_dir="/r", token_file="/t", ref="m", repo_url="u", subset="default", restart=True,
                           profile=None, concurrency=None, wait=1)
    compile(src, "start", "exec")
    assert "kill\", \"-9\"" in src.replace("'", '"') or '"-9"' in src
    assert "VS_NOTE no earlier settings found" in src and "def merge_settings" in src


def test_login_fix_strings_do_not_assume_cli_on_path():
    assert P.KAGGLE_LOGIN.endswith("kaggle-launch.sh --login") and Path(P.KAGGLE_LOGIN.split()[0]).is_absolute()


def test_webui_checks_skip_probe_while_busy(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    A.state().busy["colab"] = "job"
    r = c.get("/api/checks", headers=H).json()["targets"]["colab"]
    assert r["checks"] == []
    A.state().busy.pop("colab")


# =========================================================================== turn 4: UI-driven logins, L4 default, l4 reconciliation
import io, time  # noqa: E402


def _reporter_json():
    buf = io.StringIO()
    return C.Reporter(json_mode=True, stream=buf), buf


GOODTOK = "GOODTOKEN-abcdef0123456789"


def test_kaggle_token_install_validates_in_isolation_and_stores_600(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    monkeypatch.setenv("FAKE_KAUTH", "expired")
    (tmp_path / "kaggle_ok").write_text("1")                       # an OAuth login exists and would mask a bad token
    kdir = Path(os.environ["KAGGLE_CONFIG_DIR"]); kdir.mkdir(parents=True)
    (kdir / "credentials.json").write_text("{}")
    rep, buf = _reporter_json()
    run = C.Runner(rep)
    kg = K.Kaggle({"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "KAGGLE_KERNEL": "f/x"}, rep, run)
    with pytest.raises(C.LaunchError):
        K.install_kaggle_token("BADTOKEN-abcdef0123456789", {}, rep, run, kg)
    assert not (kdir / "access_token").exists()                    # rejected: nothing stored
    K.install_kaggle_token(GOODTOK, {}, rep, run, kg)
    f = kdir / "access_token"
    assert f.read_text().strip() == GOODTOK and oct(f.stat().st_mode & 0o777) == "0o600" and oct(kdir.stat().st_mode & 0o777) == "0o700"
    assert (kdir / "credentials.json").read_text() == "{}"         # never modified
    assert GOODTOK not in buf.getvalue() and "BADTOKEN-abcdef0123456789" not in buf.getvalue()
    cs = P.kaggle_checks({"KAGGLE_BIN": str(FAKES / "fake_kaggle.py")}, C.Runner(C.Reporter()))
    assert next(c for c in cs if c.id == "kaggle.auth").state == "ok"
    assert calls(tmp_path) and not any("BADTOKEN" in c or GOODTOK in c for c in calls(tmp_path))     # token travels by env, never argv
    probes = (tmp_path / "env.log").read_text().splitlines()[:2]                    # the two validation probes ran in a throwaway HOME
    for ln in probes:
        home, cfgdir, tok = ln.split("|")
        assert home != os.environ["HOME"] and cfgdir != os.environ["KAGGLE_CONFIG_DIR"] and tok == "token" and "visoswap-kcheck-" in home
    assert not Path(probes[0].split("|")[0]).exists()                               # and it was removed


def test_kaggle_token_rejects_garbage_without_calling_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    rep = C.Reporter(); run = C.Runner(rep)
    kg = K.Kaggle({"KAGGLE_BIN": str(FAKES / "fake_kaggle.py")}, rep, run)
    for bad in ("", "short", "has space inside the token value"):
        with pytest.raises(C.LaunchError):
            K.install_kaggle_token(bad, {}, rep, run, kg)
    assert calls(tmp_path) == []


def _relay(script, args, tmp_path, env, lines):
    """Run a launcher in relay mode; feed `lines` one at a time after each prompt. -> (events, rc)"""
    e = dict(os.environ, VISOSWAP_HOME=str(tmp_path / "home"), FAKE_DIR=str(tmp_path), HOME=str(tmp_path), PYTHONUNBUFFERED="1", **env)
    p = subprocess.Popen([sys.executable, str(L / script), *args, "--json"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, env=e)
    events, feed = [], list(lines)
    t0 = time.time()
    for raw in p.stdout:
        ev = json.loads(raw)
        events.append(ev)
        if ev.get("t") == "prompt" and feed:
            p.stdin.write(feed.pop(0) + "\n"); p.stdin.flush()
        assert time.time() - t0 < 60
    p.wait(timeout=30)
    return events, p.returncode


def test_cli_kaggle_relay_login_pasted_token(tmp_path):
    ev, rc = _relay("kaggle_launch.py", ["--login", "--relay"], tmp_path,
                    {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KAUTH": "expired", "KAGGLE_CONFIG_DIR": str(tmp_path / ".kaggle")}, [GOODTOK])
    assert rc == 0 and ev[-1]["ok"] is True, ev
    assert any(e["t"] == "prompt" and e["kind"] == "secret" for e in ev)
    assert GOODTOK not in json.dumps(ev)
    assert oct((tmp_path / ".kaggle" / "access_token").stat().st_mode & 0o777) == "0o600"


def test_cli_colab_relay_login_url_then_code(tmp_path):
    ev, rc = _relay("colab_launch.py", ["--login", "--relay"], tmp_path,
                    {"COLAB_BIN": str(FAKES / "fake_colab.py"), "FAKE_COLAB_EXPIRED": "1"}, ["GOODCODE-123456789"])
    pr = [e for e in ev if e["t"] == "prompt"]
    assert rc == 0 and ev[-1]["ok"] is True, ev
    assert len(pr) == 1 and pr[0]["kind"] == "code" and pr[0]["url"].startswith("https://accounts.google.com/")
    assert "client_id=abc" in pr[0]["url"]                      # a realistic auth URL is not mangled by redaction
    assert "GOODCODE-123456789" not in json.dumps(ev)


def test_cli_colab_relay_wrong_code_fails_and_valid_login_asks_nothing(tmp_path):
    ev, rc = _relay("colab_launch.py", ["--login", "--relay"], tmp_path,
                    {"COLAB_BIN": str(FAKES / "fake_colab.py"), "FAKE_COLAB_EXPIRED": "1"}, ["WRONG-CODE-123456789"])
    assert rc == 1 and ev[-1]["ok"] is False
    ev, rc = _relay("colab_launch.py", ["--login", "--relay"], tmp_path, {"COLAB_BIN": str(FAKES / "fake_colab.py")}, [])
    assert rc == 0 and not any(e["t"] == "prompt" for e in ev)  # already logged in: no prompt at all


def test_relay_without_a_driver_fails_fast_not_hangs(tmp_path):
    e = dict(os.environ, VISOSWAP_HOME=str(tmp_path / "home"), FAKE_DIR=str(tmp_path), HOME=str(tmp_path), COLAB_BIN=str(FAKES / "fake_colab.py"), FAKE_COLAB_EXPIRED="1")
    t0 = time.time()
    r = subprocess.run([sys.executable, str(L / "colab_launch.py"), "--login", "--relay", "--json"], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, env=e, timeout=60)
    assert r.returncode == 1 and time.time() - t0 < 30
    assert json.loads(r.stdout.strip().splitlines()[-1])["ok"] is False


def test_safe_login_url_only_google_https():
    f = CL.safe_login_url
    assert f("visit https://accounts.google.com/o/oauth2/auth?x=1 now").startswith("https://accounts.google.com/")
    assert f("https://evil.example/?u=accounts.google.com") == "" and f("http://accounts.google.com/x") == ""
    assert f("https://accounts.google.com.evil.example/x") == ""


def test_cf_api_token_from_stdin_creates_tunnel_stores_only_tunnel_token(tmp_path, monkeypatch):
    for mod, tgt, host in ((K, "kaggle", "swap.example.org"), (CL, "colab", "clswap.example.org")):
        f = FakeCF()
        rep, buf = _reporter_json()
        APITOK = "cfapitoken-abcdefghijklmnop" + tgt
        monkeypatch.setattr(sys, "stdin", io.StringIO(APITOK + "\n"))
        home = tmp_path / tgt; home.mkdir()
        res = mod.cf_api_setup(None, {"VISOSWAP_HOSTNAME": host}, rep, C.Runner(rep), fetch=f, home=home)
        assert res["ok"] and ("POST", "/accounts/a1/cfd_tunnel") in f.calls
        env = (home / f"{tgt}.env").read_text()
        assert TOKEN in env and APITOK not in env and "CF_API_TOKEN" not in env
        assert oct((home / f"{tgt}.env").stat().st_mode & 0o777) == "0o600"
        assert APITOK not in buf.getvalue() and TOKEN not in buf.getvalue()


def test_cf_api_token_rejects_garbage(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("short\n"))
    with pytest.raises(C.LaunchError):
        K.cf_api_setup(None, {}, C.Reporter(), C.Runner(C.Reporter()), fetch=FakeCF(), home=tmp_path)


def test_preflight_failure_result_carries_checks(tmp_path):
    (tmp_path / "home").mkdir()
    r = _cli("kaggle_launch.py", ["--json", "--tunnel", "quick"], tmp_path,
             {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "FAKE_KAUTH": "expired", "KAGGLE_CONFIG_DIR": str(tmp_path / ".kaggle")})
    last = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 1 and last["ok"] is False
    assert any(c["id"] == "kaggle.auth" and c["state"] == "invalid" for c in last["checks"])


def test_valid_logins_launch_asks_nothing_even_on_a_tty(tmp_path, monkeypatch):
    """With every login valid and a tunnel token stored, a launch on a (forced) terminal never prompts or runs a login."""
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("FAKE_DIR", str(tmp_path)); monkeypatch.setenv("VISOSWAP_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "kaggle.env").write_text(f"CF_TUNNEL_TOKEN={TOKEN}\nVISOSWAP_HOSTNAME=h.example.org\nKAGGLE_KERNEL=faker/visoswap-studio\n")
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted/login with valid credentials"))
    monkeypatch.setattr(K, "can_prompt", lambda a: True)
    monkeypatch.setattr(vs_cf, "prompt_secret", boom)
    monkeypatch.setattr(C.Runner, "interactive", boom)
    monkeypatch.setattr(K.vs_cf, "default_fetch", boom)
    rc = K.main(["--setup", "--yes", "--json", "--models-dir", str(tmp_path / "nomodels")])
    assert rc == 0
    assert not any(c.startswith("auth login") for c in calls(tmp_path))


def _booby_trap(monkeypatch, mod):
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted / ran a login although every login is valid"))
    monkeypatch.setattr(mod, "can_prompt", lambda a: True)
    monkeypatch.setattr(vs_cf, "prompt_secret", boom)
    monkeypatch.setattr(C.Runner, "interactive", boom)


def test_plain_kaggle_launch_with_valid_login_asks_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", str(tmp_path)); monkeypatch.setenv("FAKE_KSTATE", "RUNNING")
    _booby_trap(monkeypatch, K)
    cfg = {"KAGGLE_BIN": str(FAKES / "fake_kaggle.py"), "KAGGLE_KERNEL": "faker/visoswap-studio", "CF_TUNNEL_TOKEN": TOKEN, "VISOSWAP_HOSTNAME": "h.example.org"}
    rep = C.Reporter(); run = C.Runner(rep)
    kg = K.Kaggle(cfg, rep, run, health=lambda u: True)
    res = K.launch(K.build_parser().parse_args(["--yes", "--no-clipboard"]), cfg, rep, run, kg, home=tmp_path / "home")
    assert res["ok"] and res["state"] == "RUNNING"


def test_plain_colab_launch_with_valid_login_asks_nothing(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS)
    monkeypatch.setenv("VISOSWAP_HOME", str(tmp_path / "home"))
    _booby_trap(monkeypatch, CL)
    CL.launch(CL.build_parser().parse_args(["--yes", "--no-clipboard"]), {"VISOSWAP_HOSTNAME": "h.example.org", "CF_TUNNEL_TOKEN": TOKEN},
              co.rep, co.run, co, home=tmp_path / "home")


# ---------- Colab: L4 default, burn/units, real rejection strings
def test_colab_default_is_l4_session_named_after_gpu(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch)
    assert co.gpu == "L4" and co.session == "l4"
    co.ensure_session(600)
    assert any(c.startswith("new -s l4 --gpu L4") for c in calls(d))
    assert CL.build_parser().parse_args([]).gpu is None            # default applied in Colab(), T4 stays selectable


def test_colab_t4_optional_and_reuse_notes_mismatch(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions="[l4] gpu-l4-x | Hardware: L4 | Shape: Standard | Variant: GPU\n")
    co.gpu = "T4"
    assert co.ensure_session(60) == "l4"                           # reused, never replaced
    assert not any(c.startswith("new") for c in calls(d))


def test_colab_zero_units_fail_fast_for_l4_but_t4_proceeds(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, FAKE_UNITS="0")
    with pytest.raises(C.LaunchError) as e:
        co.ensure_session(1800)
    assert "--gpu T4" in e.value.next_action and not any(c.startswith("new") for c in calls(d)) and clk.t == 0
    co.gpu = "T4"
    co.ensure_session(60)
    assert any(c.startswith("new") and "--gpu T4" in c for c in calls(d))


def test_colab_entitlement_rejection_is_not_retried(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch, FAKE_NEW_REJECT="1")
    with pytest.raises(C.LaunchError) as e:
        co.ensure_session(1800)
    assert sum(c.startswith("new") for c in calls(d)) == 1 and "--gpu T4" in e.value.next_action


# ---------- remote scripts reconciled with the first real L4 deploy
def test_remote_scripts_match_what_worked_on_l4():
    d, st, m = vs_remote.DEPS, vs_remote.START, vs_remote.MODELS
    for pkg in ("opencv-python", "Pillow", "requests", "tqdm", "pydantic"):
        assert pkg in d
    assert "onnxruntime-gpu>=1.27" in d and "ffmpeg" in d
    assert "--token-file" in st and '"--token", open' not in st            # token stays out of argv
    assert 'pgrep("engine-worker")' in st and 'pgrep("multiprocessing.spawn")' in st
    assert "start_new_session=True" in m and "/content/models.log" in m and "VS_PENDING models" in m
    assert "VISOSWAP_PROCESS_WORKERS" in st                                # only preserved/whitelisted, never invented
    for src in (d, st, m):
        compile(vs_remote.render(src, repo_dir="/r", token_file="/t", ref="m", repo_url="u", subset="default", restart=False,
                                 profile=None, concurrency=None, wait=1, which=[], lines=1, poll=1), "x", "exec")


def test_models_inner_job_code_compiles():
    src = vs_remote.render(vs_remote.MODELS, repo_dir="/r", token_file="/t", ref="m", repo_url="u", subset="default", restart=False,
                           profile=None, concurrency=None, wait=1, which=[], lines=1, poll=1)
    head = src[:src.index("os.makedirs(MODELS")]
    ns = {}
    exec(head, ns)                                               # builds the inner job source; no side effects before makedirs
    compile(ns["code"], "models_job", "exec")
    assert "repair(models_dir=Path('/r/model_assets_owned')" in ns["code"]


def test_models_step_polls_until_done(tmp_path, monkeypatch):
    co, clk, d = _colab(tmp_path, monkeypatch)
    outs = iter(["VS_PENDING models 10%", "VS_PENDING models 60%", "VS_OK models"])
    monkeypatch.setattr(co, "remote", lambda *a, **k: next(outs))
    co.models()
    monkeypatch.setattr(co, "remote", lambda *a, **k: "VS_FAIL models bootstrap incomplete")
    with pytest.raises(C.LaunchError):
        co.models()


# ---------- web UI: login relay, redaction, auto-continue
def _wait(c, H, jid, states, tries=100):
    for _ in range(tries):
        j = c.get(f"/api/jobs/{jid}", headers=H).json()
        if j["state"] in states:
            return j
        time.sleep(0.1)
    raise AssertionError(("timeout", j))


def test_webui_colab_login_relay_url_link_code_and_redaction(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    jid = c.post("/api/targets/colab/login", headers=H).json()["job"]
    j = _wait(c, H, jid, ("waiting", "done", "failed"))
    assert j["state"] == "waiting" and j["prompt"]["kind"] == "code" and j["prompt"]["url"].startswith("https://accounts.google.com/")
    assert c.post(f"/api/jobs/{jid}/input", headers=H, json={"value": ""}).status_code == 400
    assert c.post(f"/api/jobs/{jid}/input", json={"value": "x"}).status_code == 401
    r = c.post(f"/api/jobs/{jid}/input", headers=H, json={"value": "GOODCODE-12345678"})
    assert r.status_code == 200 and "GOODCODE" not in r.text
    j = _wait(c, H, jid, ("done", "failed"))
    assert j["state"] == "done" and "GOODCODE" not in json.dumps(j)         # the fake tool echoed it; redacted
    assert c.post(f"/api/jobs/{jid}/input", headers=H, json={"value": "again"}).status_code == 409


def test_webui_login_url_must_be_google_https(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_LOGIN_URL", "https://evil.example/phish")
    A, c, H = _webui(tmp_path, monkeypatch)
    jid = c.post("/api/targets/colab/login", headers=H).json()["job"]
    j = _wait(c, H, jid, ("waiting",))
    assert j["prompt"]["url"] == ""                                          # the UI renders no link
    c.post(f"/api/jobs/{jid}/cancel", headers=H)
    assert _wait(c, H, jid, ("failed",))["result"]["error"] == "cancelled"


def test_webui_kaggle_token_paste_job(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    jid = c.post("/api/targets/kaggle/login", headers=H).json()["job"]
    j = _wait(c, H, jid, ("waiting",))
    assert j["prompt"]["kind"] == "secret" and j["prompt"]["url"] == ""
    c.post(f"/api/jobs/{jid}/input", headers=H, json={"value": GOODTOK})
    j = _wait(c, H, jid, ("done", "failed"))
    assert j["state"] == "done" and GOODTOK not in json.dumps(j)


def test_webui_docker_refuses_logins_with_plain_reason(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "IN_DOCKER", True)
    r = c.post("/api/targets/colab/login", headers=H)
    assert r.status_code == 400 and "run.sh native" in r.json()["detail"] and "keyring" in r.json()["detail"]
    r = c.post("/api/targets/kaggle/login", headers=H)
    assert r.status_code == 400 and "run.sh native" in r.json()["detail"]
    ch = c.get("/api/checks?refresh=1", headers=H).json()
    assert ch["login_ui"] == {"kaggle": False, "colab": False} and "native" in ch["login_note"]["colab"]


def test_webui_cloudflare_api_token_endpoint(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    assert c.post("/api/targets/kaggle/cloudflare-api-token", headers=H, json={"token": "short"}).status_code == 400
    tok = "cfapi-token-abcdefghijklmnop"
    r = c.post("/api/targets/kaggle/cloudflare-api-token", headers=H, json={"token": tok})
    assert r.json()["ok"] is True and tok not in r.text
    assert c.post("/api/targets/kaggle/cloudflare-api-token", json={"token": tok}).status_code == 401


def test_webui_422_does_not_echo_submitted_secret(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    r = c.post("/api/targets/kaggle/cloudflare-token", headers=H, content=b'["SECRETVALUE-123456789"]')
    assert r.status_code == 422 and "SECRETVALUE" not in r.text
    r = c.post("/api/jobs/abc/input", headers=H, content=b'"SECRETVALUE-123456789"')
    assert "SECRETVALUE" not in r.text


def test_webui_launch_blocked_by_login_returns_whitelisted_checks_then_continues(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_NEEDS_LOGIN", "1")
    A, c, H = _webui(tmp_path, monkeypatch)
    jid = c.post("/api/targets/kaggle/launch", headers=H).json()["job"]
    j = _wait(c, H, jid, ("failed", "done"))
    assert j["state"] == "failed" and j["result"]["checks"][0]["id"] == "kaggle.auth" and "extra" not in j["result"]["checks"][0]
    lj = c.post("/api/targets/kaggle/login", headers=H).json()["job"]
    _wait(c, H, lj, ("waiting",)); c.post(f"/api/jobs/{lj}/input", headers=H, json={"value": GOODTOK}); _wait(c, H, lj, ("done",))
    jid = c.post("/api/targets/kaggle/launch", headers=H).json()["job"]       # what the UI re-posts automatically
    assert _wait(c, H, jid, ("done", "failed"))["state"] == "done"


def test_run_launcher_redacts_per_field_and_keeps_urls(tmp_path, monkeypatch):
    A, c, H = _webui(tmp_path, monkeypatch)
    red = A.state().redactor()
    red.add("SECRET\"VALUE-abcdef")
    out = A.redact_obj(red, {"error": 'x SECRET"VALUE-abcdef y', "url": "https://accounts.google.com/o/oauth2/auth?client_id=abc&state=s1", "n": 3})
    assert "SECRET" not in out["error"] and out["url"].endswith("state=s1") and out["n"] == 3
    assert A.safe_google_url("https://accounts.google.com/o/oauth2/auth?client_id=abc&state=s1")


def test_health_sends_own_user_agent(monkeypatch):
    """Live run: Cloudflare answers urllib's default UA with 403 'error code: 1010', so health never passed."""
    seen = {}

    class R:
        status = 200
        def read(self, n): return b'{"status":"ok"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return R()
    monkeypatch.setattr(C.urllib.request, "urlopen", fake_urlopen)
    assert C.fetch_health("https://h.example.org") is True
    assert seen["ua"] == C.USER_AGENT and "urllib" not in seen["ua"].lower()


APIKEY = "k3y-Value_With!Symbols#0123456789abcdef"


def test_api_key_uploaded_as_file_never_in_argv_scripts_or_logs(tmp_path, monkeypatch, capsys):
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS)
    monkeypatch.setenv("VISOSWAP_HOME", str(tmp_path / "home"))
    kf = tmp_path / "api_key"; kf.write_text(APIKEY + "\n"); kf.chmod(0o600)
    scripts = []
    orig = vs_remote.render
    monkeypatch.setattr(vs_remote, "render", lambda s, **c: scripts.append(orig(s, **c)) or scripts[-1])
    monkeypatch.setattr(CL, "auth_state", lambda url: "required")
    args = CL.build_parser().parse_args(["--yes", "--no-clipboard", "--api-key-file", str(kf)])
    CL.launch(args, {"VISOSWAP_HOSTNAME": "h.example.org", "CF_TUNNEL_TOKEN": TOKEN}, co.rep, co.run, co, home=tmp_path / "home")
    assert any(c.startswith("upload") and c.split()[2] == CL.REMOTE_API_KEY for c in calls(d))
    start = [s for s in scripts if "VS_AUTH" in s]
    assert start and all(APIKEY not in s for s in scripts) and '"api_key_set": true' in start[-1]
    out = capsys.readouterr()
    assert APIKEY not in "".join(calls(d)) + out.out + out.err
    CL.restart(args, {"VISOSWAP_HOSTNAME": "h.example.org"}, co.rep, co.run, co)     # restart uploads it too
    assert sum(c.split()[2] == CL.REMOTE_API_KEY for c in calls(d) if c.startswith("upload")) == 2


def test_api_key_file_rejected_when_loose_or_invalid(tmp_path):
    rep = C.Reporter()
    kf = tmp_path / "k"; kf.write_text(APIKEY); kf.chmod(0o644)
    args = CL.build_parser().parse_args(["--api-key-file", str(kf)])
    with pytest.raises(C.LaunchError, match="readable by other users"):
        CL.load_api_key(args, {}, rep)
    kf.chmod(0o600); kf.write_text("has space")
    with pytest.raises(C.LaunchError, match="spaces"):
        CL.load_api_key(args, {}, rep)


def test_check_auth_refuses_silently_unkeyed_backend():
    with pytest.raises(C.LaunchError, match="auth 'off'"):
        CL.check_auth("https://h", {"api_key_set": True}, C.Reporter(), probe=lambda u: "off")
    CL.check_auth("https://h", {}, C.Reporter(), probe=lambda u: 1 / 0)      # no key requested: not probed


def test_start_script_reads_key_from_file_only():
    src = vs_remote.render(vs_remote.START, repo_dir="/r", token_file="/t", api_key_file="/content/api_key", ref="m", repo_url="u",
                           subset="default", restart=False, profile=None, concurrency=None, wait=1, which=[], lines=1)
    compile(src, "START", "exec")
    assert 'env["VISOSWAP_API_KEY"] = _key' in src and 'env.pop("VISOSWAP_API_KEY", None)' in src
    assert "print(_key" not in src and "VISOSWAP_API_KEY" not in src.split("KEYS = ")[1].split("\n")[0]


def test_probe_retries_dropped_exec(tmp_path, monkeypatch):
    """Live run: a 503 from the kernel proxy failed the whole --restart on the first probe."""
    co, clk, d = _colab(tmp_path, monkeypatch, sessions=SESS)
    outs = iter(["HTTPError: 503 Server Error: Service Unavailable"])
    real = co.remote
    monkeypatch.setattr(co, "remote", lambda name, *a, **k: next(outs, None) or real(name, *a, **k))
    assert co.probe()["gpu"] == "Tesla T4" and clk.t == 10
    outs = iter(["x"] * 5)
    monkeypatch.setattr(co, "remote", lambda name, *a, **k: next(outs))
    with pytest.raises(C.LaunchError, match="could not query"):
        co.probe()
