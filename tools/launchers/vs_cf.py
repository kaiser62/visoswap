"""Cloudflare provisioning: named tunnel + DNS route + token, per target.

Order of preference: (1) env file already has token+hostname -> nothing to do;
(2) CF_API_TOKEN (+ zone) -> Cloudflare API, remote-managed tunnel; (3) local
``cloudflared`` login (~/.cloudflared/cert.pem); (4) trycloudflare quick tunnel.
Never deletes, never overwrites DNS, never edits an existing tunnel's config.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from vs_common import (DEFAULT_HOSTNAMES, DEFAULT_TUNNEL_NAMES, LaunchError, Reporter, Runner, env_path,
                       load_env_file, merge_env_file, normalize_host)
import shutil

API = "https://api.cloudflare.com/client/v4"
Fetch = Callable[[str, str, Dict[str, str], Optional[dict]], Tuple[int, dict]]


def default_fetch(method: str, url: str, headers: Dict[str, str], body: Optional[dict]) -> Tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def zone_candidates(host: str) -> List[str]:
    """swap.a.b.example.org -> [a.b.example.org, b.example.org, example.org] (longest first)."""
    parts = normalize_host(host).split(".")
    return [".".join(parts[i:]) for i in range(1, len(parts) - 1)]


def cloudflared_bin(cfg: Dict[str, str]) -> Optional[str]:
    return cfg.get("CLOUDFLARED_BIN") or shutil.which("cloudflared")


def has_cert(home: Optional[Path] = None) -> bool:
    return ((home or Path.home()) / ".cloudflared" / "cert.pem").is_file()


def ingress_config(hostname: str, port: int = 8000) -> dict:
    return {"config": {"ingress": [{"hostname": hostname, "service": f"http://localhost:{port}"},
                                   {"service": "http_status:404"}]}}


def provision_api(cfg: Dict[str, str], target: str, name: str, hostname: str, rep: Reporter, dry_run: bool,
                  fetch: Fetch = default_fetch) -> str:
    """Return the tunnel token. Reads always run; writes (create tunnel, DNS) are skipped in dry-run."""
    h = {"Authorization": "Bearer " + cfg["CF_API_TOKEN"]}

    def call(method, path, body=None):
        st, js = fetch(method, API + path, h, body)
        if st >= 400 or js.get("success") is False:
            errs = "; ".join(str(e.get("message")) for e in js.get("errors", [])) or f"HTTP {st}"
            raise LaunchError(f"Cloudflare API {method} {path.split('?')[0]} failed: {errs}",
                              "check CF_API_TOKEN permissions (Account: Cloudflare Tunnel Edit, Zone: DNS Edit, Zone Read)")
        return js

    zone_id = account_id = None
    zones = [cfg["CF_ZONE"]] if cfg.get("CF_ZONE") else zone_candidates(hostname)
    for z in zones:
        res = call("GET", f"/zones?name={z}").get("result") or []
        if res:
            zone_id, account_id = res[0]["id"], (cfg.get("CF_ACCOUNT_ID") or res[0].get("account", {}).get("id"))
            break
    if not zone_id or not account_id:
        raise LaunchError(f"no Cloudflare zone found for {hostname}", "set CF_ZONE (and CF_ACCOUNT_ID) in ~/.visoswap/%s.env" % target)

    found = call("GET", f"/accounts/{account_id}/cfd_tunnel?name={name}&is_deleted=false").get("result") or []
    created = False
    if found:
        tid = found[0]["id"]
        rep.log(f"tunnel '{name}' exists ({tid[:8]}...); its configuration is left untouched")
    elif dry_run:
        rep.log(f"[dry-run] would create tunnel '{name}', ingress {hostname} -> http://localhost:8000, and DNS CNAME")
        return ""
    else:
        tid = call("POST", f"/accounts/{account_id}/cfd_tunnel", {"name": name, "config_src": "cloudflare"})["result"]["id"]
        created = True
        rep.log(f"created tunnel '{name}'")
    if created:
        call("PUT", f"/accounts/{account_id}/cfd_tunnel/{tid}/configurations", ingress_config(hostname))

    recs = call("GET", f"/zones/{zone_id}/dns_records?name={hostname}").get("result") or []
    want = f"{tid}.cfargotunnel.com"
    if recs:
        if recs[0].get("content") != want:
            raise LaunchError(f"DNS record for {hostname} already points elsewhere ({recs[0].get('content')})",
                              "pick another VISOSWAP_HOSTNAME or fix the record by hand; the launcher never overwrites DNS")
        rep.log(f"DNS {hostname} already routed to the tunnel")
    elif dry_run:
        rep.log(f"[dry-run] would create CNAME {hostname} -> {want}")
    else:
        call("POST", f"/zones/{zone_id}/dns_records", {"type": "CNAME", "name": hostname, "content": want, "proxied": True})
        rep.log(f"DNS {hostname} -> tunnel created")
    tok = call("GET", f"/accounts/{account_id}/cfd_tunnel/{tid}/token").get("result")
    return tok if isinstance(tok, str) else ""


def provision_cert(cfg: Dict[str, str], name: str, hostname: str, rep: Reporter, run: Runner) -> str:
    exe = cloudflared_bin(cfg)
    r = run.run([exe, "tunnel", "list", "--output", "json", "--name", name])
    if r.rc != 0:
        raise LaunchError("cloudflared could not list tunnels: " + r.out[:200], "run `cloudflared tunnel login` and retry")
    try:
        existing = [t for t in json.loads(r.stdout or "[]") if t.get("name") == name and not t.get("deleted_at")]
    except (ValueError, AttributeError):
        # never assume "no such tunnel" from output we could not parse
        raise LaunchError("could not parse `cloudflared tunnel list` output", "run `cloudflared tunnel list` yourself; store the token via --setup")
    if not existing:
        c = run.run([exe, "tunnel", "create", name], mutating=True)
        if c.rc != 0:
            raise LaunchError("cloudflared tunnel create failed: " + c.out[:200], "check `cloudflared tunnel login`")
    else:
        rep.log(f"tunnel '{name}' exists")
    # no --overwrite-dns: an existing record that points elsewhere is an error we surface
    d = run.run([exe, "tunnel", "route", "dns", name, hostname], mutating=True)
    if d.rc != 0 and "already configured to route to your tunnel" not in d.out.lower():
        raise LaunchError("DNS route failed: " + d.out[:200], "fix the DNS record by hand or choose another hostname")
    t = run.run([exe, "tunnel", "token", name])
    return t.stdout.strip() if t.rc == 0 and not run.dry_run else ""


TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-+/=.]{40,}$")


def valid_token(tok: str) -> bool:
    return bool(TOKEN_RE.match(tok or ""))


def prompt_secret(label: str) -> str:
    import getpass
    return getpass.getpass(label)       # never echoed


def store_token(target: str, token: str, hostname: str, rep: Reporter, home: Optional[Path] = None,
                cfg: Optional[Dict[str, str]] = None) -> None:
    """Persist a pasted tunnel token (mode 600). Refuses to replace a stored one."""
    p = env_path(target, home)
    existing = (cfg or {}).get("CF_TUNNEL_TOKEN") or load_env_file(p).get("CF_TUNNEL_TOKEN")
    if existing:
        raise LaunchError("a tunnel token is already stored; it is never overwritten",
                          f"edit {p} yourself if you really want to replace it")
    token = (token or "").strip()
    if not valid_token(token):
        raise LaunchError("that does not look like a Cloudflare tunnel token", "copy the long token after `--token` from Zero Trust > Networks > Tunnels > Install")
    rep.redact.add(token)
    merge_env_file(p, {"CF_TUNNEL_TOKEN": token, "VISOSWAP_HOSTNAME": hostname,
                       "CF_TUNNEL_NAME": (cfg or {}).get("CF_TUNNEL_NAME") or DEFAULT_TUNNEL_NAMES[target]})
    rep.log(f"token stored in {p} (mode 600)")


def ensure_tunnel(target: str, cfg: Dict[str, str], rep: Reporter, run: Runner, mode: str = "auto",
                  fetch: Fetch = default_fetch, home: Optional[Path] = None, interactive: bool = False,
                  prompt: Callable[[str], str] = prompt_secret) -> Dict[str, str]:
    """Make sure cfg has CF_TUNNEL_TOKEN + VISOSWAP_HOSTNAME (persisting them to the env file) or
    decide on a quick tunnel. Returns the (possibly updated) cfg; cfg['TUNNEL_MODE'] is named|quick."""
    if mode == "quick":
        rep.log("quick tunnel requested: a random trycloudflare.com URL will be used")
        return {**cfg, "TUNNEL_MODE": "quick"}
    if cfg.get("CF_TUNNEL_TOKEN") and cfg.get("VISOSWAP_HOSTNAME"):
        rep.log("Cloudflare: token and hostname already configured")
        return {**cfg, "TUNNEL_MODE": "named"}
    name = cfg.get("CF_TUNNEL_NAME") or DEFAULT_TUNNEL_NAMES[target]
    hostname = normalize_host(cfg.get("VISOSWAP_HOSTNAME") or DEFAULT_HOSTNAMES[target])
    token = ""
    if cfg.get("CF_API_TOKEN"):
        rep.step(f"Cloudflare API: tunnel '{name}' -> {hostname}")
        token = provision_api(cfg, target, name, hostname, rep, run.dry_run, fetch)
    elif cloudflared_bin(cfg) and has_cert(home):
        rep.step(f"cloudflared login found: tunnel '{name}' -> {hostname}")
        token = provision_cert(cfg, name, hostname, rep, run)
    elif interactive and mode != "quick":
        rep.log("No stored token, CF_API_TOKEN or `cloudflared tunnel login`.")
        tok = prompt(f"Paste the Cloudflare tunnel token for {hostname} (hidden; Enter = quick tunnel instead): ").strip()
        if tok:
            if not valid_token(tok):
                raise LaunchError("that does not look like a Cloudflare tunnel token", "re-run --setup and paste the token after `--token`")
            token = tok
        else:
            rep.log("Using a trycloudflare.com quick tunnel.")
            return {**cfg, "TUNNEL_MODE": "quick"}
    else:
        rep.log("No Cloudflare configuration (no CF_API_TOKEN, no `cloudflared tunnel login` cert.pem).")
        if mode == "named":
            raise LaunchError("named tunnel requested but Cloudflare is not configured",
                              "run `cloudflared tunnel login`, or put CF_API_TOKEN in ~/.visoswap/%s.env" % target)
        rep.log("Falling back to a trycloudflare.com quick tunnel (URL changes on every launch).")
        return {**cfg, "TUNNEL_MODE": "quick"}
    if run.dry_run:
        return {**cfg, "VISOSWAP_HOSTNAME": hostname, "TUNNEL_MODE": "named"}
    if token and not valid_token(token):
        raise LaunchError("the obtained tunnel token does not look valid; not storing it", "check the tunnel in the Cloudflare dashboard and paste its token via --setup")
    if not token:
        raise LaunchError("could not obtain the tunnel token", "run `cloudflared tunnel token %s` manually and store it as CF_TUNNEL_TOKEN" % name)
    written = merge_env_file(env_path(target, home), {"CF_TUNNEL_TOKEN": token, "VISOSWAP_HOSTNAME": hostname,
                                                       "CF_TUNNEL_NAME": name})
    rep.log(f"saved {', '.join(written) or 'nothing new'} to {env_path(target, home)} (mode 600)")
    rep.redact.add(token)
    return {**cfg, "CF_TUNNEL_TOKEN": token, "VISOSWAP_HOSTNAME": hostname, "TUNNEL_MODE": "named"}
