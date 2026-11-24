"""Preflight: one result shape for ``--check``, ``--setup``, launch and the web UI.

A :class:`Check` is (id, service, state, detail, fix, runnable, blocking). States:
``ok`` | ``invalid`` (credentials rejected/expired) | ``missing`` (not installed / not configured) |
``unknown`` (could not tell: timeout, network). Only ``invalid``/``missing`` ever start a login, and only
when the interactivity gate (``vs_common.can_prompt``) is true; ``unknown`` never triggers a login.
Nothing here prints a credential: probes use read-only commands and only a bounded excerpt of their output.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from vs_common import LaunchError, Reporter, Runner
import vs_cf

KAGGLE_LOGIN = "kaggle auth login --force"
COLAB_LOGIN = "colab usage    (open the printed URL, sign in, paste the code it shows)"
KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT = "google-colab-cli", "oauth_token"   # from colab_cli/auth.py


@dataclass
class Check:
    id: str
    service: str
    state: str
    detail: str = ""
    fix: str = ""
    runnable: bool = False      # can the launcher run the fix itself (interactive terminal needed)
    blocking: bool = True

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def good(self) -> bool:
        return self.state in ("ok", "unknown") or not self.blocking


class PreflightFailed(LaunchError):
    def __init__(self, checks: List[Check]):
        bad = [c for c in checks if not c.good]
        super().__init__("; ".join(f"{c.service}: {c.detail or c.state}" for c in bad),
                         " | ".join(c.fix for c in bad if c.fix) or "see --check")
        self.checks = checks


def excerpt(s: str, n: int = 160) -> str:
    return re.sub(r"\s+", " ", s).strip()[:n]


# ----------------------------------------------------------------- kaggle
def kaggle_auth_state(rc: int, out: str) -> str:
    low = out.lower()
    if any(m in low for m in ("authentication required", "authentication failed", "invalid credentials", "stale or invalid",
                              "could not find kaggle")) or ("401" in low and "unauthorized" in low):
        return "invalid"
    if rc == 0:
        return "ok"
    if rc in (124, 127):
        return "unknown"
    return "invalid" if ("403" in low or "forbidden" in low) else "unknown"


def kaggle_checks(cfg: Dict[str, str], run: Runner) -> List[Check]:
    exe = cfg.get("KAGGLE_BIN") or shutil.which("kaggle")
    if not exe or not (shutil.which(exe) or os.path.exists(exe)):
        return [Check("kaggle.cli", "Kaggle CLI", "missing", "kaggle CLI not installed", "pip install kaggle")]
    out = [Check("kaggle.cli", "Kaggle CLI", "ok", "installed")]
    r = run.run([exe, "kernels", "list", "--mine", "--page-size", "1"], timeout=90)
    st = kaggle_auth_state(r.rc, r.out)
    out.append(Check("kaggle.auth", "Kaggle login", st,
                     {"ok": "credentials valid", "invalid": "not logged in or credentials expired",
                      "unknown": "could not verify (network/timeout): " + excerpt(r.out, 80)}[st],
                     KAGGLE_LOGIN if st == "invalid" else "", runnable=st == "invalid"))
    if st == "ok":
        q = run.run([exe, "quota"], timeout=60)
        if q.rc == 0 and q.out.strip():
            out.append(Check("kaggle.quota", "Kaggle GPU quota", "ok", excerpt(q.out, 200), blocking=False))
        else:
            out.append(Check("kaggle.quota", "Kaggle GPU quota", "unknown", "quota not reported by the CLI; max 2 GPU kernels per account", blocking=False))
    return out


# ----------------------------------------------------------------- colab
def colab_login_marker(run: Runner) -> Optional[bool]:
    """Does a stored Google login exist? Existence only, never the secret (no -w/-g). None = cannot tell."""
    legacy = Path.home() / ".config" / "colab-cli" / "token.json"
    if legacy.is_file():
        return True
    if platform.system() == "Darwin":
        r = run.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT], timeout=15)
        return r.rc == 0
    return None


def parse_usage(out: str) -> Optional[dict]:
    m = re.search(r"Current balance:\s*([\d.]+)", out)
    if not m:
        return None
    rate = re.search(r"Usage rate:\s*([\d.]+)", out)
    return {"balance": float(m.group(1)), "rate": float(rate.group(1)) if rate else None}


def colab_login_state(rc: int, out: str) -> str:
    """colab usage output -> ok | invalid | unknown. With no stdin the CLI's login flow dies at input()."""
    if parse_usage(out) is not None:
        return "ok"
    low = out.lower()
    if any(m in low for m in ("authorization code", "visit this url", "eoferror", "aborted", "invalid_grant", "keyring")):
        return "invalid"
    return "unknown"


def colab_checks(cfg: Dict[str, str], run: Runner) -> List[Check]:
    exe = cfg.get("COLAB_BIN") or shutil.which("colab") or str(Path.home() / ".local/bin/colab")
    if not (shutil.which(exe) or os.path.exists(exe)):
        return [Check("colab.cli", "Colab CLI", "missing", "colab CLI not installed", "uv tool install google-colab-cli")]
    out = [Check("colab.cli", "Colab CLI", "ok", "installed")]
    marker = colab_login_marker(run)
    if marker is False:      # no stored login: do not even run the CLI (it would start the login flow)
        out.append(Check("colab.login", "Colab login", "invalid", "no Google login stored", COLAB_LOGIN, runnable=True))
        return out
    r = run.run([exe, "usage"], timeout=90)
    st = colab_login_state(r.rc, r.out)
    out.append(Check("colab.login", "Colab login", st,
                     {"ok": "Google login valid", "invalid": "Google login expired or revoked",
                      "unknown": "could not verify: " + excerpt(r.out, 80)}[st],
                     COLAB_LOGIN if st == "invalid" else "", runnable=st == "invalid"))
    if st == "ok":
        u = parse_usage(r.out) or {}
        bal = u.get("balance")
        note = f"{bal:.2f} compute units" if bal is not None else "unknown"
        if bal == 0:
            note += " (free tier: a T4 is not guaranteed)"
        out.append(Check("colab.units", "Colab compute units", "ok", note, blocking=False))
    return out


# ----------------------------------------------------------------- cloudflare
def cloudflare_check(target: str, cfg: Dict[str, str], vm_token: Optional[bool] = None, home: Optional[Path] = None,
                     cloudflared: Optional[str] = None) -> Check:
    """Never reads or returns the token value; only whether one is stored."""
    if cfg.get("CF_TUNNEL_TOKEN") and cfg.get("VISOSWAP_HOSTNAME"):
        return Check("cloudflare", "Cloudflare tunnel", "ok", f"token stored for {cfg['VISOSWAP_HOSTNAME']}", blocking=False)
    if vm_token:
        return Check("cloudflare", "Cloudflare tunnel", "ok", "tunnel token already on the Colab VM", blocking=False)
    can_auto = bool(cfg.get("CF_API_TOKEN") or (vs_cf.has_cert(home) and (cloudflared or vs_cf.cloudflared_bin(cfg))))
    if can_auto:
        return Check("cloudflare", "Cloudflare tunnel", "missing", "no token stored yet; the launcher will create the tunnel and DNS (API token or cloudflared login found)",
                     f"{target}-launch --setup", runnable=True, blocking=False)
    return Check("cloudflare", "Cloudflare tunnel", "missing", "no token, API token or cloudflared login: a random quick tunnel would be used",
                 f"{target}-launch --setup  (paste a tunnel token once)", runnable=True, blocking=False)


# ----------------------------------------------------------------- resolve (fix interactively or fail clearly)
def resolve(checks_fn: Callable[[], List[Check]], fixers: Dict[str, Callable[[], int]], interactive: bool,
            rep: Reporter, dry_run: bool = False) -> List[Check]:
    checks = checks_fn()
    for c in list(checks):
        if c.good or c.id not in fixers:
            continue
        if interactive:
            rep.step(f"{c.service}: {c.detail}; starting the login flow")
            fixers[c.id]()
            checks = checks_fn()
            break
    for c in checks:
        rep.log(f"{c.service}: {c.state}" + (f" ({c.detail})" if c.detail else ""))
    bad = [c for c in checks if not c.good]
    if bad:
        if dry_run:
            for c in bad:
                rep.log(f"[dry-run] would need: {c.fix or c.detail}")
        else:
            raise PreflightFailed(checks)
    return checks


def summarize(checks: List[Check]) -> dict:
    ok = all(c.good for c in checks)
    return {"ok": ok, "checks": [c.as_dict() for c in checks],
            "summary": "ready" if ok else "action needed: " + " | ".join(c.fix for c in checks if not c.good and c.fix)}
