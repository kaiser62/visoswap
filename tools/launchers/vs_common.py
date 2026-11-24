"""Shared plumbing for the Kaggle and Colab launchers (stdlib only, Python >= 3.9).

Everything that touches the outside world goes through :class:`Runner`, which
knows which commands are mutating and refuses to execute those in ``--dry-run``.
Everything that is printed goes through :class:`Reporter`, which redacts secrets
(known values plus token-shaped strings) and can emit machine-readable JSON lines
for the web UI.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPO_URL = "https://github.com/kaiser62/visoswap"

#: Documented defaults of the repository owner. Override with VISOSWAP_HOSTNAME in
#: ~/.visoswap/<target>.env; nothing else in the code mentions a domain.
DEFAULT_HOSTNAMES = {"kaggle": "swap.basic.int.eu.org", "colab": "clswap.basic.int.eu.org"}
DEFAULT_TUNNEL_NAMES = {"kaggle": "visoswap", "colab": "visoswap-colab"}

SECRET_KEY_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|API_KEY|KEY)$", re.I)


def visoswap_home() -> Path:
    return Path(os.environ.get("VISOSWAP_HOME") or (Path.home() / ".visoswap"))


# --------------------------------------------------------------------------- redaction
_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_\-+/=]{16,}"),  # Cloudflare tunnel tokens / JWTs
    re.compile(r"(?i)(--token[ =])\S+"),
    re.compile(r"(?i)\b((?:CF_TUNNEL_TOKEN|CF_API_TOKEN|KAGGLE_KEY|API_KEY|SECRET|PASSWORD|TOKEN)\s*[=:]\s*)['\"]?[^\s'\"]{6,}"),
    re.compile(r"(?i)(Authorization:\s*Bearer\s+)\S+"),
]


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets: List[str] = []
        for s in secrets:
            self.add(s)

    def add(self, value: Optional[str]) -> None:
        if value and len(value) >= 8 and value not in self._secrets:
            self._secrets.append(value)
            self._secrets.sort(key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "***")
        for i, pat in enumerate(_PATTERNS):
            text = pat.sub((lambda m: m.group(1) + "***") if pat.groups else "***", text)
        return text


# --------------------------------------------------------------------------- reporting
class Reporter:
    """Text or JSON-lines progress output. JSON mode: one object per line,
    ``{"t": "log"|"step"|"result", ...}``; the last line is always the result."""

    def __init__(self, json_mode: bool = False, redact: Optional[Redactor] = None, stream=None):
        self.json_mode = json_mode
        self.redact = redact or Redactor()
        self.stream = stream or sys.stdout
        self.result_data: Optional[dict] = None

    def _emit(self, obj: dict, text: str) -> None:
        if self.json_mode:
            print(json.dumps(obj, ensure_ascii=False), file=self.stream, flush=True)
        else:
            print(text, file=self.stream, flush=True)

    def log(self, msg: str) -> None:
        msg = self.redact(str(msg))
        self._emit({"t": "log", "msg": msg}, "  " + msg)

    def step(self, msg: str) -> None:
        msg = self.redact(str(msg))
        self._emit({"t": "step", "msg": msg}, "==> " + msg)

    def prompt(self, kind: str, msg: str, **extra) -> None:
        """Ask the driver (web UI) for one line on stdin. Only emitted under --relay."""
        self._emit({"t": "prompt", "kind": kind, "msg": self.redact(msg), **{k: v for k, v in extra.items() if v}}, msg)

    def result(self, ok: bool, **fields) -> None:
        data = {"t": "result", "ok": ok}
        data.update(fields)
        self.result_data = data
        def walk(v):
            if isinstance(v, str):
                return self.redact(v)
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x) for x in v]
            return v
        clean = walk(data)
        if self.json_mode:
            print(json.dumps(clean, ensure_ascii=False), file=self.stream, flush=True)
            return
        if clean.get("error"):
            print(f"\nFAILED: {clean['error']}", file=self.stream)
            if clean.get("next"):
                print(f"Next:   {clean['next']}", file=self.stream)
        elif clean.get("checks"):
            for c in clean["checks"]:
                mark = {"ok": "OK     ", "unknown": "UNKNOWN", "invalid": "FIX    ", "missing": "MISSING"}.get(c["state"], c["state"])
                print(f"  [{mark}] {c['service']}: {c.get('detail', '')}" + (f"\n            fix: {c['fix']}" if c.get("fix") and c["state"] != "ok" else ""), file=self.stream)
            print(f"\n{clean.get('summary', '')}", file=self.stream)
        elif clean.get("url") and clean.get("healthy"):
            print(f"\nBackend ready:\n  web / desktop : {clean['url']}\n  mobile        : {clean['url']}/mobile", file=self.stream)
        elif clean.get("summary"):
            print(f"\n{clean['summary']}", file=self.stream)
        self.stream.flush()


class LaunchError(Exception):
    def __init__(self, msg: str, next_action: str = ""):
        super().__init__(msg)
        self.next_action = next_action


# --------------------------------------------------------------------------- subprocess
@dataclass
class Result:
    rc: int
    out: str
    skipped: bool = False
    stdout: str = ""        # stdout alone: machine-readable output must not be parsed from merged stderr


class Runner:
    """Single choke point for subprocesses. ``mutating=True`` commands are only
    printed (redacted) when ``dry_run`` is set. Read-only commands always run."""

    def __init__(self, rep: Reporter, dry_run: bool = False, readonly: bool = False):
        self.rep = rep
        self.dry_run = dry_run
        self.readonly = readonly          # --check: any mutating call is a bug and raises
        self.history: List[List[str]] = []

    def interactive(self, argv: Sequence[str]) -> int:
        """Run with inherited stdin/stdout (login flows). Only reached when ``can_prompt`` is true."""
        argv = [str(a) for a in argv]
        self.history.append(argv)
        if self.readonly:
            raise AssertionError("interactive command in read-only mode: " + shlex.join(argv))
        if self.dry_run:
            self.rep.log("[dry-run] would run: " + shlex.join(argv))
            return 0
        try:
            return subprocess.call(argv)
        except FileNotFoundError:
            return 127

    def run(self, argv: Sequence[str], mutating: bool = False, timeout: Optional[float] = None,
            cwd: Optional[Path] = None, input_text: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Result:
        argv = [str(a) for a in argv]
        self.history.append(argv)
        if mutating and self.readonly:
            raise AssertionError("mutating command in read-only mode: " + shlex.join(argv))
        if mutating and self.dry_run:
            self.rep.log("[dry-run] would run: " + shlex.join(argv))
            return Result(0, "", skipped=True)
        try:
            # stdin is never inherited: a CLI that wants to prompt gets EOF instead of blocking
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd, input=input_text,
                               stdin=None if input_text is not None else subprocess.DEVNULL,
                               env=({**os.environ, **env} if env else None))
            return Result(r.returncode, ((r.stdout or "") + (r.stderr or "")).strip(), stdout=(r.stdout or "").strip())
        except FileNotFoundError:
            return Result(127, f"command not found: {argv[0]}")
        except subprocess.TimeoutExpired as e:
            partial = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            return Result(124, (partial + "\n[timed out]").strip())


def read_relay_line(rep: Reporter, kind: str, msg: str, timeout: float = 600.0, **extra) -> str:
    """Emit a prompt event and wait for ONE line on stdin (the web UI's input endpoint writes it).
    EOF or timeout -> LaunchError, so a driver that cannot answer fails fast instead of hanging.
    The answer is registered with the redactor immediately."""
    import select
    rep.prompt(kind, msg, **extra)
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (ValueError, OSError):
        ready = [sys.stdin]
    if not ready:
        raise LaunchError("timed out waiting for input", "start the login again")
    line = sys.stdin.readline()
    if not line:
        raise LaunchError("no input was provided", "start the login again")
    line = line.strip()
    rep.redact.add(line)
    return line


# --------------------------------------------------------------------------- env files
def parse_env(text: str) -> Dict[str, str]:
    cfg: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip("'\"")
    return cfg


def env_path(target: str, home: Optional[Path] = None) -> Path:
    return (home or visoswap_home()) / f"{target}.env"


def load_env_file(path: Path) -> Dict[str, str]:
    return parse_env(path.read_text()) if path.is_file() else {}


def write_private(path: Path, data: str) -> None:
    """Atomic write with mode 0600 from the first byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def merge_env_file(path: Path, updates: Dict[str, str], overwrite: bool = False) -> List[str]:
    """Add ``updates`` to an env file, keeping every existing line. Existing keys are
    left alone unless ``overwrite``. Returns the keys written."""
    existing = path.read_text().splitlines() if path.is_file() else []
    present = parse_env("\n".join(existing))
    written: List[str] = []
    lines = list(existing)
    for k, v in updates.items():
        if not v:
            continue
        if k in present:
            if not overwrite or present[k] == v:
                continue
            lines = [f"{k}={v}" if parse_env(l).get(k) is not None and l.split("=", 1)[0].strip().replace("export ", "") == k else l for l in lines]
        else:
            lines.append(f"{k}={v}")
        written.append(k)
    if written:
        write_private(path, "\n".join(lines).rstrip("\n") + "\n")
    return written


ENV_KEYS = ("CF_TUNNEL_TOKEN", "VISOSWAP_HOSTNAME", "CF_TUNNEL_NAME", "CF_ZONE", "CF_API_TOKEN", "CF_ACCOUNT_ID",
            "KAGGLE_KERNEL", "KAGGLE_BIN", "KAGGLE_DATASET", "COLAB_BIN", "COLAB_SESSION", "COLAB_GPU",
            "VISOSWAP_REF", "CLOUDFLARED_BIN", "GENERATION_CONCURRENCY", "VISOSWAP_PROFILE")


def load_config(target: str, environ: Optional[dict] = None, home: Optional[Path] = None) -> Dict[str, str]:
    """~/.visoswap/<target>.env, overridden by the process environment for known keys."""
    environ = os.environ if environ is None else environ
    cfg = load_env_file(env_path(target, home))
    for k in ENV_KEYS:
        if environ.get(k):
            cfg[k] = environ[k]
    return cfg


def secrets_of(cfg: Dict[str, str]) -> List[str]:
    return [v for k, v in cfg.items() if SECRET_KEY_RE.search(k) and v]


# --------------------------------------------------------------------------- health / url
def health_ok(body: str) -> bool:
    """A Cloudflare 502/1033 page is not health: require ``"status": "ok"``."""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            return data.get("status") == "ok"
    except ValueError:
        pass
    return bool(re.search(r'"status"\s*:\s*"ok"', body or ""))


def fetch_health(url: str, timeout: float = 10.0) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            return r.status == 200 and health_ok(r.read(4096).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def normalize_host(host: str) -> str:
    return (host or "").replace("https://", "").replace("http://", "").strip().strip("/")


def save_url(target: str, url: str, rep: Reporter, dry_run: bool, clipboard: bool, home: Optional[Path] = None,
             also: Sequence[str] = ()) -> None:
    home = home or visoswap_home()
    if dry_run:
        rep.log(f"[dry-run] would write {home / (target + '_url')} and copy {url} to the clipboard")
        return
    home.mkdir(parents=True, exist_ok=True)
    for name in (f"{target}_url", *also):
        (home / name).write_text(url + "\n")
    if clipboard and shutil.which("pbcopy"):
        subprocess.run(["pbcopy"], input=url.encode(), check=False)
        rep.log("URL copied to the clipboard")


# --------------------------------------------------------------------------- lock + confirm
@contextlib.contextmanager
def target_lock(target: str, rep: Reporter, home: Optional[Path] = None):
    """One mutating launcher run per target (CLI and web UI share it)."""
    home = home or visoswap_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # Windows: no cross-process lock, run unguarded
        yield
        return
    f = open(home / f"{target}.lock", "a+")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LaunchError(f"another {target} launcher run is already in progress",
                              "wait for it to finish (see the web UI or the other terminal)")
        yield
    finally:
        f.close()


def confirm(prompt: str, assume_yes: bool, rep: Reporter) -> bool:
    if assume_yes:
        return True
    if rep.json_mode or not sys.stdin.isatty():
        raise LaunchError(f"{prompt} -- needs confirmation", "re-run with --yes")
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def wait_until(pred: Callable[[], bool], timeout: float, interval: float, sleep=time.sleep, clock=time.time,
               on_tick: Optional[Callable[[int], None]] = None) -> bool:
    t0 = clock()
    while True:
        if pred():
            return True
        waited = clock() - t0
        if waited >= timeout:
            return False
        if on_tick:
            on_tick(int(waited))
        sleep(interval)


def concurrency_arg(v: str):
    if v.lower() == "auto":
        return "auto"
    try:
        n = int(v)
    except ValueError:
        raise argparse.ArgumentTypeError("N or 'auto'")
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def can_prompt(args) -> bool:
    """The one interactivity gate: a real terminal, and not a machine/read-only/dry run."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty()
                and not (args.json or args.dry_run or getattr(args, "check", False)))


def common_args(ap, target: str) -> None:
    g = ap.add_argument_group("actions (default: launch / reuse)")
    g.add_argument("--setup", action="store_true", help="provision only (idempotent); nothing is launched")
    g.add_argument("--status", action="store_true", help="report state and exit")
    g.add_argument("--restart", action="store_true", help="relaunch even if running")
    g.add_argument("--down", action="store_true", help="stop %s (asks for confirmation)" % ("the Kaggle kernel" if target == "kaggle" else "the Colab session"))
    g.add_argument("--check", action="store_true", help="report login/token/quota state; changes nothing")
    g.add_argument("--login", action="store_true", help="run this service's login flow (non-interactive: Kaggle only)")
    g.add_argument("--relay", action="store_true", help="with --login: ask for secrets/codes via JSON prompt events answered on stdin (web UI)")
    g.add_argument("--cf-api-token-stdin", action="store_true", help="read a Cloudflare API token from stdin, create the tunnel + DNS with it, store only the tunnel token")
    g.add_argument("--store-cf-token", action="store_true", help="read a Cloudflare tunnel token from stdin and store it (mode 600); refuses if one is stored")
    o = ap.add_argument_group("options")
    o.add_argument("--dry-run", action="store_true", help="print what would be done; mutating commands are not executed")
    o.add_argument("--json", action="store_true", help="JSON-lines output (last line is the result)")
    o.add_argument("--yes", "-y", action="store_true", help="assume yes to confirmations")
    o.add_argument("--no-clipboard", action="store_true")
    o.add_argument("--concurrency", type=concurrency_arg, help="generation workers: N, or 'auto' (default: keep what is configured/running, else auto)")
    o.add_argument("--profile", action=argparse.BooleanOptionalAction, default=None,
                   help="per-stage timing logs on/off (--no-profile); default: keep what is configured/running, else off")
    o.add_argument("--tunnel", choices=("auto", "named", "quick"), default="auto",
                   help="auto: named Cloudflare tunnel if configured/provisionable, else trycloudflare quick tunnel")


def make_reporter(args, cfg: Optional[Dict[str, str]] = None) -> Reporter:
    return Reporter(json_mode=args.json, redact=Redactor(secrets_of(cfg or {})))
