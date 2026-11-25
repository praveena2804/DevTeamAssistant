"""Model-call records and the model inventory (Part F; AC-F-01). Metadata only, never content."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from devteam_core.contracts.llm import ModelCall
from devteam_state.models import ModelCallRow, ModelInventoryRow


def record_model_call(session: Session, call: ModelCall, *, created_at: datetime) -> None:
    call = ModelCall.model_validate(call.model_dump())  # re-check the contract at the write boundary
    session.add(
        ModelCallRow(
            id=call.call_id,
            project_id=call.project_id,
            run_id=call.run_id,
            task_id=call.task_id,
            agent=call.agent.value if call.agent else None,
            service=call.service,
            kind=call.kind,
            operation_id=call.operation_id,
            slot=call.slot.value,
            model_tag=call.model_tag,
            model_digest=call.model_digest,
            route_rule=call.route_rule.value,
            recovery_step=call.recovery_step.value,
            prompt_id=call.prompt_id,
            prompt_version=call.prompt_version,
            prompt_hash=call.prompt_hash,
            input_hash=call.input_hash,
            schema_name=call.schema_name,
            cache_hit=call.cache_hit,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            duration_ms=call.duration_ms,
            free_ram_mb=call.free_ram_mb,
            outcome=call.outcome,
            error_code=call.error_code,
            attempt=call.attempt,
            created_at=created_at,
        )
    )


def _to_contract(row: ModelCallRow) -> ModelCall:
    return ModelCall.model_validate(
        {
            "call_id": row.id,
            "project_id": row.project_id,
            "run_id": row.run_id,
            "task_id": row.task_id,
            "agent": row.agent,
            "service": row.service,
            "kind": row.kind,
            "operation_id": row.operation_id,
            "slot": row.slot,
            "model_tag": row.model_tag,
            "model_digest": row.model_digest,
            "route_rule": row.route_rule,
            "recovery_step": row.recovery_step,
            "prompt_id": row.prompt_id,
            "prompt_version": row.prompt_version,
            "prompt_hash": row.prompt_hash,
            "input_hash": row.input_hash,
            "schema_name": row.schema_name,
            "cache_hit": row.cache_hit,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "duration_ms": row.duration_ms,
            "free_ram_mb": row.free_ram_mb,
            "outcome": row.outcome,
            "error_code": row.error_code,
            "attempt": row.attempt,
        }
    )


def calls_for_run(session: Session, project_id: str, run_id: str, *, limit: int = 2_000) -> list[ModelCall]:
    rows = session.scalars(
        select(ModelCallRow)
        .where(ModelCallRow.project_id == project_id, ModelCallRow.run_id == run_id)
        .order_by(ModelCallRow.created_at, ModelCallRow.id)
        .limit(limit)
    )
    return [_to_contract(r) for r in rows]


def calls_for_project(session: Session, project_id: str, *, limit: int = 2_000) -> list[ModelCall]:
    rows = session.scalars(
        select(ModelCallRow)
        .where(ModelCallRow.project_id == project_id)
        .order_by(ModelCallRow.created_at, ModelCallRow.id)
        .limit(limit)
    )
    return [_to_contract(r) for r in rows]


def count_calls(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(ModelCallRow)) or 0)


def replace_inventory(
    session: Session,
    installed: Mapping[str, str],
    configured: tuple[str, ...],
    *,
    server_version: str | None,
    checked_at: datetime,
) -> None:
    """Record what the local Ollama has, for every configured model (and nothing else)."""
    session.execute(delete(ModelInventoryRow))
    for tag in configured:
        digest = installed.get(tag.lower()) or installed.get(f"{tag.lower()}:latest")
        session.add(
            ModelInventoryRow(
                model_tag=tag,
                digest=digest or None,
                installed=digest is not None,
                server_version=server_version,
                checked_at=checked_at,
            )
        )


def inventory(session: Session) -> list[ModelInventoryRow]:
    return list(session.scalars(select(ModelInventoryRow).order_by(ModelInventoryRow.model_tag)))


__all__ = [
    "calls_for_project",
    "calls_for_run",
    "count_calls",
    "inventory",
    "record_model_call",
    "replace_inventory",
]
