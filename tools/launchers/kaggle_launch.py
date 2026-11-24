#!/usr/bin/env python3
"""One-click VisoSwap GPU backend on Kaggle (provisioning included).

    kaggle_launch.py              launch, or reuse the running kernel, print the URL
    kaggle_launch.py --setup      provision only (Cloudflare tunnel, Kaggle auth, model dataset)
    kaggle_launch.py --status     kernel state + backend health
    kaggle_launch.py --restart    delete + relaunch the kernel
    kaggle_launch.py --down       delete the kernel (asks first)
    kaggle_launch.py --dry-run    show what would happen; no mutating command runs

Config: ~/.visoswap/kaggle.env (CF_TUNNEL_TOKEN, VISOSWAP_HOSTNAME, KAGGLE_KERNEL, KAGGLE_DATASET,
KAGGLE_BIN). See docs/launchers.md.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vs_cf  # noqa: E402
import vs_preflight as P  # noqa: E402
from vs_common import (REPO_ROOT, LaunchError, Reporter, Runner, common_args, confirm, fetch_health,  # noqa: E402
                       can_prompt, load_config, make_reporter, normalize_host, save_url, target_lock, visoswap_home, wait_until)

NOTEBOOK = REPO_ROOT / "visoswap_kaggle.ipynb"
QUEUE_RETRY_S = 300       # a kernel queued this long has been unstuck by delete + re-push before
MAX_GPU_WAIT_S = 3600
MAX_BOOT_WAIT_S = 1200
#: friendly name -> `kaggle kernels push --accelerator` value ('NvidiaTeslaT4' = GPU T4 x2 per the CLI help;
#: P100 is retired). Anything else (e.g. a TPU name) is passed through verbatim.
ACCELERATORS = {"t4": "NvidiaTeslaT4", "t4x2": "NvidiaTeslaT4"}


# ----------------------------------------------------------------- pure logic
def classify_status(out: str) -> str:
    """Map `kaggle kernels status` output to a state. 'AUTH' = credentials problem,
    'MISSING' = kernel does not exist (Kaggle says 'was denied' for that), else 'UNKNOWN'."""
    low = out.lower()
    if (any(m in low for m in ("authentication required", "authentication failed", "invalid credentials", "stale or invalid",
                               "could not find kaggle"))
            or ("401" in low and "unauthorized" in low)):
        return "AUTH"
    for s in ("QUEUED", "RUNNING", "COMPLETE", "ERROR", "CANCEL"):
        if f"KernelWorkerStatus.{s}" in out or f"status \"{s.lower()}" in low:
            return s
    if "was denied" in low or "cannot access kernel" in low or "not found" in low or "404" in low:
        return "MISSING"
    return "UNKNOWN"


def decide(state: str, healthy: bool, restart: bool, knobs: bool) -> str:
    """-> reuse | wait_health | wait_gpu | launch | relaunch | knobs_need_restart | abort_auth | abort_unknown"""
    if state == "AUTH":
        return "abort_auth"
    if state == "UNKNOWN":
        return "abort_unknown"
    if state in ("RUNNING", "QUEUED"):
        if restart:
            return "relaunch"
        if knobs:
            return "knobs_need_restart"
        if state == "QUEUED":
            return "wait_gpu"
        return "reuse" if healthy else "wait_health"
    return "launch"  # MISSING or finished (COMPLETE/ERROR/CANCEL): a push makes a new version


def inject_notebook(nb: dict, token: str, host: str, concurrency: Optional[int] = None, profile: bool = False,
                    max_minutes: Optional[int] = None) -> dict:
    """Fill the notebook knobs in a copy (never in the repo file)."""
    if token and not host:
        raise LaunchError("CF_TUNNEL_TOKEN is set but VISOSWAP_HOSTNAME is not", "set VISOSWAP_HOSTNAME in kaggle.env")
    nb = json.loads(json.dumps(nb))
    for cell in nb["cells"]:
        src = "".join(cell["source"])
        new = src
        if "CF_TUNNEL_TOKEN = ''" in new:
            new = new.replace("CF_TUNNEL_TOKEN = ''", f"CF_TUNNEL_TOKEN = {token!r}").replace("CF_HOSTNAME = ''", f"CF_HOSTNAME = {host!r}")
        if max_minutes is not None:
            new = new.replace("MAX_RUNTIME_MIN = 90", f"MAX_RUNTIME_MIN = {int(max_minutes)}")
        if "GENERATION_CONCURRENCY = 'auto'" in new:
            if concurrency:
                new = new.replace("GENERATION_CONCURRENCY = 'auto'", f"GENERATION_CONCURRENCY = '{int(concurrency)}'")
            if profile:
                new = new.replace("VISOSWAP_PROFILE = '0'", "VISOSWAP_PROFILE = '1'")
        if new != src:
            lines = new.split("\n")
            cell["source"] = [l + "\n" for l in lines[:-1]] + [lines[-1]]
    return nb


def kernel_metadata(slug: str, dataset: Optional[str], accelerator_gpu: bool = True) -> dict:
    return {"id": slug, "title": slug.split("/")[1].replace("-", " "), "code_file": NOTEBOOK.name,
            "language": "python", "kernel_type": "notebook", "is_private": "true",
            "enable_gpu": "true" if accelerator_gpu else "false", "enable_tpu": "false", "enable_internet": "true",
            "dataset_sources": [dataset] if dataset else [], "competition_sources": [], "kernel_sources": [],
            "model_sources": []}


def push_cmd(exe: str, bundle: Path, accelerator: str) -> list:
    return [exe, "kernels", "push", "-p", str(bundle), "--accelerator", accelerator]


def resolve_accelerator(name: str) -> str:
    return ACCELERATORS.get(name.lower(), name)


def username_from_files(kaggle_dir: Path) -> Optional[str]:
    for f in ("kaggle.json", "credentials.json"):
        p = kaggle_dir / f
        if p.is_file():
            try:
                u = json.loads(p.read_text()).get("username")
            except ValueError:
                u = None
            if u:
                return u
    return None


def kernel_slug(cfg: Dict[str, str]) -> str:
    if cfg.get("KAGGLE_KERNEL"):
        return cfg["KAGGLE_KERNEL"]
    kdir = Path(os.environ.get("KAGGLE_CONFIG_DIR") or Path.home() / ".kaggle")
    user = username_from_files(kdir) or os.environ.get("KAGGLE_USERNAME")
    if not user:
        raise LaunchError("cannot determine your Kaggle username", "set KAGGLE_KERNEL=<user>/visoswap-studio in ~/.visoswap/kaggle.env")
    return f"{user}/visoswap-studio"


# ----------------------------------------------------------------- impure steps
class Kaggle:
    def __init__(self, cfg, rep: Reporter, run: Runner, sleep=time.sleep, clock=time.time, health=fetch_health):
        self.cfg, self.rep, self.run, self.sleep, self.clock, self.health = cfg, rep, run, sleep, clock, health
        self.exe = cfg.get("KAGGLE_BIN") or shutil.which("kaggle")
        self.slug = None

    def k(self, *args, mutating=False, timeout=600):
        if not self.exe:
            raise LaunchError("kaggle CLI not found", "pip install kaggle, then: kaggle auth login --force")
        return self.run.run([self.exe, *args], mutating=mutating, timeout=timeout)

    def check_auth(self):
        r = self.k("kernels", "list", "--mine", "--page-size", "1")
        if classify_status(r.out) == "AUTH" or r.rc == 127:
            raise LaunchError("Kaggle CLI is not authenticated", "run: " + P.KAGGLE_LOGIN)
        if r.rc != 0 and ("401" in r.out or "403" in r.out):
            raise LaunchError("Kaggle rejected the credentials", "run: " + P.KAGGLE_LOGIN)
        return r

    def state(self) -> str:
        r = self.k("kernels", "status", self.slug)
        st = classify_status(r.out)
        if st == "MISSING":  # 'denied' is also what a transient/odd failure looks like: confirm via the list
            lst = self.k("kernels", "list", "--mine", "-s", self.slug.split("/")[1])
            if self.slug in lst.out or lst.rc != 0:
                self.rep.log("kernel looks present in your list but status was denied; refusing to guess")
                return "UNKNOWN"
        return st

    def dataset(self) -> Optional[str]:
        ds = self.cfg.get("KAGGLE_DATASET") or f"{self.slug.split('/')[0]}/visoswap-models"
        if ds.lower() == "none":
            return None
        r = self.k("datasets", "status", ds)
        if r.out.strip().endswith("ready"):
            self.rep.log(f"model dataset {ds}: ready")
            return ds
        self.rep.log(f"model dataset {ds}: not ready ({r.out.strip()[:60]})")
        return None

    def ensure_dataset(self, args, create: bool = True) -> Optional[str]:
        ds = self.dataset()
        if ds:
            return ds
        ds = self.cfg.get("KAGGLE_DATASET") or f"{self.slug.split('/')[0]}/visoswap-models"
        src = Path(args.models_dir or REPO_ROOT / "model_assets_owned")
        files = sorted(src.glob("*.onnx")) if src.is_dir() else []
        if not files:
            self.rep.log(f"no local models in {src}; the kernel will download them itself (~1.3 GB, about 30 s)")
            return None
        if not create:
            self.rep.log(f"model dataset {ds} not ready; run with --setup to create it from {src}. Continuing: the kernel downloads models itself")
            return None
        mb = sum(f.stat().st_size for f in files) // 2**20
        if self.run.dry_run:
            self.rep.log(f"[dry-run] would create dataset {ds} from {len(files)} files ({mb} MB) in {src}")
            return None
        if not confirm(f"Create private Kaggle dataset {ds} from {len(files)} local models ({mb} MB)?", args.yes, self.rep):
            return None
        d = Path(tempfile.mkdtemp(prefix="visoswap-models-"))
        try:
            for f in files:
                try:
                    os.link(f, d / f.name)
                except OSError:
                    shutil.copy2(f, d / f.name)
            (d / "dataset-metadata.json").write_text(json.dumps({"title": "visoswap models", "id": ds, "licenses": [{"name": "other"}]}))
            r = self.k("datasets", "create", "-p", str(d), mutating=True, timeout=7200)
            self.rep.log("dataset create: " + (r.out.splitlines()[-1] if r.out else "(no output)"))
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return self.dataset()

    def push(self, bundle: Path, accel: str):
        r = self.k(*push_cmd("", bundle, accel)[1:], mutating=True)
        if r.skipped:
            return
        if "successfully pushed" not in r.out:
            raise LaunchError("kernel push failed: " + r.out[-300:], "check the Kaggle GPU quota/phone verification at kaggle.com/settings")
        self.rep.log("push: " + r.out.splitlines()[-1])

    def delete(self):
        self.k("kernels", "delete", self.slug, "-y", mutating=True)


def url_for(cfg) -> Optional[str]:
    h = normalize_host(cfg.get("VISOSWAP_HOSTNAME", ""))
    return f"https://{h}" if cfg.get("CF_TUNNEL_TOKEN") and h else None


def preflight(args, cfg, rep, run, kg: Kaggle):
    fixers = {"kaggle.auth": lambda: run.interactive([kg.exe, "auth", "login", "--force"])}
    return P.resolve(lambda: P.kaggle_checks(cfg, run), fixers, can_prompt(args), rep, args.dry_run)


def check(args, cfg, rep, run, kg: Kaggle) -> dict:
    """Read-only: the runner is in readonly mode, nothing is written, no lock is taken."""
    return P.summarize(P.kaggle_checks(cfg, run) + [P.cloudflare_check("kaggle", cfg)])


def login(args, cfg, rep, run, kg: Kaggle) -> dict:
    if not kg.exe:
        raise LaunchError("kaggle CLI not found", "pip install kaggle")
    cmd = [kg.exe, "auth", "login", "--force"]
    if can_prompt(args):
        rc = run.interactive(cmd)
    else:  # web UI / non-tty: browser flow on this host, no stdin
        r = run.run(cmd, mutating=True, timeout=300)
        rc = r.rc
    state = [c for c in P.kaggle_checks(cfg, run) if c.id == "kaggle.auth"]
    ok = bool(state and state[0].state == "ok")
    return {"ok": ok, "summary": "Kaggle login valid" if ok else "Kaggle login not completed", "error": "" if ok else f"login did not complete (exit {rc})",
            "next": "" if ok else P.KAGGLE_LOGIN}


def store_cf_token(args, cfg, rep) -> dict:
    tok = (vs_cf.prompt_secret("Cloudflare tunnel token (hidden): ") if sys.stdin.isatty() else sys.stdin.readline()).strip()
    host = normalize_host(cfg.get("VISOSWAP_HOSTNAME") or vs_cf.DEFAULT_HOSTNAMES["kaggle"])
    vs_cf.store_token("kaggle", tok, host, rep, cfg=cfg)
    return {"ok": True, "summary": f"token stored for {host}"}


def provision(args, cfg, rep, run, kg: Kaggle, fetch=vs_cf.default_fetch, home=None):
    rep.step("Preflight: Kaggle CLI, login, quota")
    preflight(args, cfg, rep, run, kg)
    kg.slug = kernel_slug(cfg)
    rep.log(f"kernel: {kg.slug}")
    rep.step("Provision: Cloudflare tunnel")
    cfg = vs_cf.ensure_tunnel("kaggle", cfg, rep, run, args.tunnel, fetch=fetch, home=home, interactive=can_prompt(args))
    rep.step("Provision: model dataset")
    ds = kg.ensure_dataset(args, create=args.setup)
    return cfg, ds


def launch(args, cfg, rep, run, kg: Kaggle, fetch=vs_cf.default_fetch, home=None) -> dict:
    cfg, ds = provision(args, cfg, rep, run, kg, fetch, home)
    if args.setup:
        return {"ok": True, "summary": "Kaggle provisioning complete.", "state": "provisioned", "tunnel": cfg.get("TUNNEL_MODE")}
    knobs = bool(args.concurrency or args.profile is not None or args.max_minutes is not None)
    url = url_for(cfg) if cfg.get("TUNNEL_MODE") != "quick" else None
    log_url = f"https://www.kaggle.com/code/{kg.slug}/log"
    st = kg.state()
    healthy = bool(url and kg.health(url))
    rep.step(f"kernel {kg.slug}: {st}" + (", backend healthy" if healthy else ""))
    action = decide(st, healthy, args.restart, knobs)
    if action == "abort_auth":
        raise LaunchError("Kaggle CLI is not authenticated", "run: " + P.KAGGLE_LOGIN)
    if action == "abort_unknown":
        raise LaunchError("could not read the kernel status; not touching the kernel", "check `kaggle kernels status %s` by hand, then retry" % kg.slug)
    if action == "knobs_need_restart":
        raise LaunchError("the kernel is already running; --concurrency/--profile/--max-minutes only apply to a new kernel",
                          "add --restart to relaunch with those settings")
    if action == "reuse":
        return finish(args, cfg, url, kg, rep)
    if action in ("launch", "relaunch"):
        if action == "relaunch":
            rep.log("deleting the old kernel (frees a GPU session slot; max 2 per account)")
            kg.delete()
            if not run.dry_run:
                kg.sleep(3)
        bundle = build_bundle(cfg, kg.slug, ds, args)
        try:
            kg.push(bundle, resolve_accelerator(args.accelerator))
            if run.dry_run:
                rep.log("[dry-run] would now wait for RUNNING, then for /api/health")
                return {"ok": True, "summary": "dry run complete", "state": "dry-run", "url": url}
            wait_for_gpu(kg, bundle, resolve_accelerator(args.accelerator), rep, log_url)
        finally:
            shutil.rmtree(bundle, ignore_errors=True)  # holds the tunnel token
    elif action == "wait_gpu":
        wait_for_gpu(kg, None, None, rep, log_url)
    if not url:
        rep.log("quick tunnel: Kaggle has no live-log API, so open the kernel log and copy the trycloudflare.com URL")
        return {"ok": True, "state": "RUNNING", "summary": f"Kernel is running. Open {log_url} and copy the https://*.trycloudflare.com URL (~5 min after start).",
                "log_url": log_url}
    rep.step("waiting for the backend (setup takes ~5 min)")
    if not wait_until(lambda: kg.health(url), MAX_BOOT_WAIT_S, 15, kg.sleep, kg.clock,
                      lambda w: rep.log(f"[{w:4d}s] not healthy yet")):
        raise LaunchError(f"backend did not become healthy within {MAX_BOOT_WAIT_S // 60} min (Cloudflare 1033/502 = tunnel or uvicorn not up yet)",
                          f"read the kernel log: {log_url}")
    return finish(args, cfg, url, kg, rep)


def build_bundle(cfg, slug, dataset, args) -> Path:
    nb = inject_notebook(json.loads(NOTEBOOK.read_text()), cfg.get("CF_TUNNEL_TOKEN", "") if cfg.get("TUNNEL_MODE") != "quick" else "",
                         normalize_host(cfg.get("VISOSWAP_HOSTNAME", "")) if cfg.get("TUNNEL_MODE") != "quick" else "",
                         args.concurrency if isinstance(args.concurrency, int) else None, bool(args.profile), args.max_minutes)
    d = Path(tempfile.mkdtemp(prefix="visoswap-kaggle-"))
    os.chmod(d, 0o700)
    (d / NOTEBOOK.name).write_text(json.dumps(nb, indent=1, ensure_ascii=False))
    (d / "kernel-metadata.json").write_text(json.dumps(kernel_metadata(slug, dataset)))
    return d


def wait_for_gpu(kg: Kaggle, bundle, accel, rep, log_url):
    t0 = pushed = kg.clock()
    while True:
        st = kg.state()
        rep.log(f"[{int(kg.clock() - t0):4d}s] {st}")
        if st in ("COMPLETE", "ERROR", "CANCEL"):
            raise LaunchError(f"kernel ended with {st}", f"read the log: {log_url}")
        if st in ("AUTH", "UNKNOWN"):
            raise LaunchError("lost the kernel status (auth expired?)", "run: " + P.KAGGLE_LOGIN)
        if st == "RUNNING":
            return
        if kg.clock() - t0 > MAX_GPU_WAIT_S:
            raise LaunchError("gave up waiting for a GPU", "Kaggle may be out of GPUs or your weekly quota is used; retry later")
        if bundle and kg.clock() - pushed > QUEUE_RETRY_S:
            rep.log("queued too long; deleting and re-pushing")
            kg.delete()
            kg.sleep(3)
            kg.push(bundle, accel)
            pushed = kg.clock()
        kg.sleep(20)


def finish(args, cfg, url, kg, rep) -> dict:
    if args.dry_run:
        rep.log("[dry-run] no URL file written")
    else:
        save_url("kaggle", url, rep, False, not (args.no_clipboard or args.json), also=("backend_url",))
    return {"ok": True, "state": "RUNNING", "healthy": True, "url": url, "mobile_url": url + "/mobile"}


def status(args, cfg, rep, run, kg: Kaggle) -> dict:
    kg.slug = kernel_slug(cfg)
    kg.check_auth()
    st = kg.state()
    url = url_for(cfg) if cfg.get("TUNNEL_MODE") != "quick" else None
    healthy = bool(url and kg.health(url))
    return {"ok": True, "state": st, "healthy": healthy, "url": url, "kernel": kg.slug,
            "configured": bool(url), "summary": f"kernel {kg.slug}: {st}; backend {'healthy' if healthy else 'not answering'}"}


def down(args, cfg, rep, run, kg: Kaggle) -> dict:
    kg.slug = kernel_slug(cfg)
    kg.check_auth()
    st = kg.state()
    if st in ("MISSING",):
        return {"ok": True, "state": st, "summary": "no kernel to delete"}
    if st in ("UNKNOWN",):
        raise LaunchError("cannot read kernel status; not deleting", "check auth: kaggle auth login --force")
    if not confirm(f"Delete Kaggle kernel {kg.slug} (stops the backend)?", args.yes or args.dry_run, rep):
        return {"ok": True, "state": st, "summary": "aborted"}
    kg.delete()
    if run.dry_run:
        return {"ok": True, "state": "dry-run", "summary": f"dry run: would delete {kg.slug}"}
    return {"ok": True, "state": "MISSING", "summary": f"deleted {kg.slug}"}


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common_args(ap, "kaggle")
    ap.add_argument("--max-minutes", type=int, help="kernel self-terminates after N minutes (default 90, 0 = never)")
    ap.add_argument("--accelerator", default="t4x2", help="t4x2 (default, = NvidiaTeslaT4), or a raw `kaggle kernels push --accelerator` value")
    ap.add_argument("--models-dir", help="local model dir used to create the model dataset (default <repo>/model_assets_owned)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config("kaggle")
    rep = make_reporter(args, cfg)
    run = Runner(rep, dry_run=args.dry_run, readonly=args.check)
    kg = Kaggle(cfg, rep, run)
    try:
        if args.check:
            res = check(args, cfg, rep, run, kg)
        elif args.store_cf_token:
            res = store_cf_token(args, cfg, rep)
        elif args.login:
            with target_lock("kaggle", rep):
                res = login(args, cfg, rep, run, kg)
        elif args.status:
            res = status(args, cfg, rep, run, kg)
        elif args.down:
            if args.dry_run:
                res = down(args, cfg, rep, run, kg)
            else:
                with target_lock("kaggle", rep):
                    res = down(args, cfg, rep, run, kg)
        else:
            if args.dry_run:
                res = launch(args, cfg, rep, run, kg)
            else:
                with target_lock("kaggle", rep):
                    res = launch(args, cfg, rep, run, kg)
        ok = res.pop("ok", True)
        rep.result(ok, **res)
        return 0 if ok else 1
    except LaunchError as e:
        rep.result(False, error=str(e), next=e.next_action)
        return 1
    except KeyboardInterrupt:
        rep.result(False, error="interrupted", next="re-run; the launcher is idempotent")
        return 130


if __name__ == "__main__":
    sys.exit(main())
