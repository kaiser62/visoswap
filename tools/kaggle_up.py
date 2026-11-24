#!/usr/bin/env python3
"""One-click VisoSwap GPU backend on Kaggle.

    python tools/kaggle_up.py            # start (or reuse) the backend, print its URL
    python tools/kaggle_up.py --restart  # force a fresh kernel
    python tools/kaggle_up.py --down     # delete the kernel (frees the GPU session slot)

With a Cloudflare *named* tunnel the URL is static (https://<hostname>) and is the
same on every run: put it in the web UI, the mobile UI and the native client once.
Without one, a throwaway trycloudflare.com URL is used and has to be read from the
kernel's Logs tab (Kaggle offers no API for live logs).

Config (environment, or ``~/.visoswap/kaggle.env`` with KEY=VALUE lines):
    CF_TUNNEL_TOKEN   token of a Cloudflare remote-managed tunnel (Zero Trust >
                      Networks > Tunnels > your tunnel > Install > the token after
                      ``--token``). Its public hostname must point to http://localhost:8000.
    VISOSWAP_HOSTNAME the public hostname of that tunnel, e.g. swap.example.com
    KAGGLE_KERNEL     kernel slug, default <your-kaggle-user>/visoswap-studio
    KAGGLE_BIN        path of the kaggle CLI, default the one on PATH

The token is written only into a private temp copy of the notebook that is pushed to
your private Kaggle kernel; it never enters the repository.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = ROOT / "visoswap_kaggle.ipynb"
CONF = Path.home() / ".visoswap"
QUEUE_RETRY_S = 300  # re-pushing a kernel that sat queued this long has unstuck it before
MAX_WAIT_S = 3600


def load_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    env_file = CONF / "kaggle.env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip("'\"")
    for k in ("CF_TUNNEL_TOKEN", "VISOSWAP_HOSTNAME", "KAGGLE_KERNEL", "KAGGLE_BIN"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def kaggle(cfg: dict[str, str], *args: str, cwd: Path | None = None) -> str:
    exe = cfg.get("KAGGLE_BIN") or shutil.which("kaggle")
    if not exe:
        sys.exit("kaggle CLI not found. pip install kaggle, then `kaggle auth login`.")
    r = subprocess.run([exe, *args], capture_output=True, text=True, cwd=cwd)
    out = (r.stdout + r.stderr).strip()
    if "Authentication required" in out or "was denied" in out and "kernels" in out:
        sys.exit("Kaggle CLI is not authenticated. Run: kaggle auth login --force")
    return out


def kernel_slug(cfg: dict[str, str]) -> str:
    if cfg.get("KAGGLE_KERNEL"):
        return cfg["KAGGLE_KERNEL"]
    cred = Path.home() / ".kaggle" / "kaggle.json"
    user = None
    for f in (cred, Path.home() / ".kaggle" / "credentials.json"):
        if f.is_file():
            try:
                user = json.loads(f.read_text()).get("username")
            except ValueError:
                pass
            if user:
                break
    user = user or os.environ.get("KAGGLE_USERNAME")
    if not user:
        sys.exit("Set KAGGLE_KERNEL=<kaggle-username>/visoswap-studio (could not read your username).")
    return f"{user}/visoswap-studio"


def status(cfg: dict[str, str], slug: str) -> str:
    out = kaggle(cfg, "kernels", "status", slug)
    for s in ("QUEUED", "RUNNING", "COMPLETE", "ERROR", "CANCEL"):
        if f"KernelWorkerStatus.{s}" in out:
            return s
    return "UNKNOWN" if "not found" not in out.lower() and "404" not in out else "MISSING"


def build_bundle(cfg: dict[str, str], slug: str, concurrency: int | None = None, profile: bool = False) -> Path:
    """Private temp copy of the notebook with the tunnel token filled in."""
    nb = json.loads(NOTEBOOK.read_text())
    token, host = cfg.get("CF_TUNNEL_TOKEN", ""), cfg.get("VISOSWAP_HOSTNAME", "")
    if token and not host:
        sys.exit("CF_TUNNEL_TOKEN is set but VISOSWAP_HOSTNAME is not.")
    for cell in nb["cells"]:
        src = "".join(cell["source"])
        if "CF_TUNNEL_TOKEN = ''" in src:
            src = src.replace("CF_TUNNEL_TOKEN = ''", f"CF_TUNNEL_TOKEN = {token!r}")
            src = src.replace("CF_HOSTNAME = ''", f"CF_HOSTNAME = {host!r}")
            lines = src.split("\n")
            cell["source"] = [l + "\n" for l in lines[:-1]] + [lines[-1]]
        if "GENERATION_CONCURRENCY = '2'" in src:
            if concurrency:
                src = src.replace("GENERATION_CONCURRENCY = '2'", f"GENERATION_CONCURRENCY = '{int(concurrency)}'")
            if profile:
                src = src.replace("VISOSWAP_PROFILE = '0'", "VISOSWAP_PROFILE = '1'")
            lines = src.split("\n")
            cell["source"] = [l + "\n" for l in lines[:-1]] + [lines[-1]]
    d = Path(tempfile.mkdtemp(prefix="visoswap-kaggle-"))
    (d / NOTEBOOK.name).write_text(json.dumps(nb, indent=1, ensure_ascii=False))
    (d / "kernel-metadata.json").write_text(json.dumps({
        "id": slug, "title": slug.split("/")[1].replace("-", " "),
        "code_file": NOTEBOOK.name, "language": "python", "kernel_type": "notebook",
        "is_private": "true", "enable_gpu": "true", "enable_tpu": "false",
        "enable_internet": "true", "dataset_sources": [], "competition_sources": [],
        "kernel_sources": [], "model_sources": [],
    }))
    os.chmod(d, 0o700)
    return d


def push(cfg: dict[str, str], bundle: Path) -> None:
    out = kaggle(cfg, "kernels", "push", "-p", str(bundle), "--accelerator", "NvidiaTeslaT4")
    print("  push:", out.splitlines()[-1] if out else "(no output)")
    if "successfully pushed" not in out:
        sys.exit(f"push failed:\n{out}")


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=10) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--restart", action="store_true", help="delete and relaunch even if running")
    ap.add_argument("--concurrency", type=int, help="generation workers on the GPU (default 2)")
    ap.add_argument("--profile", action="store_true", help="log per-stage ms every 50 frames (VISOSWAP_PROFILE=1)")
    ap.add_argument("--down", action="store_true", help="delete the kernel and exit")
    args = ap.parse_args()

    cfg = load_config()
    slug = kernel_slug(cfg)
    host = cfg.get("VISOSWAP_HOSTNAME", "").replace("https://", "").strip("/")
    static = bool(cfg.get("CF_TUNNEL_TOKEN") and host)
    url = f"https://{host}" if static else None

    if args.down:
        print(kaggle(cfg, "kernels", "delete", slug, "-y"))
        return

    st = status(cfg, slug)
    print(f"kernel {slug}: {st}")
    if st == "RUNNING" and not (args.restart or args.concurrency or args.profile):
        if url and healthy(url):
            finish(url)
            return
        print("  running but backend not answering yet; waiting")
    else:
        if st != "MISSING":
            print("  deleting old kernel (frees the 2-session GPU limit)")
            kaggle(cfg, "kernels", "delete", slug, "-y")
            time.sleep(3)
        bundle = build_bundle(cfg, slug, args.concurrency, args.profile)
        try:
            push(cfg, bundle)
            pushed_at = time.time()
            t0 = pushed_at
            while True:
                st = status(cfg, slug)
                waited = int(time.time() - t0)
                print(f"  [{waited:4d}s] {st}", flush=True)
                if st in ("COMPLETE", "ERROR", "CANCEL"):
                    sys.exit(f"kernel ended with {st}; see https://www.kaggle.com/code/{slug}/log")
                if st == "RUNNING":
                    break
                if time.time() - t0 > MAX_WAIT_S:
                    sys.exit("gave up waiting for a GPU")
                if time.time() - pushed_at > QUEUE_RETRY_S:
                    print("  queued too long; re-pushing")
                    push(cfg, bundle)
                    pushed_at = time.time()
                time.sleep(20)
        finally:
            shutil.rmtree(bundle, ignore_errors=True)  # holds the tunnel token

    if not url:
        print(f"\nKernel is running. No static tunnel configured, so open\n"
              f"  https://www.kaggle.com/code/{slug}/log\n"
              f"and copy the https://*.trycloudflare.com URL (setup takes ~5 min).")
        return
    print("  waiting for the backend (setup takes ~5 min)")
    t0 = time.time()
    while not healthy(url):
        if time.time() - t0 > 1200:
            sys.exit(f"backend did not come up; see https://www.kaggle.com/code/{slug}/log")
        time.sleep(15)
    finish(url)


def finish(url: str) -> None:
    CONF.mkdir(exist_ok=True)
    (CONF / "backend_url").write_text(url + "\n")
    if shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=url.encode())
    print(f"\nBackend ready:\n  web / desktop : {url}\n  mobile        : {url}/mobile\n"
          f"  saved to {CONF / 'backend_url'} (and copied to the clipboard)")


if __name__ == "__main__":
    main()
