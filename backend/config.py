"""Environment-driven settings. Everything configurable lives here."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_cors_origins() -> str:
    """Vite dev server origins, tracking `FRONTEND_PORT` when one is assigned."""
    port = os.environ.get("FRONTEND_PORT", "5173")
    return f"http://localhost:{port},http://127.0.0.1:{port}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The sole currently shipped backend. FrameGenerator remains the expansion seam.
    default_backend: str = "engine"
    # ONNX Runtime execution provider for the in-process backend. Measured on a
    # 4070 Ti Super at 1920x1080, one face: TensorRT 27.8 fps against CUDA's
    # 21.2 at one thread, 34.1 against 30.1 at four. The cost is a one-off
    # engine load of roughly 20s, paid at bind time rather than on a frame.
    # "TensorRT" | "TensorRT-Engine" | "CUDA" | "CPU", as VisoMaster names them.
    visomaster_provider: str = "CUDA"

    # Face selection. One face is swapped per video: the most prominent face
    # the classifier calls female, falling back to the largest face when it
    # finds none. `gender_model_path` is insightface's genderage.onnx (1.3MB);
    # without it selection still works, on size alone.
    gender_model_path: Path | None = None
    # Confidence below which a label is treated as unknown. Measured over 138
    # faces: male labels cluster at 0.2-3.5, confident females at 4-12, and
    # sub-1.0 calls flip between adjacent frames of the same face.
    gender_margin: float = Field(default=1.0, ge=0)

    # Storage
    data_dir: Path = REPO_ROOT / "data"
    database_url: str = "sqlite:///./data/app.db"

    # Model bootstrap (plan 04-03). The backend refuses to start until the
    # required models are present and (on a first run) hash-verified.
    # `MODELS_DIR` env is read into `models_dir` automatically (pydantic-settings
    # maps the field name case-insensitively). None means "let
    # visoswap.schema.resolve_models_dir decide": MODELS_DIR, then the repo's
    # `model_assets/` default. The gate must not invent a second resolution order.
    models_dir: Path | None = None
    # Startup verification mode: "auto" (fast when a verification marker for the
    # current manifest exists, full otherwise -- the safe default), "fast"
    # (presence/non-emptiness only, the developer-loop escape hatch), or "full"
    # (also hash every required entry). There is deliberately no off switch: an
    # off switch on a refusal-to-start gate is the first thing an environment
    # file sets and then forgets, and BACKEND-01 would quietly stop being true.
    models_verify_mode: str = "auto"

    # Generation
    # 1 by default: one engine serializes, so a second worker against it only
    # inflates per-frame latency (0.886s -> 0.913s per frame, same throughput).
    # Set this to the number of engines listed in `visomaster_url` — each
    # worker binds to one by index. Two engines measured 0.886s -> 0.479s per
    # frame, 1.85x, on a 4070 Ti Super that a single engine leaves 30-50% idle.
    # For `visomaster_local` this is also the swap thread pool size. Raw 1080p
    # swap throughput measured 24 fps at 1 thread, 34 at 2, 39 at 4, and
    # collapsed to under 1 fps at 8 — past four the GPU thrashes and the run is
    # worse than single-threaded. Four sustains ~18 generated fps end to end
    # (decode 7ms, swap 64ms, jpeg 23ms per frame).
    generation_concurrency: int = Field(default=4, ge=1, le=32)
    default_interval: float = Field(default=5.0, gt=0)
    default_lookahead: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    retry_backoff: float = Field(default=10.0, ge=0)

    # jpeg, not webp: at 1080p a webp encode measured 0.78-0.94s against
    # 0.015-0.031s for jpeg, an order of magnitude more than the face swap it
    # follows. Generated frames are a playback cache, not an archive, so the
    # size/quality edge webp has is not worth a frame budget it alone exceeds.
    generated_format: str = "jpeg"
    # Source bytes are retained for the abstract generator fallback.
    source_format: str = "webp"

    # Recording
    # The composed video is muxed continuously while generation runs, so a
    # cancel, an error or a hard kill all still leave a playable mp4.
    recorder_enabled: bool = True
    # Seconds the recorder trails the generation frontier before committing a
    # timestamp. Generation is asynchronous, so a frame for t can land after the
    # recorder has passed t; this is the grace window it gets. Larger keeps more
    # late frames, at the cost of the recording lagging further behind playback.
    recorder_grace: float = Field(default=15.0, ge=0)
    # veryfast, not a slower preset: this encode runs alongside the swap
    # pipeline and competes with it for CPU. `recorder_enabled=False` reclaims it.
    recorder_preset: str = "veryfast"
    recorder_crf: int = Field(default=20, ge=0, le=51)
    # Finished recordings are copied here under a readable name. The copy in
    # `data/projects/<id>/` is the working file and stays where the API serves
    # it from; this folder is the one meant for a human to open.
    output_dir: Path = REPO_ROOT / "output"
    # Copy a recording that stopped early too, suffixed `.partial`. Off would
    # mean a cancelled run leaves nothing in the output folder at all.
    output_include_partial: bool = True

    # Server
    host: str = "0.0.0.0"
    # `BACKEND_PORT` wins over `PORT` so a per-workspace port assignment can be
    # set without clobbering the generic name other tooling may already use.
    port: int = Field(default=8000, validation_alias=AliasChoices("BACKEND_PORT", "PORT"))
    log_level: str = "INFO"
    cors_origins: str = _default_cors_origins()
    max_upload_bytes: int = 8 * 1024**3

    enable_ytdlp: bool = True

    # Binaries
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    @field_validator("default_backend")
    @classmethod
    def _check_backend(cls, v: str) -> str:
        v = (v or "").lower().strip()
        if v != "engine":
            raise ValueError(f"unknown generation backend: {v}")
        return v

    @field_validator("visomaster_provider")
    @classmethod
    def _check_provider(cls, v: str) -> str:
        value = (v or "").upper().strip()
        if value not in {"CUDA", "CPU"}:
            raise ValueError(
                f"unsupported provider {v!r}; TensorRT is refused because its relative cache path writes outside the project"
            )
        return value

    @field_validator("generated_format", "source_format")
    @classmethod
    def _check_format(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"webp", "png", "jpeg", "jpg"}:
            raise ValueError(f"unsupported image format: {v}")
        return "jpeg" if v == "jpg" else v

    @field_validator("data_dir", mode="before")
    @classmethod
    def _resolve_data_dir(cls, v: object) -> Path:
        p = Path(str(v))
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()

    @field_validator("models_dir", mode="before")
    @classmethod
    def _resolve_models_dir(cls, v: object) -> Path | None:
        if v in (None, ""):
            return None
        p = Path(str(v))
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()

    @field_validator("models_verify_mode")
    @classmethod
    def _check_verify_mode(cls, v: str) -> str:
        v = (v or "").lower().strip()
        if v not in {"auto", "fast", "full"}:
            raise ValueError(
                f"unknown models_verify_mode {v!r}; expected auto, fast or full"
            )
        return v

    @property
    def resolved_gender_model(self) -> Path | None:
        """Where genderage.onnx lives, or None when it is not on this machine.

        Falls back to insightface's own cache directory, which is where the
        model already sits on a box that has ever run insightface, so the
        common case needs no configuration.
        """
        if self.gender_model_path:
            return self.gender_model_path
        cached = Path.home() / ".insightface" / "models" / "buffalo_l" / "genderage.onnx"
        return cached if cached.is_file() else None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_path(self) -> Path:
        """Absolute path to the app SQLite file, derived from `database_url`."""
        url = self.database_url
        prefix = "sqlite:///"
        raw = url[len(prefix) :] if url.startswith(prefix) else url
        p = Path(raw)
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    # Machine-global (D-03): one face store shared by every project,
    # deliberately outside `projects_dir` so deleting a project cannot take
    # the user's face library with it.
    @property
    def faces_dir(self) -> Path:
        return self.data_dir / "faces"


@lru_cache
def get_settings() -> Settings:
    return Settings()
