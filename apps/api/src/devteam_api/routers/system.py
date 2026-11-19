"""System endpoints: ``/health`` and ``/version`` (roadmap §13 Part A)."""

import platform
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine, text

from devteam_core import __version__
from devteam_state import current_revision, head_revision

router = APIRouter(tags=["system"])

API_VERSION = "v1"


class ComponentCheck(BaseModel):
    status: Literal["ok", "error"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, ComponentCheck]


class VersionResponse(BaseModel):
    name: str
    version: str
    api_version: str
    python_version: str
    profile: str
    schema_revision: str | None
    schema_head: str


def _check_state_db(engine: Engine) -> ComponentCheck:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = current_revision(engine)
    except Exception as exc:
        return ComponentCheck(status="error", detail=type(exc).__name__)
    if revision != head_revision():
        return ComponentCheck(status="error", detail=f"schema at {revision}, expected {head_revision()}")
    return ComponentCheck(status="ok")


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "One or more components are unhealthy"}},
    summary="Platform health",
)
def health(request: Request, response: Response) -> HealthResponse:
    checks = {"state_db": _check_state_db(request.app.state.engine)}
    healthy = all(check.status == "ok" for check in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status="ok" if healthy else "degraded", checks=checks)


@router.get("/version", response_model=VersionResponse, summary="Platform version")
def version(request: Request) -> VersionResponse:
    engine: Engine = request.app.state.engine
    try:
        revision = current_revision(engine)
    except Exception:
        revision = None
    return VersionResponse(
        name="DevTeam AI",
        version=__version__,
        api_version=API_VERSION,
        python_version=platform.python_version(),
        profile=request.app.state.settings.profile,
        schema_revision=revision,
        schema_head=head_revision(),
    )
