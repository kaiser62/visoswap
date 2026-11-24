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
    with pytest.raises(C.LaunchError, match="no T4 became available"):
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
