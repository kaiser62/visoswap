#!/usr/bin/env python3
"""One-click VisoSwap GPU backend on Google Colab (provisioning included).

    colab_launch.py              obtain/reuse a T4 session, set up, start, print the URL
    colab_launch.py --setup      provision (session, deps, repo, models, tunnel config); do not start
    colab_launch.py --status     session + remote processes + public health
    colab_launch.py --restart    restart uvicorn + tunnel inside the SAME session
    colab_launch.py --logs       tail uvicorn / tunnel logs
    colab_launch.py --down       the ONLY way a session is stopped (asks first)
    colab_launch.py --dry-run    show what would happen; nothing mutating runs

Config: ~/.visoswap/colab.env (CF_TUNNEL_TOKEN, VISOSWAP_HOSTNAME, COLAB_SESSION=l4, COLAB_GPU=L4,
VISOSWAP_REF=master, COLAB_BIN). See docs/launchers.md.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vs_cf  # noqa: E402
import vs_preflight as P  # noqa: E402
import vs_remote  # noqa: E402
from vs_common import (REPO_URL, read_relay_line, LaunchError, Reporter, Runner, common_args, confirm, fetch_health,  # noqa: E402
                       can_prompt, load_config, make_reporter, normalize_host, save_url, target_lock, visoswap_home, wait_until)

REMOTE_REPO = "/content/visoswap"
REMOTE_TOKEN = "/content/cf_token"
RETRY_INTERVAL_S = 30
DEFAULT_GPU = "L4"       # L4 burns compute units while it exists (see `colab usage`); T4 is the cheaper option (--gpu T4)


# ----------------------------------------------------------------- pure logic
def parse_sessions(out: str) -> List[Dict[str, str]]:
    """`[t4] gpu-t4-s-usw4b0-... | Hardware: T4 | Shape: Standard | Variant: GPU` -> dicts."""
    res = []
    for line in out.splitlines():
        m = re.match(r"\s*\[([^\]]+)\]\s*(\S+)\s*(.*)", line)
        if not m:
            continue
        hw = re.search(r"Hardware:\s*([^|]+)", m.group(3))
        res.append({"name": m.group(1), "id": m.group(2), "hardware": hw.group(1).strip() if hw else ""})
    return res


def choose_session(sessions: List[Dict[str, str]], wanted: str) -> Dict[str, str]:
    """Returns {'action': 'reuse'|'create'|'abort', 'session': name, 'why': ...}. Never 'create' when one exists."""
    for s in sessions:
        if s["name"] == wanted:
            return {"action": "reuse", "session": wanted}
    if len(sessions) == 1:
        return {"action": "reuse", "session": sessions[0]["name"], "why": f"adopting the only existing session '{sessions[0]['name']}'"}
    if not sessions:
        return {"action": "create", "session": wanted}
    return {"action": "abort", "session": wanted,
            "why": f"{len(sessions)} sessions exist and none is named '{wanted}'"}


def plan_steps(probe: dict, public_healthy: bool, restart: bool, ref_given: bool, setup_only: bool) -> List[str]:
    """Which remote steps to run. Every step re-checks, so extra steps are cheap but this keeps launches fast."""
    if not (restart or ref_given or setup_only):
        if public_healthy:
            return []
        if probe.get("health") and probe.get("tunnel"):   # up on the VM: do not checkout/kill anything, just wait for the public URL
            return []
    steps = []
    if probe.get("deps_missing") or "onnxruntime" in probe.get("deps_missing", []):
        steps.append("deps")
    steps.append("repo")
    steps.append("models")
    if not setup_only:
        steps.append("start")
    return steps


def exec_cmd(exe: str, session: str, script: Path, timeout: int) -> list:
    return [exe, "exec", "-s", session, "-f", str(script), "--timeout", str(timeout)]


def new_cmd(exe: str, session: str, gpu: str) -> list:
    return [exe, "new", "-s", session, "--gpu", gpu]


def markers(out: str, step: str):
    """-> (ok, fail_reason)"""
    if f"VS_OK {step}" in out:
        return True, ""
    m = re.search(r"VS_FAIL \S+ (.*)", out)
    return False, (m.group(1) if m else "no completion marker (timeout or crash); see output above")


def auth_problem(out: str) -> bool:
    low = out.lower()
    return any(w in low for w in ("not logged in", "oauth", "credential", "authentication", "login required", "invalid_grant",
                                  "reauth", "authorization code", "visit this url", "eoferror", "aborted!"))


# ----------------------------------------------------------------- impure
class Colab:
    def __init__(self, cfg, rep: Reporter, run: Runner, sleep=time.sleep, clock=time.time, health=fetch_health):
        self.cfg, self.rep, self.run, self.sleep, self.clock, self.health = cfg, rep, run, sleep, clock, health
        self.exe = cfg.get("COLAB_BIN") or shutil.which("colab") or str(Path.home() / ".local/bin/colab")
        self.gpu = (cfg.get("COLAB_GPU") or DEFAULT_GPU).upper()
        self.session = cfg.get("COLAB_SESSION") or self.gpu.lower()
        self.ref = cfg.get("VISOSWAP_REF") or "master"

    def c(self, *args, mutating=False, timeout=120):
        r = self.run.run([self.exe, *args], mutating=mutating, timeout=timeout)
        if r.rc == 127:
            raise LaunchError("colab CLI not found", "uv tool install google-colab-cli  (or set COLAB_BIN), then run `colab usage` once to log in")
        if r.rc != 0 and auth_problem(r.out):
            raise LaunchError("Colab CLI is not logged in", "run in a terminal: " + P.COLAB_LOGIN)
        return r

    def sessions(self) -> List[Dict[str, str]]:
        return parse_sessions(self.c("sessions").out)

    def ensure_session(self, max_wait: int) -> str:
        sess = self.sessions()
        d = choose_session(sess, self.session)
        if d["action"] == "abort":
            raise LaunchError(d["why"], "set COLAB_SESSION in ~/.visoswap/colab.env to the session to use; the launcher never creates a second one")
        if d["action"] == "reuse":
            self.session = d["session"]
            self.rep.log(d.get("why") or f"reusing session '{self.session}'")
            hw = next((s["hardware"] for s in sess if s["name"] == self.session), "")
            if hw and self.gpu.lower() not in hw.lower():
                self.rep.log(f"note: the existing session has {hw}, not {self.gpu}; reusing it as is (it is never replaced; --down first to change GPU)")
            return self.session
        self.rep.step(f"no session: requesting a {self.gpu} (retrying every {RETRY_INTERVAL_S}s for up to {max_wait // 60} min; GPU capacity is often 'Service Unavailable')")
        if self.run.dry_run:
            self.rep.log("[dry-run] would run: " + " ".join(new_cmd(self.exe, self.session, self.gpu)))
            return None
        u = P.parse_usage(self.c("usage").out) or {}
        if u:
            rate = f", current usage rate {u['rate']:.2f}/hr" if u.get("rate") is not None else ""
            self.rep.log(f"Colab compute units: {u['balance']:.2f}{rate}. A {self.gpu} session burns units for as long as it exists (see `colab usage`); --down ends it.")
            if u["balance"] <= 0 and self.gpu != "T4":
                raise LaunchError(f"no compute units left: Colab will not assign a {self.gpu} without them",
                                  "use the free-tier GPU instead: --gpu T4 (availability is not guaranteed), or buy compute units")
        t0, n = self.clock(), 0
        while True:
            n += 1
            # a failed-looking `new` may have succeeded: always re-check first
            if choose_session(self.sessions(), self.session)["action"] != "create":
                self.rep.log(f"session available after {n - 1} attempt(s)")
                return self.session
            r = self.c("new", "-s", self.session, "--gpu", self.gpu, mutating=True, timeout=300)
            why = re.search(r"Service Unavailable|Backend rejected[^\n]*|quota[^.\n]*|[A-Za-z]*Error[^|\n]*", r.out)
            if re.search(r"rejected accelerator|quota or entitlement", r.out, re.I) and choose_session(self.sessions(), self.session)["action"] == "create":
                # a permanent refusal for this account/GPU, not a capacity blip: retrying for 30 minutes cannot help
                raise LaunchError(f"Colab refused a {self.gpu} for this account: " + (why.group(0)[:160] if why else "quota or entitlement"),
                                  "try --gpu T4 (free tier) or check your compute units / Colab plan")
            if choose_session(self.sessions(), self.session)["action"] != "create":
                self.rep.log(f"attempt {n}: {self.gpu} assigned")
                return self.session
            self.rep.log(f"attempt {n}: {why.group(0) if why else (r.out.splitlines() or ['no output'])[-1][:80]}")
            if self.clock() - t0 + RETRY_INTERVAL_S > max_wait:
                raise LaunchError(f"no {self.gpu} became available within {max_wait // 60} min",
                                  "retry later, raise --max-wait, or try the other GPU (--gpu T4 / --gpu L4); capacity is often short at peak hours")
            self.sleep(RETRY_INTERVAL_S)

    def remote(self, name: str, script: str, timeout: int, **cfg) -> str:
        base = dict(repo_dir=REMOTE_REPO, token_file=REMOTE_TOKEN, ref=self.ref, repo_url=REPO_URL, subset="default",
                    restart=False, profile=None, concurrency=None, wait=240, which=["uvicorn", "tunnel"], lines=60)
        base.update(cfg)
        d = Path(tempfile.mkdtemp(prefix="visoswap-colab-"))
        f = d / f"{name}.py"
        try:
            f.write_text(vs_remote.render(script, **base))
            os.chmod(f, 0o600)
            r = self.c(*exec_cmd("", self.session, f, timeout)[1:], mutating=name not in ("probe", "logs"), timeout=timeout + 60)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        if r.skipped:
            return ""
        for line in r.out.splitlines()[-25:]:
            if name != "probe" and line.strip():
                self.rep.log(line[:200])
        return r.out

    def probe(self) -> dict:
        out = self.remote("probe", vs_remote.PROBE, 120)
        m = re.search(r"VS_PROBE (\{.*\})", out)
        if not m:
            raise LaunchError("could not query the Colab session: " + (out[-200:] or "no output"),
                              "the runtime may be disconnected; run `colab status -s %s` and consider --restart" % self.session)
        return json.loads(m.group(1))

    def step(self, name: str, script: str, timeout: int, **cfg):
        out = self.remote(name, script, timeout, **cfg)
        if self.run.dry_run:
            return out
        ok, why = markers(out, name)
        if not ok:
            raise LaunchError(f"remote step '{name}' failed: {why}", f"see `colab_launch.py --logs`, then re-run (every step is idempotent)")
        return out

    def models(self, limit: int = 3300):
        """Model download runs as a detached remote job; poll it with short execs until it reports OK/FAIL."""
        t0 = self.clock()
        while True:
            out = self.remote("models", vs_remote.MODELS, 400, poll=200)
            if self.run.dry_run or "VS_OK models" in out:
                return
            if "VS_PENDING models" not in out:
                ok, why = markers(out, "models")
                raise LaunchError(f"remote step 'models' failed: {why}", "see /content/models.log via --logs; re-run (the download resumes)")
            if self.clock() - t0 > limit:
                raise LaunchError("model download is still running after %d min" % (limit // 60), "re-run the launcher; the download continues on the VM")
            self.rep.log("models: still downloading")

    def stop_session(self):
        """The ONLY place that builds a `stop` command. Reached only via --down."""
        return self.c("stop", "-s", self.session, mutating=True)

    def upload_token(self, token: str):
        d = Path(tempfile.mkdtemp(prefix="visoswap-tok-"))
        f = d / "cf_token"
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(token)
            r = self.c("upload", str(f), REMOTE_TOKEN, "-s", self.session, mutating=True, timeout=120)
            if r.rc != 0 and not r.skipped:
                raise LaunchError("could not upload the tunnel token: " + r.out[:150], "retry")
        finally:
            shutil.rmtree(d, ignore_errors=True)


def saved_url() -> Optional[str]:
    f = visoswap_home() / "colab_url"
    return f.read_text().strip() if f.is_file() else None


def resolve_url(cfg, probe: dict) -> Optional[str]:
    """One resolver for launch / restart / status: env hostname, else the VM's existing token (older manual
    setup) with the documented default hostname, else the last saved URL."""
    u = public_url(cfg)
    if u:
        return u
    if probe.get("token_file"):
        return "https://" + vs_cf.DEFAULT_HOSTNAMES["colab"]
    return saved_url()


def public_url(cfg) -> Optional[str]:
    h = normalize_host(cfg.get("VISOSWAP_HOSTNAME", ""))
    return f"https://{h}" if h else None


def settings_overrides(args, cfg) -> dict:
    """Explicit settings only (flags beat colab.env); None = keep what the VM already runs with."""
    conc = args.concurrency if args.concurrency is not None else (cfg.get("GENERATION_CONCURRENCY") or None)
    if isinstance(conc, str) and conc.lower() != "auto":
        conc = int(conc) if conc.isdigit() else None
    prof = args.profile if args.profile is not None else {"1": True, "0": False}.get(cfg.get("VISOSWAP_PROFILE", ""))
    return {"concurrency": conc, "profile": prof}


def preflight(args, cfg, rep, run, co: Colab):
    fixers = {"colab.login": lambda: run.interactive([co.exe, "usage"])}
    return P.resolve(lambda: P.colab_checks({**cfg, "COLAB_BIN": co.exe}, run), fixers, can_prompt(args), rep, args.dry_run)


def check(args, cfg, rep, run, co: Colab) -> dict:
    """Read-only (runner is in readonly mode): login state, units, session, token-on-VM."""
    checks = P.colab_checks({**cfg, "COLAB_BIN": co.exe}, run)
    vm = None
    if all(c.good for c in checks if c.id in ("colab.cli", "colab.login")) and any(c.id == "colab.login" for c in checks):
        d = choose_session(co.sessions(), co.session)
        if d["action"] == "reuse":
            co.session = d["session"]
            probe = co.probe()
            vm = bool(probe.get("token_file"))
            checks.append(P.Check("colab.session", "Colab session", "ok", f"'{co.session}' ({probe.get('gpu') or 'no GPU'})", blocking=False))
        else:
            checks.append(P.Check("colab.session", "Colab session", "missing", "no session yet; launch requests a GPU (retries, up to --max-wait)", blocking=False))
    checks.append(P.cloudflare_check("colab", cfg, vm))
    return P.summarize(checks)


GOOGLE_LOGIN_HOSTS = ("accounts.google.com",)


def safe_login_url(text: str) -> str:
    """First https URL in the CLI output, only if it points at Google's sign-in host (it is rendered as a link in the UI)."""
    from urllib.parse import urlparse
    for m in re.finditer(r"https://[^\s\"'<>]+", text):
        u = urlparse(m.group(0))
        if u.scheme == "https" and u.hostname in GOOGLE_LOGIN_HOSTS:
            return m.group(0)
    return ""


def relay_login(args, cfg, rep, run, co: Colab, url_wait: float = 60.0) -> dict:
    """Headless Colab login for the web UI: run `colab usage` with piped stdio, hand the Google URL to the driver,
    feed the pasted code to the CLI's stdin. If `usage` succeeds without asking, no prompt is issued."""
    import subprocess, threading
    if run.dry_run:
        rep.log("[dry-run] would run the Colab OAuth copy-paste login via the web UI")
        return {"ok": True, "summary": "dry run"}
    buf: List[str] = []
    try:
        p = subprocess.Popen([co.exe, "usage"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except OSError:
        raise LaunchError("colab CLI not found", "uv tool install google-colab-cli  (or set COLAB_BIN)")

    def pump():
        for ln in p.stdout:
            buf.append(ln)
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    try:
        url, t0 = "", time.time()
        while time.time() - t0 < url_wait and p.poll() is None and not url:
            url = safe_login_url("".join(buf))
            if not url:
                time.sleep(0.2)
        if p.poll() is None:
            if not url:
                raise LaunchError("the Colab CLI did not show a Google sign-in URL", "run in a terminal: " + P.COLAB_LOGIN)
            code = read_relay_line(rep, "code", "Open the link, sign in with Google, copy the code it shows and paste it here", url=url)
            try:
                p.stdin.write(code + "\n")
                p.stdin.flush()
            except OSError:
                pass
        with contextlib.suppress(OSError):
            p.stdin.close()
        try:
            p.wait(120)
        except subprocess.TimeoutExpired:
            p.kill()
        th.join(5)
    finally:
        if p.poll() is None:
            p.kill()
    st = [c for c in P.colab_checks({**cfg, "COLAB_BIN": co.exe}, run) if c.id == "colab.login"]
    ok = bool(st and st[0].state == "ok")
    return {"ok": ok, "summary": "Colab login valid" if ok else "Colab login not completed",
            "error": "" if ok else "the code was not accepted, or the login could not be saved (no OS keyring: use the native web UI, run.sh native)",
            "next": "" if ok else P.COLAB_LOGIN}


def login(args, cfg, rep, run, co: Colab) -> dict:
    if getattr(args, "relay", False):
        return relay_login(args, cfg, rep, run, co)
    if not can_prompt(args):
        raise LaunchError("the Colab login needs a terminal (browser sign-in plus a pasted code; the token goes to the OS keychain)",
                          "run in a terminal: " + P.COLAB_LOGIN)
    run.interactive([co.exe, "usage"])
    st = [c for c in P.colab_checks({**cfg, "COLAB_BIN": co.exe}, run) if c.id == "colab.login"]
    ok = bool(st and st[0].state == "ok")
    return {"ok": ok, "summary": "Colab login valid" if ok else "Colab login not completed", "next": "" if ok else P.COLAB_LOGIN}


def cf_api_setup(args, cfg, rep, run, fetch=vs_cf.default_fetch, home=None) -> dict:
    """Create the tunnel + DNS from a Cloudflare API token read from stdin. The API token lives in memory
    only; just the resulting tunnel token and hostname are stored (mode 600)."""
    tok = sys.stdin.readline().strip()
    if len(tok) < 20 or any(ch.isspace() for ch in tok):
        raise LaunchError("that does not look like a Cloudflare API token", "dash.cloudflare.com > My Profile > API Tokens (Account: Tunnel edit, Zone: DNS edit)")
    rep.redact.add(tok)
    out = vs_cf.ensure_tunnel("colab", {**cfg, "CF_API_TOKEN": tok}, rep, run, "named", fetch=fetch, home=home, interactive=False)
    return {"ok": True, "summary": "tunnel and DNS ready for " + str(out.get("VISOSWAP_HOSTNAME", "?")), "tunnel": out.get("TUNNEL_MODE")}


def store_cf_token(args, cfg, rep) -> dict:
    tok = (vs_cf.prompt_secret("Cloudflare tunnel token (hidden): ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
    host = normalize_host(cfg.get("VISOSWAP_HOSTNAME") or vs_cf.DEFAULT_HOSTNAMES["colab"])
    vs_cf.store_token("colab", tok, host, rep, cfg=cfg)
    return {"ok": True, "summary": f"token stored for {host}"}


def launch(args, cfg, rep, run, co: Colab, fetch=vs_cf.default_fetch, home=None) -> dict:
    rep.step("Preflight: Colab CLI, login, compute units")
    preflight(args, cfg, rep, run, co)
    rep.step("Provision: session")
    if co.ensure_session(args.max_wait) is None:
        return {"ok": True, "state": "dry-run", "summary": "dry run: no session exists; the real run would request a GPU session, then set up and start the backend"}
    probe = co.probe()
    rep.log(f"GPU: {probe.get('gpu') or 'NONE'}; repo {probe.get('sha') or 'not cloned'}; models {probe.get('models', 0)}; "
            f"backend {'up' if probe.get('health') else 'down'}")
    if not probe.get("gpu"):
        raise LaunchError("the session has no GPU", "it was created as a CPU runtime: run --down, then launch again")
    rep.step("Provision: Cloudflare tunnel")
    named_ready = bool(cfg.get("CF_TUNNEL_TOKEN") and cfg.get("VISOSWAP_HOSTNAME"))
    if not named_ready and args.tunnel != "quick" and probe.get("token_file"):
        # older manual setup: the VM already holds /content/cf_token; only the hostname is needed locally
        rep.log("tunnel token already on the VM; keeping it")
        cfg = {**cfg, "VISOSWAP_HOSTNAME": cfg.get("VISOSWAP_HOSTNAME") or vs_cf.DEFAULT_HOSTNAMES["colab"], "TUNNEL_MODE": "named"}
    else:
        cfg = vs_cf.ensure_tunnel("colab", cfg, rep, run, args.tunnel, fetch=fetch, home=home, interactive=can_prompt(args) and args.setup)
    quick = cfg.get("TUNNEL_MODE") == "quick"
    url = None if quick else resolve_url(cfg, probe)
    healthy = bool(url and co.health(url))
    knobs = args.concurrency is not None or args.profile is not None
    steps = plan_steps(probe, healthy, args.restart, bool(args.ref) or knobs, args.setup)
    rep.step("plan: " + (", ".join(steps) if steps else "nothing to do (already healthy)"))
    if not steps:
        if not healthy and url and not wait_until(lambda: co.health(url), 180, 10, co.sleep, co.clock,
                                                  lambda w: rep.log(f"[{w:4d}s] backend is up on the VM; waiting for {url}")):
            raise LaunchError("backend and tunnel run on the VM but the public URL is not healthy",
                              "check --logs tunnel; use --restart to restart both processes")
        return finish(args, url, rep)
    if "deps" in steps:
        co.step("deps", vs_remote.DEPS, 1800)
    repo_out = co.step("repo", vs_remote.REPO_STEP, 900)
    co.models()
    if args.setup:
        return {"ok": True, "state": "provisioned", "summary": "Colab provisioning complete (session, deps, repo, models).", "tunnel": cfg.get("TUNNEL_MODE")}
    if not quick and cfg.get("CF_TUNNEL_TOKEN") and (not probe.get("token_file") or args.refresh_token):
        co.upload_token(cfg["CF_TUNNEL_TOKEN"])
    code_changed = "VS_CHANGED 1" in (repo_out or "")
    out = co.step("start", vs_remote.START, 600, restart=bool(args.restart or code_changed or knobs), **settings_overrides(args, cfg))
    if run.dry_run:
        return {"ok": True, "state": "dry-run", "summary": "dry run complete", "url": url}
    if quick:
        m = re.search(r"VS_QUICK_URL (\S+)", out)
        if not m:
            raise LaunchError("quick tunnel started but no trycloudflare URL appeared", "run --logs tunnel")
        url = m.group(1)
    if not wait_until(lambda: co.health(url), 300, 10, co.sleep, co.clock, lambda w: rep.log(f"[{w:4d}s] waiting for {url}")):
        raise LaunchError("backend is up inside Colab but not reachable through the tunnel (Cloudflare 1033/502)",
                          "check the tunnel with --logs tunnel; the token or ingress (http://localhost:8000) may be wrong")
    return finish(args, url, rep)


def finish(args, url, rep) -> dict:
    save_url("colab", url, rep, args.dry_run, not (args.no_clipboard or args.json))
    return {"ok": True, "state": "RUNNING", "healthy": True, "url": url, "mobile_url": url + "/mobile"}


def status(args, cfg, rep, run, co: Colab) -> dict:
    sess = co.sessions()
    d = choose_session(sess, co.session)
    if d["action"] != "reuse":
        return {"ok": True, "state": "NO_SESSION", "healthy": False, "url": public_url(cfg), "configured": bool(public_url(cfg)),
                "summary": "no Colab session; launch to create one"}
    co.session = d["session"]
    probe = co.probe()
    url = resolve_url(cfg, probe)
    healthy = bool(url and co.health(url))
    state = "RUNNING" if probe.get("health") else "SESSION_ONLY"
    return {"ok": True, "state": state, "healthy": healthy, "url": url, "session": co.session, "gpu": probe.get("gpu"),
            "sha": probe.get("sha"), "uvicorn": probe.get("uvicorn"), "tunnel": probe.get("tunnel"),
            "configured": bool(url),
            "summary": f"session {co.session} ({probe.get('gpu')}): backend {'up' if probe.get('health') else 'down'}, public {'healthy' if healthy else 'not answering'}"}


def restart(args, cfg, rep, run, co: Colab) -> dict:
    d = choose_session(co.sessions(), co.session)
    if d["action"] != "reuse":
        raise LaunchError("no Colab session to restart processes in", "run the launcher without --restart to create one")
    co.session = d["session"]
    probe = co.probe()
    url = None if cfg.get("TUNNEL_MODE") == "quick" or args.tunnel == "quick" else resolve_url(cfg, probe)
    out = co.step("start", vs_remote.START, 600, restart=True, **settings_overrides(args, cfg))
    if run.dry_run:
        return {"ok": True, "state": "dry-run", "summary": "dry run: would restart uvicorn and the tunnel inside the same session", "url": url}
    m = re.search(r"VS_QUICK_URL (\S+)", out)
    if m:
        url = m.group(1)
    if not url:
        raise LaunchError("restarted, but no public URL is known", "set VISOSWAP_HOSTNAME in colab.env or use --logs tunnel")
    if not wait_until(lambda: co.health(url), 300, 10, co.sleep, co.clock, lambda w: rep.log(f"[{w:4d}s] waiting for {url}")):
        raise LaunchError("restarted, but the public URL is not healthy (Cloudflare 1033/502)", "run --logs both")
    return finish(args, url, rep)


def down(args, cfg, rep, run, co: Colab) -> dict:
    d = choose_session(co.sessions(), co.session)
    if d["action"] != "reuse":
        return {"ok": True, "state": "NO_SESSION", "summary": "no session to stop"}
    co.session = d["session"]
    if not confirm(f"Stop Colab session '{co.session}'? The backend goes offline and a new T4 may be hard to get.", args.yes, rep):
        return {"ok": True, "state": "RUNNING", "summary": "aborted"}
    co.stop_session()
    if run.dry_run:
        return {"ok": True, "state": "dry-run", "summary": f"dry run: would stop session {co.session}"}
    return {"ok": True, "state": "NO_SESSION", "summary": f"stopped session {co.session}"}


def logs(args, co: Colab) -> dict:
    d = choose_session(co.sessions(), co.session)
    if d["action"] != "reuse":
        raise LaunchError("no Colab session", "launch first")
    co.session = d["session"]
    out = co.remote("logs", vs_remote.LOGS, 120, which=["uvicorn", "tunnel"] if args.logs == "both" else [args.logs], lines=args.lines)
    for line in out.splitlines():
        if not line.startswith("VS_"):
            co.rep.log(line[:300])
    return {"ok": True, "summary": "logs shown above"}


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common_args(ap, "colab")
    ap.add_argument("--ref", help="git ref (branch/tag/sha) to deploy; default master (VISOSWAP_REF)")
    ap.add_argument("--gpu", help="GPU for a NEW session: L4 (default, uses compute units), T4 (cheaper), G4, A100, H100")
    ap.add_argument("--session", help="session name (default: the GPU name in lower case, e.g. l4; COLAB_SESSION)")
    ap.add_argument("--max-wait", type=int, default=1800, help="seconds to keep retrying for a GPU (default 1800)")
    ap.add_argument("--refresh-token", action="store_true", help="re-upload the tunnel token to the VM")
    ap.add_argument("--logs", nargs="?", const="both", choices=("uvicorn", "tunnel", "both"), help="show remote logs")
    ap.add_argument("--lines", type=int, default=60)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config("colab")
    if args.ref:
        cfg["VISOSWAP_REF"] = args.ref
    if args.gpu:
        cfg["COLAB_GPU"] = args.gpu
    if args.session:
        cfg["COLAB_SESSION"] = args.session
    rep = make_reporter(args, cfg)
    run = Runner(rep, dry_run=args.dry_run, readonly=args.check)
    co = Colab(cfg, rep, run)
    try:
        if args.check:
            res = check(args, cfg, rep, run, co)
        elif args.store_cf_token:
            res = store_cf_token(args, cfg, rep)
        elif args.cf_api_token_stdin:
            with (contextlib.nullcontext() if args.dry_run else target_lock("colab", rep)):
                res = cf_api_setup(args, cfg, rep, run)
        elif args.login:
            res = login(args, cfg, rep, run, co)
        elif args.status:
            res = status(args, cfg, rep, run, co)
        elif args.logs:
            res = logs(args, co)
        else:
            with (contextlib.nullcontext() if args.dry_run else target_lock("colab", rep)):
                if args.down:
                    res = down(args, cfg, rep, run, co)
                elif args.restart and not args.setup:
                    res = restart(args, cfg, rep, run, co)
                else:
                    res = launch(args, cfg, rep, run, co)
        ok = res.pop("ok", True)
        rep.result(ok, **res)
        return 0 if ok else 1
    except LaunchError as e:
        rep.result(False, error=str(e), next=e.next_action,
                   **({"checks": [c.as_dict() for c in e.checks]} if hasattr(e, "checks") else {}))
        return 1
    except KeyboardInterrupt:
        rep.result(False, error="interrupted", next="re-run; every step is idempotent")
        return 130


if __name__ == "__main__":
    sys.exit(main())
