"""Read-only platform settings (Part F.6; D-55): models, hardware profile, limits, eval-gate status.

Everything comes from configuration files and SQLite: the model inventory (installed tags and digests) is
written by the worker, so the API never contacts Ollama or the vector store (D-57). Nothing here edits
configuration: settings stay in files under version control.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from devteam_api.deps import DbSession, ProjectUser, limit_rate
from devteam_api.schemas import ERROR_RESPONSES
from devteam_core.config import Settings
from devteam_core.config.models import load_model_catalog
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.evaluation import check_report, report_paths
from devteam_harness.prompts.files import code_root, load_prompt_config
from devteam_state.repositories import model_calls as model_calls_repo

router = APIRouter(
    prefix="/api/settings", tags=["settings"], dependencies=[Depends(limit_rate)], responses=ERROR_RESPONSES
)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelOut(_Out):
    tag: str
    kind: str
    approved: bool
    benchmark_ref: str | None
    peak_ram_mb: int | None
    num_ctx: int
    timeout_s: float
    installed: bool | None  # None: the worker has not checked yet
    digest: str | None  # first 12 characters
    checked_at: datetime | None


class SlotOut(_Out):
    slot: str
    model: str | None
    fallbacks: list[str]


class ModelsOut(_Out):
    models: list[ModelOut]
    slots: list[SlotOut]
    agent_overrides: int
    operation_overrides: int
    keep_alive: str
    response_cache: bool
    ollama_version: str | None


class ProfileOut(_Out):
    profile: str
    max_model_ram_mb: int
    min_free_ram_mb: int
    ram_margin_mb: int
    sandbox_memory_mb: int
    sandbox_cpus: float


class LimitsOut(_Out):
    rate_limits: dict[str, Any]
    rag: dict[str, Any]
    context: dict[str, Any]
    llm: dict[str, Any]


class PromptStatusOut(_Out):
    prompt_id: str
    active_version: int
    eval_required: bool
    report: str  # "matches" | "none" | problem text


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _config_dir(settings: Settings) -> Path:
    return settings.root / "config"


@router.get("/models", response_model=ModelsOut, name="settings.models")
def models(request: Request, db: DbSession, _: ProjectUser) -> ModelsOut:
    settings = _settings(request)
    catalog = load_model_catalog(_config_dir(settings), max_model_ram_mb=settings.resources.max_model_ram_mb)
    seen = {row.model_tag: row for row in model_calls_repo.inventory(db)}
    out = [
        ModelOut(
            tag=tag, kind=entry.kind, approved=entry.approved, benchmark_ref=entry.benchmark_ref,
            peak_ram_mb=entry.peak_ram_mb, num_ctx=entry.num_ctx, timeout_s=entry.timeouts.total_s,
            installed=seen[tag].installed if tag in seen else None,
            digest=(seen[tag].digest or "")[:12] or None if tag in seen else None,
            checked_at=seen[tag].checked_at if tag in seen else None,
        )
        for tag, entry in sorted(catalog.models.items())
    ]  # fmt: skip
    slots = [
        SlotOut(
            slot=s.value, model=catalog.slot_model(s), fallbacks=list(catalog.fallback_ladders.get(s, ()))
        )
        for s in ModelSlot
    ]
    versions = {row.server_version for row in seen.values() if row.server_version}
    return ModelsOut(
        models=out, slots=slots, agent_overrides=len(catalog.agents),
        operation_overrides=len(catalog.operations),
        keep_alive=settings.ollama.keep_alive, response_cache=settings.llm.response_cache,
        ollama_version=next(iter(versions), None),
    )  # fmt: skip


@router.get("/profile", response_model=ProfileOut, name="settings.profile")
def profile(request: Request, _: ProjectUser) -> ProfileOut:
    settings = _settings(request)
    catalog = load_model_catalog(_config_dir(settings), max_model_ram_mb=settings.resources.max_model_ram_mb)
    return ProfileOut(
        profile=settings.profile, max_model_ram_mb=settings.resources.max_model_ram_mb,
        min_free_ram_mb=settings.resources.min_free_ram_mb, ram_margin_mb=catalog.ram_margin_mb,
        sandbox_memory_mb=settings.sandbox.limits.memory_mb, sandbox_cpus=settings.sandbox.limits.cpus,
    )  # fmt: skip


@router.get("/limits", response_model=LimitsOut, name="settings.limits")
def limits(request: Request, _: ProjectUser) -> LimitsOut:
    settings = _settings(request)
    raw = yaml.safe_load((_config_dir(settings) / "rate_limits.yaml").read_text(encoding="utf-8")) or {}
    return LimitsOut(
        rate_limits={k: raw.get(k) for k in ("max_wait_s", "agents", "services")},
        rag=settings.rag.model_dump(),
        context=settings.context.model_dump(),
        llm=settings.llm.model_dump(),
    )


@router.get("/evals", response_model=list[PromptStatusOut], name="settings.evals")
def evals(request: Request, _: ProjectUser) -> list[PromptStatusOut]:
    config = load_prompt_config(_config_dir(_settings(request)))
    root = code_root()
    rows = []
    for prompt_id, entry in sorted(config.prompts.items()):
        report, _md = report_paths(root, AgentRole(prompt_id.split(".", 1)[0]), entry.active)
        problem = check_report(report, prompt_hash=entry.released[entry.active])
        rows.append(
            PromptStatusOut(
                prompt_id=prompt_id, active_version=entry.active, eval_required=entry.eval_required,
                report="matches" if problem is None else ("none" if problem == "no report" else problem),
            )
        )  # fmt: skip
    return rows
