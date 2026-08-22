"""The stable generation seam used by the scheduler and worker."""

from __future__ import annotations

import abc
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
    project: dict[str, Any], timeout: float, worker_index: int = 0
) -> FrameGenerator:
    """Build the sole current generator; later backends return through this seam."""
    from backend.services.engine_backend import EngineFrameGenerator

    return EngineFrameGenerator.from_project(project, timeout, worker_index)
