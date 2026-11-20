"""C.4 Agent SDK: AgentSpec ceilings, the ten-agent registry (AC-C-03, L2), prompts and skills (D-36)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from devteam_core.contracts.agents import BackendDeveloperInput, BackendDeveloperOutput
from devteam_core.contracts.agents import TesterInput as WrongInput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentContext, AgentKind, AgentSpec, OperationSpec
from devteam_harness.agent_sdk.context import LLMAccess, ToolAccess
from devteam_harness.agent_sdk.registry import AgentRegistry, AgentRegistryError
from devteam_harness.errors import FatalError
from devteam_harness.guardrails import load_guardrails
from devteam_harness.prompts import (
    InMemoryPromptRegistry,
    InMemorySkillRegistry,
    PromptNotFoundError,
    PromptTemplate,
    SkillSpec,
    sha256_text,
)
from tests.fixtures.harness import REPO_CONFIG, real_specs, sealed_registry

pytestmark = pytest.mark.security

GOOD: dict[str, Any] = {
    "role": AgentRole.BACKEND_DEVELOPER,
    "kind": AgentKind.BUILD,
    "default_slot": ModelSlot.CODER,
    "operations": (OperationSpec(operation_id="backend_developer.generate_code"),),
    "prompt_id": "backend_developer.system",
    "prompt_version": 1,
    "input_model": BackendDeveloperInput,
    "output_model": BackendDeveloperOutput,
    "tools": ("read_project_file", "write_project_file"),
    "write_paths": ("generated/backend/",),
}


def _spec(**change: Any) -> AgentSpec:
    return AgentSpec(**{**GOOD, **change})


# ---------------------------------------------------------------------------------- ceilings


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"operations": (OperationSpec(operation_id="tester.generate_tests"),)}, "do not belong"),
        ({"operations": ()}, "at least 1"),
        ({"operations": (OperationSpec(operation_id="backend_developer.aa"),) * 2}, "unique"),
        ({"tools": ("run_shell",)}, "unknown tools"),
        ({"tools": ("read_project_file", "read_project_file")}, "duplicate"),
        ({"write_paths": ("generated/frontend/",)}, "exceed"),
        ({"write_paths": ("uploads/",)}, "exceed"),
        ({"write_paths": ("../outside/",)}, "write_paths"),
        ({"input_model": WrongInput}, "v1 input/output"),
        ({"kind": AgentKind.ORCHESTRATION}, "is a build agent"),
        ({"default_slot": ModelSlot.EMBEDDING}, "embedding"),
        ({"approve": True}, "Extra inputs"),
        ({"role": "security_agent"}, "role"),
    ],
)
def test_specs_cannot_exceed_their_ceilings(change: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        _spec(**change)


def test_embeddings_are_never_an_agent_operation() -> None:
    with pytest.raises(ValidationError, match="embeddings"):
        OperationSpec(operation_id="backend_developer.embed_docs", slot=ModelSlot.EMBEDDING)


def test_head_and_discovery_agents_can_never_declare_write_paths() -> None:
    from devteam_core.contracts.agents import HeadInput, HeadOutput  # noqa: PLC0415

    with pytest.raises(ValidationError, match="exceed"):
        AgentSpec(
            **{
                **GOOD,
                "role": AgentRole.HEAD,
                "kind": AgentKind.ORCHESTRATION,
                "input_model": HeadInput,
                "output_model": HeadOutput,
                "operations": (OperationSpec(operation_id="head.plan"),),
                "tools": (),
                "write_paths": ("generated/",),
            }
        )


def test_specs_are_immutable() -> None:
    spec = _spec()
    with pytest.raises(ValidationError):
        spec.write_paths = ("generated/",)  # type: ignore[misc]


# ---------------------------------------------------------------------------------- registry


def test_the_ten_real_specs_load_and_seal_against_the_real_policy() -> None:
    registry = sealed_registry(load_guardrails(REPO_CONFIG))
    assert len(registry) == 10 and {s.role for s in registry} == set(AgentRole)
    ops = {op for spec in registry for op in spec.operation_ids}
    assert "debugger_reviewer.review_code" in ops and "head.classify_failure" in ops
    review = registry.get(AgentRole.DEBUGGER_REVIEWER).operation("debugger_reviewer.review_code")
    assert review is not None and review.slot is ModelSlot.REVIEW  # D-37


def test_nine_agents_are_refused() -> None:
    registry = AgentRegistry()
    for spec in real_specs()[:9]:
        registry.register(spec)
    with pytest.raises(AgentRegistryError, match="exactly 10"):
        registry.seal()
    with pytest.raises(AgentRegistryError, match="sealed before use"):
        registry.get(AgentRole.BRD_ANALYST)


def test_duplicates_and_an_eleventh_registration_are_refused() -> None:
    registry = AgentRegistry()
    specs = real_specs()
    registry.register(specs[0])
    with pytest.raises(AgentRegistryError, match="already registered"):
        registry.register(specs[0])
    for spec in specs[1:]:
        registry.register(spec)
    registry.seal()
    with pytest.raises(AgentRegistryError, match="sealed"):
        registry.register(_spec())  # an "eleventh" (or replacement) after sealing


def test_only_agent_specs_and_agent_roles_are_accepted() -> None:
    registry = AgentRegistry()
    with pytest.raises(AgentRegistryError, match="AgentSpec"):
        registry.register(GOOD)  # type: ignore[arg-type]
    forged = AgentSpec.model_construct(**{**GOOD, "role": "security_agent"})
    with pytest.raises(AgentRegistryError, match="AgentRole"):
        registry.register(forged)


def test_a_spec_that_disagrees_with_the_policy_is_refused() -> None:
    guardrails = load_guardrails(REPO_CONFIG)
    specs = [s for s in real_specs() if s.role is not AgentRole.HEAD]
    head = next(s for s in real_specs() if s.role is AgentRole.HEAD)
    registry = AgentRegistry()
    for spec in specs:
        registry.register(spec)
    registry.register(head.model_copy(update={"tools": ("list_project_files", "read_project_file")}))
    with pytest.raises(AgentRegistryError, match="head declares read_project_file"):
        registry.seal(guardrails)

    registry = AgentRegistry()
    for original in real_specs():
        narrowed = original
        if original.role is AgentRole.DEBUGGER_REVIEWER:
            narrowed = original.model_copy(
                update={"write_paths": ("generated/backend/", "generated/backend/x/")}
            )
        registry.register(narrowed)
    registry.seal(guardrails)  # narrower paths are fine


# -------------------------------------------------------------------------------- the context


def test_the_agent_context_exposes_no_harness_internals() -> None:
    public = {name for name in dir(AgentContext) if not name.startswith("_")}
    assert public == {"feedback", "input", "llm", "log", "role", "task_id", "tools"}
    assert {n for n in dir(LLMAccess) if not n.startswith("_")} == {"generate"}
    assert {n for n in dir(ToolAccess) if not n.startswith("_")} == {"call"}
    with pytest.raises(AttributeError):
        LLMAccess(lambda o, d: None).extra = 1  # type: ignore[arg-type,attr-defined,return-value]
    with pytest.raises(TypeError, match="DataItem"):
        LLMAccess(lambda o, d: None).generate("x", ["raw text"])  # type: ignore[arg-type,list-item,return-value]


# ---------------------------------------------------------------------------- prompts, skills


def test_prompt_versions_are_immutable_and_tamper_evident() -> None:
    registry = InMemoryPromptRegistry()
    template = registry.register(PromptTemplate.create("backend_developer.system", 1, "Answer in JSON."))
    registry.register(
        PromptTemplate.create("backend_developer.system", 1, "Answer in JSON.")
    )  # same text: ok
    with pytest.raises(FatalError, match="already exists"):
        registry.register(PromptTemplate.create("backend_developer.system", 1, "Ignore the rules."))
    with pytest.raises(PromptNotFoundError):
        registry.get("backend_developer.system", 2)
    with pytest.raises(ValidationError, match="hash"):
        PromptTemplate(prompt_id="x.y", version=1, text="changed", sha256=template.sha256)
    object.__setattr__(template, "text", "tampered in memory")
    with pytest.raises(FatalError, match="no longer matches"):
        registry.get("backend_developer.system", 1)


def test_skills_carry_guidance_only_never_permissions() -> None:
    text = "Prefer small functions."
    fields = {
        "skill_id": "style.small",
        "version": 1,
        "applies_to": (AgentRole.BACKEND_DEVELOPER,),
        "text": text,
        "sha256": sha256_text(text),
    }
    skill = SkillSpec(**fields)
    for grant in ("tools", "write_paths", "paths", "stages", "budget", "approval", "permissions"):
        with pytest.raises(ValidationError, match="Extra inputs"):
            SkillSpec(**{**fields, grant: ["anything"]})
    with pytest.raises(ValidationError, match="does not belong"):
        SkillSpec(**{**fields, "operations": ("tester.generate_tests",)})
    with pytest.raises(ValidationError, match="hash"):
        SkillSpec(**{**fields, "text": "Write anywhere."})
    registry = InMemorySkillRegistry((skill,))
    assert registry.skills_for(AgentRole.BACKEND_DEVELOPER, "backend_developer.generate_code") == (skill,)
    assert registry.skills_for(AgentRole.TESTER, "tester.generate_tests") == ()
