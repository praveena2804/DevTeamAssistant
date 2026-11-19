"""FastAPI application factory.

Part A exposes only ``/health`` and ``/version``. Identity (local API token), CORS and rate limits are added
in Part B (HC-15, HC-18) before any mutating endpoint exists.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from devteam_api.middleware.request_context import RequestContextMiddleware
from devteam_api.routers import system
from devteam_core import __version__
from devteam_core.config import Settings, load_settings
from devteam_core.logging import configure_logging, get_logger
from devteam_state import create_sqlite_engine, ensure_state_schema

_log = get_logger("devteam_api")


def create_app(settings: Settings | None = None, *, configure_logs: bool = True) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if configure_logs:
            configure_logging(resolved)
        if resolved.database.auto_migrate:
            ensure_state_schema(resolved)
        engine = create_sqlite_engine(
            resolved.state_db_path, busy_timeout_ms=resolved.database.busy_timeout_ms
        )
        app.state.engine = engine
        _log.info("api_started", profile=resolved.profile, version=__version__)
        try:
            yield
        finally:
            engine.dispose()
            _log.info("api_stopped")

    app = FastAPI(
        title="DevTeam AI Platform API",
        version=__version__,
        description="Local-first platform API. Loopback only.",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.add_middleware(RequestContextMiddleware)
    app.include_router(system.router)
    return app
