"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.api import (
    backends,
    compose,
    faces,
    gallery,
    generation,
    playback,
    preview,
    projects,
    settings as settings_api,
    ws,
)
from backend.config import Settings, get_settings
from backend.models.database import db
from backend.services import cache, video
from backend.services.compose_queue import queue as compose_queue
from backend.services.ffmpeg import ffmpeg_available
from backend.services.scheduler import registry
from backend.services.video import ytdlp_available
from visoswap.models import bootstrap

log = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    # httpx logs a line per request; one per frame is pure noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# the model bootstrap gate
# ---------------------------------------------------------------------------


def _verify_models_at_startup(settings: Settings) -> None:
    """Refuse to start before any route can serve if required models are incomplete.

    Raising from the lifespan means the app never reaches a serving state, so no
    route can answer -- not merely that the generation route checks first. A
    check inside ``api/generation.py`` would satisfy criterion 2's wording and
    miss its point: the failure would arrive as a 500 on the first frame, which
    is the mid-inference failure BACKEND-01 exists to replace.

    The error is written to stderr as well as logged, because a user starting a
    server reads a terminal, not a log file. Mode selection and the verification
    marker live in ``visoswap.models.bootstrap`` (``verify_at_startup``).
    """
    try:
        bootstrap.verify_at_startup(
            settings.models_dir,
            settings.data_dir,
            settings.models_verify_mode,
        )
    except bootstrap.ModelVerificationError as exc:
        sys.stderr.write(str(exc) + "\n")
        log.error("model bootstrap refused: %s", exc)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.projects_dir.mkdir(parents=True, exist_ok=True)

    # Model gate first: refuse to start on incomplete models before any route can
    # serve, before the DB is even opened. See _verify_models_at_startup.
    _verify_models_at_startup(settings)

    await db.connect()
    log.info(
        "started | data_dir=%s backend=%s ffmpeg=%s yt-dlp=%s",
        settings.data_dir, settings.default_backend,
        ffmpeg_available(), ytdlp_available(),
    )
    if not ffmpeg_available():
        log.warning("ffmpeg/ffprobe not on PATH — frame extraction will fail")
    try:
        yield
    finally:
        # Order matters: stopping the schedulers is what queues the last round
        # of composes, and they are then cancelled rather than left running
        # against a database that is about to close.
        await registry.stop_all()
        await compose_queue.aclose()
        await db.close()


async def _bind_url(project_id: str, url: str) -> None:
    """Fetch/probe a URL and bind it to a project, off the request path.

    Failures are written to the project's `error` field rather than raised:
    the response has already been sent, so this is the only place the user can
    still be told the URL was unusable.
    """
    try:
        project = await db.get_project(project_id)
        source, info = await video.resolve_url(
            project_id, url, require_local=False
        )
    except Exception as exc:
        log.warning("[URL] project=%s url=%s failed: %s", project_id, url, exc)
        await db.update_project(project_id, status="error", error=str(exc)[:500])
        return
    is_local = not source.lower().startswith(("http://", "https://"))
    await db.update_project(
        project_id,
        video_path=source if is_local else None,
        video_url=url if not is_local else None,
        duration=info.duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        error=None,
    )
    log.info("[URL] project=%s bound duration=%.2f", project_id, info.duration or 0.0)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Predictive Video Frame Transformer",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(cache.UnsafePathError)
    async def _unsafe_path(request: Request, exc: cache.UnsafePathError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "ffmpeg": ffmpeg_available(),
            "ytdlp": ytdlp_available(),
            "default_backend": settings.default_backend,
            "concurrency": settings.generation_concurrency,
        }

    @app.get("/url={target:path}")
    async def open_url(target: str, request: Request) -> RedirectResponse:
        """Paste a video URL straight onto the address bar: `:8000/url=<URL>`.

        The path converter stops at the query string, so `?v=abc` would be lost
        — it is put back here. `=` after `url` is optional, so both
        `/url=https://…` and `/urlhttps://…` cannot be confused with an api
        route: the mount that serves the UI is registered after this one.

        Binding runs in the background because a yt-dlp fetch can take minutes
        and the browser must not sit on a dead socket. The UI polls the project
        until `has_video` flips, so the redirect can land immediately.
        """
        url = target.lstrip("=")
        if request.url.query:
            url = f"{url}?{request.url.query}"
        return await _open(url)

    @app.get("/", include_in_schema=False)
    async def root(url: str | None = None) -> Response:
        """`/?url=<percent-encoded URL>` — the form a share sheet or a copied
        link produces, and the one a URL with its own query survives intact.

        Registered before the static mount, so `/` with no `url` still has to
        serve the UI itself rather than falling through to it.
        """
        if url:
            return await _open(url)
        index = FRONTEND_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {"detail": "frontend not built; run 'npm run build' in ./frontend "
                       "or use the Vite dev server on :5173"}
        )

    async def _open(url: str) -> Response:
        try:
            # Same guard as POST /{id}/url: scheme allow-list, no loopback or
            # private literals. Reject before a project row is ever created.
            video.validate_url(url)
        except video.VideoSourceError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        # No `video_url` yet: `has_video` would flip before the probe, and the
        # player would be handed a source with no duration, width or fps.
        project = await db.create_project(name=url[:200])
        cache.ensure_project_dirs(project["id"])
        asyncio.create_task(_bind_url(project["id"], url))
        return RedirectResponse(f"/?project={project['id']}", status_code=303)

    app.include_router(projects.router)
    app.include_router(faces.router)
    app.include_router(preview.router)
    app.include_router(playback.router)
    app.include_router(gallery.router)
    app.include_router(generation.router)
    app.include_router(compose.router)
    app.include_router(backends.router)
    app.include_router(settings_api.router)
    app.include_router(ws.router)

    # Dedicated route for mobile web app so direct access to /mobile serves index.html
    @app.get("/mobile", include_in_schema=False)
    @app.get("/mobile/", include_in_schema=False)
    @app.get("/mobile/{subpath:path}", include_in_schema=False)
    async def mobile_root(subpath: str = "") -> Response:
        index = FRONTEND_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {"detail": "frontend not built; run 'npm run build' in ./frontend"}
        )

    # Serve the built frontend when it exists (single-container deployment).
    # `/` itself is handled above so `?url=` can be intercepted.
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
