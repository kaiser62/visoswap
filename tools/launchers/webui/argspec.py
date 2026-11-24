"""The ONLY place that turns a (target, op, options) request into launcher flags.

Used by the web UI (to validate a request early) and by the host bridge (which re-validates everything: it never trusts
the container). No free-form argument ever reaches a launcher: every flag below comes from a fixed table, every value
is range/enum checked. Pure functions, no import-time environment reads.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

TARGETS = ("kaggle", "colab")

#: ops that run as long-lived, streamed jobs (one per target at a time)
JOB_OPS: Dict[str, List[str]] = {
    "launch": [], "start": [], "restart": ["--restart"], "stop": ["--down", "--yes"],
    "provision": ["--setup", "--yes"], "login": ["--login", "--relay"],
}
#: one-shot ops: result only
QUICK_OPS: Dict[str, List[str]] = {
    "status": ["--status"], "check": ["--check"], "quota": ["--quota"],
    "cf-token": ["--store-cf-token"], "cf-api-token": ["--cf-api-token-stdin"],
    "kaggle-json": ["--kaggle-json-stdin"], "colab-token": ["--colab-token-stdin"],
    "logs": ["--logs"],
}
OPS = {**JOB_OPS, **QUICK_OPS}
STDIN_OPS = {"cf-token", "cf-api-token", "kaggle-json", "colab-token"}      # one secret on stdin, then EOF
RELAY_OPS = {"login"}                                                        # stdin stays open for prompt answers
ONLY_FOR = {"kaggle-json": "kaggle", "colab-token": "colab"}
LOG_SOURCES = {"kaggle": ("kernel",), "colab": ("uvicorn", "tunnel", "both")}
START_OPTIONS = {"gpu", "concurrency", "profile", "api_key", "max_minutes"}
GPUS = ("L4", "T4")
API_KEY_FILE = "api_key"          # fixed name inside the launcher home; never a client-supplied path
MIN_LINES, MAX_LINES = 10, 1000


def validate(target: str, op: str, options: Optional[dict] = None, confirm: bool = False) -> None:
    """Raise ValueError (client-safe message) when the request is not allowed. Needs no filesystem."""
    if target not in TARGETS:
        raise ValueError("unknown target")
    if op not in OPS:
        raise ValueError("unknown action")
    if ONLY_FOR.get(op, target) != target:
        raise ValueError(f"{op} does not apply to {target}")
    options = options or {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    if op == "stop" and confirm is not True:
        raise ValueError('stop requires {"confirm": true}')
    if op == "logs":
        if set(options) - {"source", "lines"}:
            raise ValueError("unknown option")
        if options.get("source") not in LOG_SOURCES[target]:
            raise ValueError("unknown log source")
        n = options.get("lines", 200)
        if not isinstance(n, int) or isinstance(n, bool) or not MIN_LINES <= n <= MAX_LINES:
            raise ValueError(f"lines must be {MIN_LINES}..{MAX_LINES}")
        return
    if op in ("launch", "start", "restart"):
        _start_options(target, options)
    elif options:
        raise ValueError(f"{op} takes no options")


def _start_options(target: str, o: dict) -> List[Tuple[str, Optional[str]]]:
    extra = set(o) - START_OPTIONS
    if extra:
        raise ValueError("unknown option")
    out: List[Tuple[str, Optional[str]]] = []
    if o.get("gpu") not in (None, ""):
        if target != "colab" or o["gpu"] not in GPUS:
            raise ValueError("gpu must be L4 or T4 and applies to Colab only")
        out.append(("--gpu", o["gpu"]))
    c = o.get("concurrency")
    if c not in (None, "", "auto"):
        if isinstance(c, bool) or not (isinstance(c, int) or (isinstance(c, str) and c.isdigit())) or not 1 <= int(c) <= 16:
            raise ValueError("concurrency must be auto or 1..16")
        out.append(("--concurrency", str(int(c))))
    elif c == "auto":
        out.append(("--concurrency", "auto"))
    p = o.get("profile")
    if p is not None and p != "":
        if not isinstance(p, bool):
            raise ValueError("profile must be true or false")
        out.append(("--profile" if p else "--no-profile", None))
    k = o.get("api_key")
    if k not in (None, "", "keep"):
        if k not in ("on", "off"):
            raise ValueError("api_key must be on or off")
        out.append(("--api-key-file" if k == "on" else "--no-api-key", API_KEY_FILE if k == "on" else None))
    m = o.get("max_minutes")
    if m not in (None, ""):
        if target != "kaggle" or isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= 720:
            raise ValueError("max_minutes must be 0..720 and applies to Kaggle only")
        out.append(("--max-minutes", str(m)))
    return out


def build_flags(target: str, op: str, options: Optional[dict], home: Path, confirm: bool = False) -> List[str]:
    """Launcher flags for a validated request. `home` is the EXECUTING side's launcher home (the api-key file lives there)."""
    validate(target, op, options, confirm)
    options = options or {}
    flags = list(OPS[op])
    if op == "logs":
        flags += [options["source"] if target == "colab" else "", "--lines", str(options.get("lines", 200))]
        flags = [f for f in flags if f]
    elif op in ("launch", "start", "restart"):
        for flag, val in _start_options(target, options):
            if flag == "--api-key-file":
                f = home / API_KEY_FILE
                if not f.is_file():
                    raise ValueError(f"no API key file at {f}: create it (printable ASCII, chmod 600) before turning the key on")
                val = str(f)
            flags.append(flag)
            if val is not None:
                flags.append(val)
    return flags
