"""The Qt-free engine implementation of the scheduler's generator seam."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from backend.config import get_settings
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
        inst = cls(get_settings().visomaster_provider)
        inst._project = dict(project)
        return inst

    def _get_engine(self):
        if self._engine is None:
            import sqlite3

            from visoswap.engine import Engine
            from visoswap.settings import db as settings_db
            from visoswap.settings import store

            # The engine reads its control/parameters mappings unconditionally
            # (frame_worker.py indexes them by name), so a sparse dict would
            # raise a KeyError deep in a tensor op. Resolve the full global and
            # project tiers from the Phase 3 store so every key is present --
            # this is the store Phase 3 exists to serve. The settings tables
            # live in the backend's own app DB (they were applied at connect),
            # so a fresh sync connection to `get_settings().db_path` reads them.
            connection = sqlite3.connect(get_settings().db_path)
            connection.row_factory = sqlite3.Row
            try:
                settings_db.apply_settings_schema(connection)
                global_settings = store.resolve_control(connection)
                project_settings = {}
                if self._project and self._project.get("id"):
                    project_settings = store.resolve_parameters(
                        connection, self._project["id"]
                    )
            finally:
                connection.close()
            self._engine = Engine(
                device="cuda",
                global_settings=global_settings,
                project_settings=project_settings,
            )
        return self._engine

    async def health(self) -> dict[str, Any]:
        from visoswap.engine import SAFE_PROVIDERS
        return {"backend": self.name, "provider": self.provider, "providers": SAFE_PROVIDERS}

    async def validate(self) -> None:
        if self.provider not in {"CUDA", "CPU"}:
            raise ValueError(f"unsupported provider {self.provider!r}; TensorRT is refused")
        face = self._project.get("source_face_path") if self._project else None
        if face and not Path(face).is_file():
            raise ValueError(f"source face not found: {face}")

    async def bind(self, project: dict[str, Any]) -> None:
        if bool(self._media) and self._project and self._project.get("id") == project.get("id"):
            # Same project: update row metadata (such as source_face_path) without
            # re-decoding the video or re-detecting target faces.
            self._project = dict(project)
            return
        video = project.get("video_path")
        if not video:
            raise ValueError("project has no local video to bind")
        # Set the project before building the engine: `_get_engine` resolves the
        # project tier from the store, and `detect_faces` reads the project
        # settings (e.g. `SimilarityThresholdSlider`) unconditionally, so a
        # project-less engine would be built with an empty project tier.
        self._project = dict(project)

        def _bind() -> tuple[dict[str, Any], list[Any]]:
            engine = self._get_engine()
            return engine.load(str(video)), engine.detect_faces()

        # Off the loop, for the same reason as `generate_at`: building the
        # engine loads models and `detect_faces` is a full inference pass, and
        # both used to run where nothing else could be served.
        self._media, self.faces = await asyncio.to_thread(_bind)

    async def generate(self, image_bytes: bytes, filename: str) -> GenerationResult:
        raise NotImplementedError("engine generator decodes its bound video")

    async def generate_at(self, timestamp: float, filename: str) -> GenerationResult:
        if not self._project:
            raise RuntimeError("generator is not bound")
        import cv2
        started = time.monotonic()
        fps = float(self._media.get("fps") or (self._project and self._project.get("fps")) or 24.0)
        frame_number = int(timestamp * fps)  # floor: playback must never use a future frame
        source = self._project.get("source_face_path")
        if not source:
            raise ValueError("project has no source_face_path")
        # The worker's helper is the one place the downscale rule lives: an
        # explicit width wins, otherwise a scale below one applied to the
        # media's width, otherwise nothing. It never ran for this backend
        # because it sits on the source-frame extraction branch this generator
        # skips (`decodes_own_source`), which is what left the knob inert.
        from backend.workers.generation_worker import _processing_width

        target_width = _processing_width(self._project)

        def _swap() -> bytes:
            frame = self._get_engine().swap(
                frame_number, str(source), target_width=target_width
            )
            ok, encoded = cv2.imencode(Path(filename).suffix or ".jpeg", frame)
            if not ok:
                raise RuntimeError(f"could not encode {filename}")
            return encoded.tobytes()

        # The swap is a synchronous CUDA call and the encode is a synchronous
        # CPU one. Awaited directly on the loop they starved everything else
        # the backend serves: with four workers each blocking for a few hundred
        # milliseconds per frame, a plain status read measured 4.3s during a
        # run against 6ms idle, which is long enough for a client timeout to
        # turn an ordinary poll into a 500. Each worker owns its own generator
        # and its own engine, so only one thread is ever inside a given one.
        data = await asyncio.to_thread(_swap)
        return GenerationResult(data, None, time.monotonic() - started, filename)

    async def unbind(self) -> None:
        self._project = None
        self._media = {}
        self.faces = []

    async def close(self) -> None:
        await self.unbind()
