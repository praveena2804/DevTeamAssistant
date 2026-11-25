"""Part F.1: model catalogue start-up rules (MODEL_ROUTING §3.3) and router precedence (GW-04, D-06, D-37)."""

from __future__ import annotations

import pytest

from devteam_core.config.models import load_model_catalog, parse_model_catalog
from devteam_core.contracts.llm import RecoveryStep, RouteRule
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.errors import ConfigError
from devteam_harness.errors import ResourceUnavailableError
from devteam_harness.llm_gateway.router import Router
from tests.fixtures.harness import REPO_ROOT

BASE = """
schema_version: 1
default_slot: instruct
models:
  big:3b: {approved: true, benchmark_ref: b.md, peak_ram_mb: 2100, num_ctx: 4096, reduced_num_ctx: 2048,
           timeouts: {connect_s: 5, read_s: 100, total_s: 100}}
  code:3b: {approved: true, benchmark_ref: b.md, peak_ram_mb: 2150, num_ctx: 4096,
            timeouts: {connect_s: 5, read_s: 100, total_s: 100}}
  small:1b: {approved: true, benchmark_ref: b.md, peak_ram_mb: 1200, num_ctx: 4096,
             timeouts: {connect_s: 5, read_s: 60, total_s: 60}}
  emb: {kind: embedding, approved: true, benchmark_ref: e.md, peak_ram_mb: 500, num_ctx: 2048,
        timeouts: {connect_s: 5, read_s: 30, total_s: 30}}
slots:
  instruct: {model: big:3b}
  coder: {model: code:3b}
  review: {model: null, inherit: coder}
  coder_large: {model: null, inherit: coder}
  fallback: {model: small:1b}
  embedding: {model: emb}
fallback_ladders: {instruct: [small:1b], coder: [small:1b]}
agents: AGENTS
operations: OPERATIONS
"""


def _catalog(agents: str = "{}", operations: str = "{}", **replace: str) -> str:
    text = BASE.replace("AGENTS", agents).replace("OPERATIONS", operations)
    for old, new in replace.items():
        text = text.replace(old, new)
    return text


def test_the_repository_catalogue_is_valid_and_routes_every_chat_slot() -> None:
    catalog = load_model_catalog(REPO_ROOT / "config", max_model_ram_mb=3072)
    router = Router(catalog)
    assert (
        router.resolve(AgentRole.BRD_ANALYST, "brd_analyst.analyse_section", ModelSlot.INSTRUCT).tag
        == "qwen2.5:3b"
    )
    review = router.resolve(AgentRole.DEBUGGER_REVIEWER, "debugger_reviewer.review_code", ModelSlot.REVIEW)
    assert review.tag == "qwen2.5-coder:3b" and review.rule is RouteRule.SLOT  # review inherits coder
    assert set(router.all_models()) == {"qwen2.5:3b", "qwen2.5-coder:3b", "qwen2.5:1.5b"}


def test_precedence_operation_then_agent_then_slot_then_global() -> None:
    router = Router(
        parse_model_catalog(
            _catalog(
                agents="{architect: {model: small:1b}}",
                operations="{architect.design: {slot: coder}}",
            )
        )
    )
    op = router.resolve(AgentRole.ARCHITECT, "architect.design", ModelSlot.INSTRUCT)
    assert (op.tag, op.rule) == ("code:3b", RouteRule.OPERATION)
    agent = router.resolve(AgentRole.ARCHITECT, "architect.other", ModelSlot.INSTRUCT)
    assert (agent.tag, agent.rule) == ("small:1b", RouteRule.AGENT)
    slot = router.resolve(AgentRole.TESTER, "tester.generate_tests", ModelSlot.CODER)
    assert (slot.tag, slot.rule) == ("code:3b", RouteRule.SLOT)


def test_recovery_rungs_reduced_context_then_smaller_models_only() -> None:
    router = Router(parse_model_catalog(_catalog()))
    choice = router.resolve(AgentRole.HEAD, "head.plan", ModelSlot.INSTRUCT)
    rungs = router.recovery(choice)
    assert [(r.tag, r.step, r.num_ctx) for r in rungs] == [
        ("big:3b", RecoveryStep.REDUCED_CONTEXT, 2048),
        ("small:1b", RecoveryStep.SMALLER_MODEL, None),
    ]
    coder = router.resolve(AgentRole.TESTER, "tester.generate_tests", ModelSlot.CODER)
    assert [r.step for r in router.recovery(coder)] == [RecoveryStep.SMALLER_MODEL]  # no reduced_num_ctx


def test_embedding_slot_and_fail_closed_when_null() -> None:
    assert Router(parse_model_catalog(_catalog())).embedding().tag == "emb"
    null = Router(parse_model_catalog(_catalog(**{"embedding: {model: emb}": "embedding: {model: null}"})))
    with pytest.raises(ResourceUnavailableError) as info:
        null.embedding()
    assert info.value.code == "rag.embedding_not_configured"
    with pytest.raises(ConfigError):
        null.resolve(AgentRole.HEAD, "head.plan", ModelSlot.EMBEDDING)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("instruct: {model: big:3b}", "instruct: {model: ghost:7b}", "rule 1"),
        ("agents: {}", "agents: {wizard: {slot: coder}}", "agents"),
        ("review: {model: null, inherit: coder}", "review: {model: null, inherit: review}", "cycle"),
        ("agents: {}", "agents: {head: {slot: coder, model: small:1b}}", "exactly one"),
        ("default_slot: instruct", "default_slot: instruct\ndefault_slot: coder", "duplicate"),
        ("big:3b: {approved: true", "big:3b: {approved: false", "rule 7"),
        ("peak_ram_mb: 2100", "peak_ram_mb: 9000", "rule 8"),
        ("instruct: [small:1b]", "instruct: [code:3b]", "rule 9"),
        ("coder: {model: code:3b}", "coder: {model: emb}", "needs a chat model"),
        ("agents: {}", "agents: {head: {slot: embedding}}", "embedding slot"),
        ("operations: {}", "operations: {}\nsurprise: 1", "surprise"),
    ],
)
def test_each_startup_rule_stops_start_up(old: str, new: str, message: str) -> None:
    text = _catalog()
    assert old in text, old
    with pytest.raises(ConfigError) as info:
        parse_model_catalog(text.replace(old, new, 1), max_model_ram_mb=3072)
    assert message in str(info.value), str(info.value)


def test_operation_overrides_must_be_declared_by_an_agent() -> None:
    router = Router(parse_model_catalog(_catalog(operations="{head.not_declared: {slot: coder}}")))
    with pytest.raises(ConfigError, match="rule 3"):
        router.check_operations({AgentRole.HEAD: ["head.plan"]})
    router.check_operations({AgentRole.HEAD: ["head.not_declared"]})  # declared → fine


def test_the_old_ollama_models_key_is_refused(tmp_path: object) -> None:
    from devteam_core.config import load_settings  # noqa: PLC0415

    with pytest.raises(ConfigError):
        load_settings(root=REPO_ROOT, environ={"DEVTEAM_OLLAMA__MODELS__INSTRUCT": "qwen2.5:7b"})
