"""Sandbox status and starter-kit verification (Part E plan Â§3.9; D-49).

The API never contacts Docker and never runs anything (D-41): it reads the status file the worker writes
(``data/sandbox_status.json``), the image lock and the database, and it **queues** verification requests
that the worker runs. Every route requires sign-in and the ``project_user`` role; the request route is
rate limited and audited. Responses carry metadata only (statuses, counts, durations), never output text.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict

from devteam_api.deps import Audit, DbSession, ProjectUser, api_error, limit_rate
from devteam_api.schemas import ERROR_RESPONSES, ErrorResponse
from devteam_core.config import Settings
from devteam_core.contracts.sandbox import TemplateVerification, TemplateVerificationStatus
from devteam_core.ids import is_valid_id, new_id
from devteam_services.integration_builder import load_manifest, template_ids
from devteam_state.repositories import sandbox as repo

router = APIRouter(
    prefix="/api/sandbox", tags=["sandbox"], dependencies=[Depends(limit_rate)], responses=ERROR_RESPONSES
)
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found"}}
STATUS_FILE = "sandbox_status.json"


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageOut(_Out):
    name: str
    tag: str
    image_id: str
    audit: str


class SandboxStatusOut(_Out):
    configured: bool
    reachable: bool | None  # None: the worker has not checked yet
    engine_version: str | None = None
    operating_system: str | None = None
    reason: str | None = None
    checked_at: str | None = None
    images: list[ImageOut]
    lock_holder: Literal["llm", "sandbox"] | None = None


class TestCountsOut(_Out):
    __test__ = False
    tests: int
    passed: int
    failed: int
    errors: int
    skipped: int


class RecipeOutcomeOut(_Out):
    recipe: str
    status: str
    exit_code: int | None
    tests: TestCountsOut | None
    duration_ms: int
    peak_memory_mb: float | None
    reason: str | None


class VerificationOut(_Out):
    verification_id: str
    template_id: str
    template_version: str
    status: str
    recipes: list[RecipeOutcomeOut]
    duration_ms: int | None
    peak_memory_mb: float | None
    requested_by: str
    requested_at: datetime
    finished_at: datetime | None
    reason: str | None


class TemplateOut(_Out):
    template_id: str
    version: str
    description: str
    frontend: str
    self_test: list[str]
    latest: VerificationOut | None


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _verification_out(item: TemplateVerification) -> VerificationOut:
    return VerificationOut(
        verification_id=item.verification_id,
        template_id=item.template_id,
        template_version=item.template_version,
        status=item.status.value,
        recipes=[
            RecipeOutcomeOut(
                recipe=o.recipe,
                status=o.status.value,
                exit_code=o.exit_code,
                tests=TestCountsOut(**o.tests.model_dump(exclude={"schema_version"})) if o.tests else None,
                duration_ms=o.duration_ms,
                peak_memory_mb=o.peak_memory_mb,
                reason=o.reason,
            )
            for o in item.recipes
        ],
        duration_ms=item.duration_ms,
        peak_memory_mb=item.peak_memory_mb,
        requested_by=item.requested_by,
        requested_at=item.requested_at,
        finished_at=item.finished_at,
        reason=item.reason,
    )


@router.get("/status", response_model=SandboxStatusOut, name="sandbox.status")
def sandbox_status(request: Request, db: DbSession, _: ProjectUser) -> SandboxStatusOut:
    settings = _settings(request)
    path = settings.data_dir / STATUS_FILE
    recorded: dict[str, Any] = {}
    if path.is_file():
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            recorded = {}
    images: list[ImageOut] = []
    lock_path = settings.sandbox_image_lock_path
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        images = [
            ImageOut(name=name, tag=entry["tag"], image_id=entry["image_id"][:19],
                     audit=str(entry.get("audit", {}).get("status", "unknown")))
            for name, entry in sorted(lock.get("images", {}).items())
        ]  # fmt: skip
    holder = repo.current_resource_lock(db)
    return SandboxStatusOut(
        configured=bool(recorded.get("configured", settings.sandbox.enabled)),
        reachable=recorded.get("reachable"),
        engine_version=recorded.get("version"),
        operating_system=recorded.get("operating_system"),
        reason=recorded.get("reason"),
        checked_at=recorded.get("checked_at"),
        images=images,
        lock_holder=holder.holder if holder is not None else None,
    )


@router.get("/templates", response_model=list[TemplateOut], name="sandbox.templates")
def list_templates(db: DbSession, _: ProjectUser) -> list[TemplateOut]:
    latest = repo.latest_verifications(db)
    rows = []
    for template_id in template_ids():
        manifest = load_manifest(template_id)
        found = latest.get(template_id)
        rows.append(
            TemplateOut(
                template_id=template_id, version=manifest.version, description=manifest.description,
                frontend=manifest.frontend, self_test=list(manifest.self_test),
                latest=_verification_out(found) if found else None,
            )
        )  # fmt: skip
    return rows


@router.post(
    "/templates/{template_id}/verifications",
    response_model=VerificationOut,
    status_code=status.HTTP_201_CREATED,
    name="sandbox.verification_request",
    responses={**NOT_FOUND, 409: {"model": ErrorResponse, "description": "Already queued or running"}},
)
def request_verification(
    template_id: str, db: DbSession, scope: Audit, principal: ProjectUser
) -> VerificationOut:
    if template_id not in template_ids():
        raise api_error(status.HTTP_404_NOT_FOUND, "template.not_found", "Starter kit not found.")
    if repo.has_active_verification(db, template_id):
        raise api_error(status.HTTP_409_CONFLICT, "template.verification_active",
                        "A verification of this starter kit is already queued or running.")  # fmt: skip
    queued = TemplateVerification(
        verification_id=new_id("tvf"),
        template_id=template_id,
        template_version=load_manifest(template_id).version,
        status=TemplateVerificationStatus.QUEUED,
        requested_by=principal.id,
        requested_at=datetime.now().astimezone(),
    )
    repo.add_verification(db, queued)
    scope.set(target_type="template", target_id=template_id, verification_id=queued.verification_id)
    return _verification_out(queued)


@router.get("/verifications/{verification_id}", response_model=VerificationOut, name="sandbox.verification",
            responses=NOT_FOUND)  # fmt: skip
def read_verification(verification_id: str, db: DbSession, _: ProjectUser) -> VerificationOut:
    found = repo.get_verification(db, verification_id) if is_valid_id(verification_id, "tvf") else None
    if found is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "verification.not_found", "Verification not found.")
    return _verification_out(found)
