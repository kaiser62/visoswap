"""Machine facts that decide how many generation workers to run, and how.

Frame generation is Python around short GPU calls. Engines sharing one process
are serialised by the GIL, so on a box with spare CPU cores one engine *process*
per worker is faster (and spreads over every GPU); on a box with few cores the
processes just fight for CPU and slow the start-up (measured: 2-core Colab T4,
4 processes 8.5 fps and a 31 s first frame vs 8.7 fps / 10 s with 3 threads;
4-core Kaggle 2xT4, 4 processes ~21 fps steady vs 15 with threads).

Auto policy (an explicit env value always wins):
  VISOSWAP_PROCESS_WORKERS  1/0 force; unset/"auto": processes when an NVIDIA GPU
                            is present and there are at least MIN_PROCESS_CPUS cores.
  GENERATION_CONCURRENCY    N forces; unset: min(cpus, 3 * gpus) in process mode
                            (at least 2), 3 threads on a GPU box with few cores,
                            else the Settings default.
"""

from __future__ import annotations

import os
import subprocess

MIN_PROCESS_CPUS = 4
WORKERS_PER_GPU = 3
THREAD_WORKERS_GPU = 3

_gpus: int | None = None


def cpu_count() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def gpu_count() -> int:
    """NVIDIA GPUs visible to this machine (0 when there is none)."""
    global _gpus
    if _gpus is None:
        forced = os.environ.get("VISOSWAP_GPU_COUNT", "").strip()
        if forced.isdigit():
            _gpus = int(forced)
        else:
            try:
                out = subprocess.run(
                    ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10
                ).stdout
                _gpus = sum(1 for line in out.splitlines() if line.startswith("GPU "))
            except Exception:
                _gpus = 0
    return _gpus


def process_workers_enabled() -> bool:
    flag = os.environ.get("VISOSWAP_PROCESS_WORKERS", "auto").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    return gpu_count() > 0 and cpu_count() >= MIN_PROCESS_CPUS


def auto_concurrency(default: int = 4) -> int:
    """Worker count when GENERATION_CONCURRENCY is not set."""
    gpus = gpu_count()
    if gpus <= 0:
        return default
    if process_workers_enabled():
        return max(2, min(cpu_count(), WORKERS_PER_GPU * gpus))
    return THREAD_WORKERS_GPU
