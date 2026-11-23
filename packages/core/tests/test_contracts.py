"""C.1 contract tests: registry completeness, JSON Schema snapshots, round-trips (AC-C-03)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import pkgutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import devteam_core.contracts as contracts_pkg
from devteam_core.contracts import (
    AGENT_IO,
    CONTRACT_MODELS,
    EVENT_ADAPTER,
    SCHEMA_DIR,
    Contract,
    export_json_schemas,
    render_schema,
)
from devteam_core.contracts.agents import AgentIO, BuildTaskInput, BuildTaskOutput
from devteam_core.contracts.base import NeverAssumed, Sourced
from devteam_core.contracts.events import EventBase
from devteam_core.contracts.statuses import (
    BUILD_PLAN_ROLES,
    DISCOVERY_ROLES,
    EXPECTED_AGENT_COUNT,
    AgentRole,
    ProjectVerificationStatus,
    TaskStatus,
)
from tests.fixtures.contract_samples import SAMPLES

pytestmark = pytest.mark.contract

ABSTRACT_BASES = {Contract, Sourced, NeverAssumed, AgentIO, BuildTaskInput, BuildTaskOutput, EventBase}

CANONICAL_AGENTS = (
    "brd_analyst",
    "requirements_finalizer",
    "head",
    "architect",
    "database_developer",
    "backend_developer",
    "frontend_developer",
    "tester",
    "debugger_reviewer",
    "documenter",
)


def _all_contract_classes() -> set[type[Contract]]:
    for info in pkgutil.iter_modules(contracts_pkg.__path__):
        importlib.import_module(f"devteam_core.contracts.{info.name}")
    found: set[type[Contract]] = set()
    stack = [Contract]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            if sub.__module__.startswith("devteam_core.contracts") and sub not in found:
                found.add(sub)
                stack.append(sub)
    return found


def test_every_concrete_contract_is_registered() -> None:
    concrete = {c for c in _all_contract_classes() if c not in ABSTRACT_BASES}
    assert {c.__name__ for c in concrete} == set(CONTRACT_MODELS)


def test_every_registered_contract_has_a_sample() -> None:
    assert set(SAMPLES) == set(CONTRACT_MODELS)


@pytest.mark.parametrize("name", sorted(CONTRACT_MODELS))
def test_round_trip_json(name: str) -> None:
    model = CONTRACT_MODELS[name]
    instance = SAMPLES[name]()
    assert type(instance) is model
    restored = model.model_validate_json(instance.model_dump_json())
    assert restored == instance
    assert restored.schema_version == 1


@pytest.mark.parametrize("name", sorted(CONTRACT_MODELS))
def test_schema_forbids_extra_fields_and_is_versioned(name: str) -> None:
    schema = CONTRACT_MODELS[name].model_json_schema()
    assert schema.get("additionalProperties") is False
    assert schema["properties"]["schema_version"]["const"] == 1


@pytest.mark.parametrize("name", sorted(CONTRACT_MODELS))
def test_contracts_are_immutable(name: str) -> None:
    instance = SAMPLES[name]()
    with pytest.raises(ValidationError):
        instance.schema_version = 2  # type: ignore[misc,assignment]


def test_schema_snapshots_are_up_to_date() -> None:
    """The committed schema files equal the code (re-export with scripts/export_contract_schemas.py)."""
    wanted = {name: render_schema(schema) for name, schema in export_json_schemas().items()}
    on_disk = {p.name: p.read_text(encoding="utf-8") for p in SCHEMA_DIR.glob("*.json")}
    assert set(on_disk) == set(wanted), "schema files missing or orphaned"
    stale = [name for name, text in wanted.items() if on_disk[name] != text]
    assert not stale, f"stale schema snapshots: {stale}"
    for text in on_disk.values():
        json.loads(text)


def test_exactly_ten_agents_with_canonical_names() -> None:
    assert len(AgentRole) == EXPECTED_AGENT_COUNT == 10
    assert tuple(r.value for r in AgentRole) == CANONICAL_AGENTS


def test_every_agent_has_distinct_input_and_output_contracts() -> None:
    assert set(AGENT_IO) == set(AgentRole)
    models = [m for pair in AGENT_IO.values() for m in pair]
    assert len(models) == len(set(models)) == 20
    for role, (given, produced) in AGENT_IO.items():
        assert given.__name__.endswith("Input"), role
        assert produced.__name__.endswith("Output"), role


def test_role_groups_are_consistent() -> None:
    assert AgentRole.HEAD not in BUILD_PLAN_ROLES
    assert AgentRole.DOCUMENTER not in BUILD_PLAN_ROLES
    assert not DISCOVERY_ROLES & BUILD_PLAN_ROLES


def test_status_lists_match_the_spec() -> None:
    assert len(TaskStatus) == 9  # Spec §20.2
    assert len(ProjectVerificationStatus) == 8  # Spec §18.1


def test_no_agent_output_can_express_a_project_status() -> None:
    """No output contract references ProjectVerificationStatus (only the Completion Gate sets it)."""
    for _, produced in AGENT_IO.values():
        text = json.dumps(produced.model_json_schema())
        assert "ProjectVerificationStatus" not in text
        for value in ("requirements_verified", "tests_passed", "build_passed"):
            assert f'"{value}"' not in text


def test_event_union_parses_by_kind() -> None:
    for name in ("RunTransitioned", "ToolCallRecorded", "ModelCallRecorded", "FileChanged"):
        event = SAMPLES[name]()
        assert type(EVENT_ADAPTER.validate_json(event.model_dump_json())) is type(event)


def test_export_script_check_mode(capsys: pytest.CaptureFixture[str]) -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "export_contract_schemas.py"
    spec = importlib.util.spec_from_file_location("script_export_contract_schemas", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0
    assert "0 stale; 0 orphaned" in capsys.readouterr().out
