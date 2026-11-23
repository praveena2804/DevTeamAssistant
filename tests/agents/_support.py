"""Shared inputs for the Head Agent core tests (D.2): real write scopes and a plan context."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from itertools import count
from typing import Any

from devteam_agents.head.validator import PlanContext
from devteam_core.contracts.statuses import AgentRole
from devteam_core.ids import new_id


def real_write_scopes() -> dict[AgentRole, tuple[str, ...]]:
    """Each agent's declared write scope, read from its own ``AgentSpec`` (as orchestration will)."""
    scopes: dict[AgentRole, tuple[str, ...]] = {}
    for role in AgentRole:
        spec: Any = importlib.import_module(f"devteam_agents.{role.value}.spec").SPEC
        scopes[role] = tuple(spec.write_paths)
    return scopes


WRITE_SCOPES = real_write_scopes()
KEYS = frozenset({"FR-001", "FR-002", "NFR-001", "BR-001"})


def context(**overrides: Any) -> PlanContext:
    values: dict[str, Any] = {
        "plan_id": new_id("pln"),
        "project_id": new_id("prj"),
        "run_id": new_id("run"),
        "spec_version_id": new_id("spv"),
        "in_scope_keys": KEYS,
        "write_scopes": WRITE_SCOPES,
    }
    values.update(overrides)
    return PlanContext(**values)


def sequential_ids() -> Iterator[str]:
    """Deterministic, valid task IDs (``tsk_`` + 26 Crockford base32 characters)."""
    for n in count(1):
        yield f"tsk_{n:026d}"


def id_factory() -> Any:
    numbers = sequential_ids()

    def make(prefix: str) -> str:
        assert prefix == "tsk"
        return next(numbers)

    return make
