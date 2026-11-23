"""FastAPI application factory.

Part A: ``/health`` and ``/version`` (public, reveal nothing sensitive).
Part B: ``/api/*`` — every route requires the local token or session cookie (HC-15, D-29 (a)), mutating
routes are rate limited (HC-18) and audited (HC-21), uploads are validated (HC-20).
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from devteam_api.middleware.body_limit import UploadBodyLimitMiddleware
from devteam_api.middleware.request_context import RequestContextMiddleware
from devteam_api.routers import audit, documents, projects, session, system
from devteam_core import __version__
from devteam_core.config import Settings, load_settings
from devteam_core.logging import configure_logging, get_logger, secret_registry
from devteam_harness.iam import LOCAL_HUMAN_ROLES, Principal, load_api_token, session_value
from devteam_harness.ratelimit import RateLimiter
from devteam_harness.secrets import secrets_manager_from_settings
from devteam_services.intake import IntakeService
from devteam_services.workspace import WorkspaceManager
from devteam_state import create_sqlite_engine, ensure_state_schema, session_factory
from devteam_state.repositories import principals as principals_repo

_log = get_logger("devteam_api")
_MULTIPART_OVERHEAD = 1024 * 1024


def _bootstrap_principal(app: FastAPI) -> Principal:
    with app.state.session_factory.begin() as db:
        record = principals_repo.ensure_local_human(db, display_name="Local owner", roles=LOCAL_HUMAN_ROLES)
    return Principal(id=record.id, kind=record.kind, display_name=record.display_name, roles=record.roles)


def create_app(
    settings: Settings | None = None,
    *,
    configure_logs: bool = True,
    token_provider: Callable[[], str] | None = None,
) -> FastAPI:
    """Build the API.

    The install token comes from the OS credential store (HC-17, D-18); startup fails closed if the
    store is unavailable or a legacy plaintext key file still exists. ``token_provider`` lets the
    D-18a migration verify a candidate key in-process without touching the store's normal path.
    """
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
        app.state.session_factory = session_factory(engine)
        if token_provider is not None:
            app.state.api_token = token_provider()
            secret_registry.register(app.state.api_token)
        else:
            app.state.api_token = load_api_token(
                secrets_manager_from_settings(resolved), resolved.api_token_path
            )
        app.state.session_cookie_value = session_value(app.state.api_token)
        app.state.principal = _bootstrap_principal(app)
        limits = resolved.ratelimit
        app.state.rate_limiters = {
            "upload": RateLimiter(capacity=limits.upload_per_minute),
            "mutation": RateLimiter(capacity=limits.mutation_per_minute),
            "session": RateLimiter(capacity=limits.session_per_minute),
        }
        app.state.workspaces = WorkspaceManager(resolved.workspaces_dir)
        app.state.intake = IntakeService(resolved.uploads, app.state.workspaces)
        _log.info("api_started", profile=resolved.profile, version=__version__)
        try:
            yield
        finally:
            engine.dispose()
            _log.info("api_stopped")

    app = FastAPI(
        title="DevTeam AI Platform API",
        version=__version__,
        description="Local-first platform API. Loopback only. All /api routes require the local token.",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    uploads = resolved.uploads
    # Middleware: the last one added runs first. Request context is outermost so every log line has IDs.
    app.add_middleware(
        UploadBodyLimitMiddleware,
        max_bytes=uploads.max_files_per_upload * uploads.max_file_bytes + _MULTIPART_OVERHEAD,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.auth.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(system.router)
    app.include_router(session.router)
    app.include_router(projects.router)
    app.include_router(documents.router)
    app.include_router(audit.router)
    return app
