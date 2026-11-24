"""Engine generator that runs each worker's engine in its own OS process.

Frame generation is mostly Python around short GPU calls (resizes, numpy
post-processing, tensor bookkeeping), so engines running as threads of one
process are serialised by the GIL: on a 4-core box with two T4s the GPUs
idled while one core was saturated. One child process per worker removes
that ceiling, and lets workers be spread over every GPU of the machine.

The child owns a plain `EngineFrameGenerator` and answers four commands over
a pipe (bind, generate, unbind, close). The parent-side class keeps the same
`FrameGenerator` surface, so the scheduler and worker do not change.

Enabled by `hardware.process_workers_enabled()` (auto, or VISOSWAP_PROCESS_WORKERS=1/0).
GPUs are counted with `nvidia-smi -L` (override with VISOSWAP_GPU_COUNT); worker `i` is pinned to GPU `i % count`
through CUDA_VISIBLE_DEVICES unless that variable is already set.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import threading
import time
import traceback
from typing import Any

from backend.services.engine_backend import EngineFrameGenerator
from backend.services.generator import GenerationResult
from backend.services.hardware import gpu_count

log = logging.getLogger(__name__)

_spawn_lock = threading.Lock()


def _child_main(conn: Any, provider: str) -> None:
    """Child process: serve engine commands until told to close."""
    import warnings

    warnings.filterwarnings("ignore")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    factory = os.environ.get("VISOSWAP_PROCGEN_FACTORY")  # "module:function", for tests
    if factory:
        import importlib

        mod, _, fn = factory.partition(":")
        gen = getattr(importlib.import_module(mod), fn)(provider)
    else:
        gen = EngineFrameGenerator(provider)
    try:
        while True:
            try:
                msg = conn.recv()
            except EOFError:
                break
            cmd = msg[0]
            try:
                if cmd == "bind":
                    loop.run_until_complete(gen.bind(msg[1]))
                    conn.send(("ok", gen._media))
                elif cmd == "gen":
                    result = loop.run_until_complete(gen.generate_at(msg[1], msg[2]))
                    conn.send(("ok", (result.data, result.duration)))
                elif cmd == "unbind":
                    loop.run_until_complete(gen.unbind())
                    conn.send(("ok", None))
                elif cmd == "close":
                    loop.run_until_complete(gen.close())
                    conn.send(("ok", None))
                    break
                else:
                    conn.send(("err", ("ValueError", f"unknown command {cmd!r}")))
            except Exception as exc:  # reported to the parent, which decides what to do
                conn.send(("err", (type(exc).__name__, f"{exc}\n{traceback.format_exc()}")))
    finally:
        try:
            conn.close()
        except Exception:
            pass


class ProcessEngineGenerator(EngineFrameGenerator):
    """`EngineFrameGenerator` whose engine lives in a dedicated child process."""

    name = "engine"
    decodes_own_source = True

    def __init__(self, provider: str = "CUDA", worker_index: int = 0) -> None:
        super().__init__(provider)
        self.worker_index = worker_index
        self._proc: mp.process.BaseProcess | None = None
        self._conn: Any | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_project(cls, project: dict[str, Any], timeout: float = 0, worker_index: int = 0):
        from backend.config import get_settings

        inst = cls(get_settings().visomaster_provider, worker_index)
        inst._project = dict(project)
        return inst

    # -- process management -------------------------------------------------

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    def _spawn(self) -> None:
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        proc = ctx.Process(
            target=_child_main,
            args=(child, self.provider),
            name=f"engine-worker-{self.worker_index}",
            daemon=True,
        )
        with _spawn_lock:  # the child inherits the environment at start
            saved = os.environ.get("CUDA_VISIBLE_DEVICES")
            pin = saved is None and gpu_count() > 1
            if pin:
                os.environ["CUDA_VISIBLE_DEVICES"] = str(self.worker_index % gpu_count())
            try:
                proc.start()
            finally:
                if pin:
                    if saved is None:
                        os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        child.close()
        self._proc, self._conn = proc, parent
        log.info("[PROCGEN] worker=%d pid=%s started (gpus=%d)", self.worker_index, proc.pid, gpu_count())

    def _kill(self) -> None:
        proc, conn = self._proc, self._conn
        self._proc = self._conn = None
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        if proc is not None and proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)

    def _rpc(self, *msg: Any) -> Any:
        """One blocking round trip. Runs in a thread (never on the event loop)."""
        with self._lock:
            if not self._alive():
                self._kill()
                self._spawn()
                if msg[0] != "bind" and self._project is not None and self._media:
                    # The child died mid-run: bring a fresh one back to the bound state.
                    self._conn.send(("bind", self._project))
                    status, payload = self._conn.recv()
                    if status != "ok":
                        raise RuntimeError(f"engine process rebind failed: {payload[1]}")
            try:
                self._conn.send(msg)
                status, payload = self._conn.recv()
            except (EOFError, BrokenPipeError, ConnectionResetError, OSError) as exc:
                self._kill()
                raise RuntimeError(f"engine process died ({exc!r}); it will be restarted") from exc
        if status != "ok":
            kind, text = payload
            if kind in {"ValueError", "FileNotFoundError"}:
                raise ValueError(text)
            raise RuntimeError(text)
        return payload

    # -- FrameGenerator surface ---------------------------------------------

    async def bind(self, project: dict[str, Any]) -> None:
        if bool(self._media) and self._project and self._project.get("id") == project.get("id"):
            self._project = dict(project)
            return
        self._project = dict(project)
        self._media = await asyncio.to_thread(self._rpc, "bind", dict(project))

    async def generate_at(self, timestamp: float, filename: str) -> GenerationResult:
        if not self._project:
            raise RuntimeError("generator is not bound")
        started = time.monotonic()
        data, _ = await asyncio.to_thread(self._rpc, "gen", timestamp, filename)
        return GenerationResult(data, None, time.monotonic() - started, filename)

    async def unbind(self) -> None:
        self._project = None
        self._media = {}
        self.faces = []
        if self._alive():
            try:
                await asyncio.to_thread(self._rpc, "unbind")
            except Exception:
                log.exception("[PROCGEN] worker=%d unbind failed", self.worker_index)

    async def close(self) -> None:
        self._project = None
        self._media = {}
        self.faces = []

        def _shutdown() -> None:
            with self._lock:
                if self._alive():
                    try:
                        self._conn.send(("close",))
                        if self._conn.poll(30):
                            self._conn.recv()
                    except Exception:
                        pass
                    self._proc.join(timeout=10)
                self._kill()  # frees the child's whole CUDA context

        await asyncio.to_thread(_shutdown)
