"""Part F.1 live probe (``llm``: real local Ollama, never in CI): a schema-constrained call through the
runtime's gateway is routed by ``config/models.yaml``, answers the probe schema, and is recorded as a
``ModelCall`` with the installed model's digest and no content."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from devteam_core.contracts.llm import (
    ChatMessage,
    ChatRole,
    ModelParams,
    ModelRequest,
    ModelUsage,
    RouteRule,
    Trust,
)
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.ids import new_id
from devteam_harness.budgets.jobs import JobBudget, JobLimits
from devteam_harness.prompts import sha256_text
from devteam_state.models import ModelCallRow
from tests.fixtures.orchestration import make_env

pytestmark = pytest.mark.llm
SYSTEM = "Reply only with JSON that matches the given schema. Use small whole numbers."


def test_a_routed_schema_constrained_call_is_answered_and_recorded(tmp_path: Path) -> None:
    env = make_env(tmp_path, with_graph=False)
    try:
        gateway = env.rt.gateway
        assert gateway is not None
        request = ModelRequest(
            request_id=new_id("mrq"),
            role=AgentRole.HEAD,
            operation_id="head.plan",
            slot=ModelSlot.FALLBACK,  # the smallest approved model keeps the probe quick
            messages=(
                ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM, trust=Trust.TRUSTED),
                ChatMessage(
                    role=ChatRole.USER, content="input_tokens 3, output_tokens 4", trust=Trust.TRUSTED
                ),
            ),
            output_schema="ModelUsage",
            params=ModelParams(max_output_tokens=128, context_window=2048, seed=7),
            prompt_id="probe.system",
            prompt_version=1,
            prompt_hash=sha256_text(SYSTEM),
        )
        budget = JobBudget(
            JobLimits(max_model_calls=4, max_tokens=20_000, max_minutes=10, model_call_timeout_s=180,
                      min_free_ram_mb=0),
            project_id=None,
        )  # fmt: skip
        response = gateway.generate(
            request, budget=budget, principal_id="service:evaluator", output_model=ModelUsage
        )
        assert response.validation.valid, response.validation.errors
        assert response.route_rule is RouteRule.SLOT and response.model_id == "qwen2.5:1.5b"
        assert response.model_digest is not None and len(response.model_digest) == 64
        with env.sessions() as session:
            [row] = list(session.scalars(select(ModelCallRow)))
        assert (row.service, row.model_tag, row.model_digest, row.outcome) == (
            "evaluator", "qwen2.5:1.5b", response.model_digest, "valid"
        )  # fmt: skip
        assert SYSTEM not in json.dumps([str(v) for v in row.__dict__.values()])
    finally:
        env.close()
