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
    # Server-assigned only (the tracer / a trusted caller sets it directly on
    # the row). Not exposed for arbitrary client writes.
    source_face_path: str | None = None


class UrlSource(BaseModel):
    url: str = Field(..., max_length=4096)


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
