"""D.2 runtime: the production harness uses the real Ollama provider, ten agents, no fake provider."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from devteam_core.contracts import CONTRACT_MODELS
from devteam_core.contracts.agents import BrdAnalystInput, DebuggerReviewerInput
from devteam_core.contracts.llm import Provider
from devteam_core.contracts.statuses import AgentRole, ModelSlot, Stage, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from devteam_orchestration.prompts import build_prompt_registry
from devteam_orchestration.runtime import RuntimeConfigError, build_runtime, check_models
from devteam_orchestration.safety import CloudTracingConfiguredError
from devteam_orchestration.steps import ResourceWait, ensure_task, run_task, task_row_status
from devteam_state.repositories import documents
from tests.fixtures.harness import REPO_ROOT
from tests.fixtures.orchestration import Env, assume_free_ram, closed_port, make_env


@pytest.fixture
def offline(tmp_path: Path) -> Iterator[Env]:
    env = make_env(tmp_path, ollama_url=f"http://127.0.0.1:{closed_port()}")
    assume_free_ram(env)  # these tests are about an unreachable model, not about this host's memory
    yield env
    env.close()


def test_the_runtime_has_exactly_ten_agents_each_with_a_behaviour(offline: Env) -> None:
    rt = offline.rt
    assert rt.registry.sealed
    assert {spec.role for spec in rt.registry} == set(AgentRole)
    assert len(rt.registry) == 10
    assert set(rt.write_scopes) == set(AgentRole)
    assert rt.write_scopes[AgentRole.HEAD] == ()


def test_the_only_provider_is_the_local_ollama(offline: Env) -> None:
    assert offline.rt.provider.name is Provider.OLLAMA
    assert offline.rt.provider.model_for(ModelSlot.INSTRUCT) == "qwen2.5:3b"


def test_every_operation_names_a_registered_answer_contract_and_every_agent_a_prompt(offline: Env) -> None:
    prompts, _skills = build_prompt_registry(REPO_ROOT / "config")
    active = prompts.active()
    for spec in offline.rt.registry:
        assert spec.prompt_id in active  # every agent has an active, verified prompt file (Part F, D-58)
        assert prompts.get(spec.prompt_id, spec.prompt_version).sha256 == active[spec.prompt_id].sha256
        for op in spec.operations:
            assert op.output_schema in CONTRACT_MODELS, op.operation_id


def test_the_runtime_refuses_cloud_tracing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(tmp_path / "a", ollama_url=f"http://127.0.0.1:{closed_port()}")
    env.close()
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with pytest.raises(CloudTracingConfiguredError):
        build_runtime(env.settings, env.sessions)


def test_an_unreachable_model_is_reported_not_simulated(offline: Env) -> None:
    check = check_models(offline.rt)
    assert not check.ok
    assert check.problem is not None and "not reachable" in check.problem


def test_an_agent_step_without_a_model_waits_and_completes_nothing(offline: Env) -> None:
    env = offline
    with env.sessions() as session:
        doc = documents.list_for_project(session, env.project_id)[0]
        chunks, _ = documents.list_chunks(session, env.project_id, doc.id, offset=0, limit=3)

    def build(task_id: str) -> AgentTask:
        return AgentTask(
            task_id=task_id,
            project_id=env.project_id,
            run_id=env.run_id,
            agent_type=AgentRole.BRD_ANALYST,
            operation_id="brd_analyst.analyse_section",
            objective="Analyse",
            done_criteria=("done",),
        )

    task_id = ensure_task(env.rt, node="brd_analyst", scope="analysis", build=build)
    assert ensure_task(env.rt, node="brd_analyst", scope="analysis", build=build) == task_id  # created once
    given = BrdAnalystInput(
        project_id=env.project_id,
        run_id=env.run_id,
        document_ids=(doc.id,),
        chunk_ids=tuple(c.id for c in chunks),
    )
    with pytest.raises(ResourceWait) as caught:
        run_task(
            env.rt,
            project_id=env.project_id,
            task_id=task_id,
            stage=Stage.ANALYSIS,
            given=given,
            spec_approved=False,
        )
    assert caught.value.code == "ollama.unreachable"
    assert task_row_status(env.rt, env.project_id, task_id) is TaskStatus.READY  # not failed, not completed


def test_orchestration_and_the_worker_never_load_the_fake_provider() -> None:
    code = (
        "import sys; import devteam_orchestration.runtime, devteam_orchestration.steps, devteam_worker; "
        "print('devteam_harness.llm_gateway.providers.fake' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO_ROOT
    )
    assert result.stdout.strip() == "False"
    control = subprocess.run(
        [
            sys.executable,
            "-c",
            code.replace("devteam_worker;", "devteam_worker, devteam_harness.llm_gateway.providers.fake;"),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    assert control.stdout.strip() == "True"  # negative control: the check sees a loaded module


def test_a_missing_behaviour_fails_closed(offline: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from devteam_agents import tester  # noqa: PLC0415

    monkeypatch.delattr(tester, "BEHAVIOUR")
    with pytest.raises(RuntimeConfigError, match="tester has no behaviour"):
        build_runtime(offline.settings, offline.sessions)


# ------------------------------------------------------------------------------ real local model


@pytest.mark.llm
def test_the_debugger_repairs_through_the_real_gateway(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    try:

        def build(task_id: str) -> AgentTask:
            return AgentTask(
                task_id=task_id,
                project_id=env.project_id,
                run_id=env.run_id,
                agent_type=AgentRole.DEBUGGER_REVIEWER,
                operation_id="debugger_reviewer.repair",
                objective="Loan creation returns HTTP 500 when the member already has five loans",
                requirement_ids=("FR-002",),
                permitted_paths=("generated/backend/",),
                done_criteria=("repair notes written",),
                spec_version_id="spv_" + "0" * 26,
            )

        task_id = ensure_task(env.rt, node="debugger_reviewer", scope="repair_1", build=build)
        from devteam_orchestration.steps import load_task  # noqa: PLC0415

        given = DebuggerReviewerInput(
            project_id=env.project_id,
            run_id=env.run_id,
            task=load_task(env.rt, env.project_id, task_id),
            spec_version_id="spv_" + "0" * 26,
        )
        outcome = run_task(
            env.rt,
            project_id=env.project_id,
            task_id=task_id,
            stage=Stage.BUILD,
            given=given,
            spec_approved=True,
        )
        assert outcome.status is TaskStatus.COMPLETED, outcome.error
        assert outcome.output is not None
        assert outcome.output["root_cause"]
        [written] = outcome.output["files"]
        assert written["path"] == "generated/backend/REPAIR_NOTES.md"
        text = (env.rt.workspace(env.project_id) / written["path"]).read_text(encoding="utf-8")
        assert text.startswith("> Written by the Debugger & Code Reviewer agent with a local AI model")
    finally:
        env.close()


def test_an_invalid_capability_catalog_fails_closed(offline: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Part G (AC-G-02): the runtime (and so the worker) refuses to start with an invalid catalog."""
    from devteam_orchestration import runtime  # noqa: PLC0415
    from devteam_services.catalog import CatalogError  # noqa: PLC0415

    def broken() -> None:
        raise CatalogError("capabilities.yaml is invalid at entries.0.key: not a CatalogKey")

    assert offline.rt.catalog is not None  # control: the shipped catalog is loaded
    monkeypatch.setattr(runtime, "load_catalog", broken)
    with pytest.raises(RuntimeConfigError, match="not a CatalogKey"):
        build_runtime(offline.settings, offline.sessions)
