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
