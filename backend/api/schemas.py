"""Request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Backend = Literal["engine"]
# "interval": one target every `interval` seconds.
# "stream":   one target on every video frame, from the playhead forward.
GenerationMode = Literal["interval", "stream"]


class ProjectCreate(BaseModel):
    name: str = Field(default="Untitled project", max_length=200)
    interval: float | None = Field(default=None, gt=0, le=3600)
    lookahead: float | None = Field(default=None, gt=0, le=36000)
    backend: Backend | None = None
    processing_scale: float = Field(default=1.0, gt=0, le=1.0)
    processing_width: int | None = Field(default=None, ge=64, le=8192)
    generated_format: Literal["webp", "png", "jpeg"] | None = None
    full_video_mode: bool = False
    generation_mode: GenerationMode | None = None
    stream_buffer: float | None = Field(default=None, gt=0, le=600)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    interval: float | None = Field(default=None, gt=0, le=3600)
    lookahead: float | None = Field(default=None, gt=0, le=36000)
    backend: Backend | None = None
    processing_scale: float | None = Field(default=None, gt=0, le=1.0)
    processing_width: int | None = Field(default=None, ge=64, le=8192)
    generated_format: Literal["webp", "png", "jpeg"] | None = None
    full_video_mode: bool | None = None
    generation_mode: GenerationMode | None = None
    stream_buffer: float | None = Field(default=None, gt=0, le=600)
    # `source_face_path` is deliberately absent (T-05.1-02-07): it is written
    # solely by the activation endpoint from a validated face id, never by a
    # client payload.


class UrlSource(BaseModel):
    url: str = Field(..., max_length=4096)


class FaceOut(BaseModel):
    """One library face. Paths never appear -- only content-addressed URLs."""

    face_id: str
    display_name: str | None = None
    bytes: int
    url: str
    thumbnail_url: str | None = None


class FaceActivate(BaseModel):
    """Bind a library face to a project. No target index: D-04 keeps the
    automatic single-target chooser; per-target picking is a later phase."""

    face_id: str = Field(..., min_length=32, max_length=32)


class FaceAssignment(BaseModel):
    target_index: int
    face_id: str
    thumbnail_url: str | None = None


class FaceAssignments(BaseModel):
    assignments: list[FaceAssignment]


class AffectedProject(BaseModel):
    id: str
    name: str


class FaceUsageConflict(BaseModel):
    """The 409 body for deleting a face projects still point at (D-05).

    Machine-readable on purpose: the confirm dialog renders this list, not a
    human reading a message string.
    """

    message: str
    projects: list[AffectedProject]


class PlaybackUpdate(BaseModel):
    current_time: float = Field(..., ge=0)
    seeked: bool = False


class SchedulerStart(BaseModel):
    full_video: bool | None = None
    current_time: float = Field(default=0.0, ge=0)
    # Generate a fixed span instead of following the playhead: start at
    # `range_start` and cover `range_duration` seconds. The playhead is
    # irrelevant here — the run finishes the span whether or not it is watched.
    range_start: float | None = Field(default=None, ge=0)
    range_duration: float | None = Field(default=None, gt=0, le=36000)


class BackendTest(BaseModel):
    backend: Backend = "engine"


# ---------------------------------------------------------------------------
# Settings API (plan 05-01/05-02)
# ---------------------------------------------------------------------------


class SettingsUpdate(BaseModel):
    """Project-tier overrides to persist.

    ``overrides`` holds arbitrary keys: per-key type/bounds/option/tier
    validation is the Phase 3 store's job, not the schema's, so an unknown key
    or an invalid value reaches ``store.set_project`` and is rejected there with
    the correct error type (and nothing stored).
    """

    overrides: dict[str, bool | int | float | str] = {}


class SettingsResponse(BaseModel):
    """The 200 body of a settings read or write: every resolved value."""

    values: dict[str, bool | int | float | str] = {}


class PresetApplyResponse(BaseModel):
    """The 200 body of a preset apply: the write report plus resolved values."""

    report: dict[str, int | list]
    values: dict[str, bool | int | float | str] = {}
