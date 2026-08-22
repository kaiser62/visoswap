"""The Qt-free engine implementation of the scheduler's generator seam."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from backend.services.generator import FrameGenerator, GenerationResult


class EngineFrameGenerator(FrameGenerator):
    """Bind once, then floor timestamps to avoid selecting a future frame."""

    name = "engine"
    decodes_own_source = True

    def __init__(self, provider: str = "CUDA") -> None:
        self.provider = provider
        self._engine: Any | None = None
        self._project: dict[str, Any] | None = None
        self._media: dict[str, Any] = {}
        self.faces: list[Any] = []

    @classmethod
    def from_project(cls, project: dict[str, Any], timeout: float = 0, worker_index: int = 0):
        from backend.config import get_settings
        return cls(get_settings().visomaster_provider)

    def _get_engine(self):
        if self._engine is None:
            from visoswap.engine import Engine
            self._engine = Engine(device="cuda")
        return self._engine

    async def health(self) -> dict[str, Any]:
        from visoswap.engine import SAFE_PROVIDERS
        return {"backend": self.name, "provider": self.provider, "providers": SAFE_PROVIDERS}

    async def validate(self) -> None:
        if self.provider not in {"CUDA", "CPU"}:
            raise ValueError(f"unsupported provider {self.provider!r}; TensorRT is refused")

    async def bind(self, project: dict[str, Any]) -> None:
        if self._project and self._project.get("id") == project.get("id"):
            return
        video = project.get("video_path")
        if not video:
            raise ValueError("project has no local video to bind")
        engine = self._get_engine()
        self._media = engine.load(str(video))
        self.faces = engine.detect_faces()
        self._project = dict(project)

    async def generate(self, image_bytes: bytes, filename: str) -> GenerationResult:
        raise NotImplementedError("engine generator decodes its bound video")

    async def generate_at(self, timestamp: float, filename: str) -> GenerationResult:
        if not self._project:
            raise RuntimeError("generator is not bound")
        import cv2
        started = time.monotonic()
        fps = float(self._media["fps"])
        frame_number = int(timestamp * fps)  # floor: playback must never use a future frame
        source = self._project.get("source_face_path")
        if not source:
            raise ValueError("project has no source_face_path")
        frame = self._get_engine().swap(frame_number, str(source))
        ok, encoded = cv2.imencode(Path(filename).suffix or ".jpeg", frame)
        if not ok:
            raise RuntimeError(f"could not encode {filename}")
        return GenerationResult(encoded.tobytes(), None, time.monotonic() - started, filename)

    async def unbind(self) -> None:
        self._project = None
        self._media = {}
        self.faces = []

    async def close(self) -> None:
        await self.unbind()
