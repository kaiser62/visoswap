"""The stable generation seam used by the scheduler and worker."""

from __future__ import annotations

import abc
import importlib
import os
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class GenerationResult:
    data: bytes
    prompt_id: str | None
    duration: float
    source_filename: str | None = None


class FrameGenerator(abc.ABC):
    """Transforms source frames without exposing engine internals to scheduling."""

    name: str = "generator"
    decodes_own_source: bool = False

    @abc.abstractmethod
    async def health(self) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def validate(self) -> None: ...

    @abc.abstractmethod
    async def generate(self, image_bytes: bytes, filename: str) -> GenerationResult: ...

    async def bind(self, project: dict[str, Any]) -> None:
        return None

    async def unbind(self) -> None:
        return None

    async def generate_at(self, timestamp: float, filename: str) -> GenerationResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None


def build_generator(
    project: dict[str, Any], timeout: float, worker_index: int = 0, pooled: bool = False
) -> FrameGenerator:
    """Build the sole current generator; later backends return through this seam.

    `pooled` marks a long-lived scheduler worker. With VISOSWAP_PROCESS_WORKERS=1
    such workers get their engine in a child process (no GIL sharing, spread over
    the GPUs); short-lived callers (validate, preview) stay in-process.

    `VISOSWAP_GENERATOR_FACTORY` ("module:function") replaces the whole choice and
    is how tests drive the worker without an engine or a GPU. It is honored only
    for pooled workers, so it can never affect a one-off validate/preview call.
    """
    if pooled:
        factory = os.environ.get("VISOSWAP_GENERATOR_FACTORY")
        if factory:
            mod, _, fn = factory.partition(":")
            gen = getattr(importlib.import_module(mod), fn)
            return gen(project)
        from backend.services.hardware import process_workers_enabled
        from backend.services.process_generator import ProcessEngineGenerator

        if process_workers_enabled():
            return ProcessEngineGenerator.from_project(project, timeout, worker_index)
    from backend.services.engine_backend import EngineFrameGenerator

    return EngineFrameGenerator.from_project(project, timeout, worker_index)
