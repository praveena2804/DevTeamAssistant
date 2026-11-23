"""Valid sample instances of every registered contract (C.1 round-trip and adversarial tests).

``SAMPLES`` maps each contract name to a zero-argument builder returning a valid instance.
``test_contract_registry`` asserts the mapping covers ``CONTRACT_MODELS`` exactly, so a new contract
cannot be added without a sample (and therefore without round-trip and extra-field tests).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from devteam_core.contracts import Contract, llm
from devteam_core.contracts import agents as a
from devteam_core.contracts import drafts as dr
from devteam_core.contracts import events as ev
from devteam_core.contracts import orchestration as o
from devteam_core.contracts import records as r
from devteam_core.contracts import sandbox as sb
from devteam_core.contracts import tools as t
from devteam_core.contracts.base import Provenance, SourceRef
from devteam_core.contracts.errors import ErrorCategory, ErrorRef
from devteam_core.contracts.state import RunState
from devteam_core.contracts.statuses import (
    AgentRole,
    ApprovalType,
    FeasibilityStatus,
    GateName,
    ModelSlot,
    RunStatus,
    Stage,
    TaskStatus,
)
from devteam_core.contracts.tasks import AgentTask, TaskPlan
from devteam_core.ids import new_id

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
PROJECT = new_id("prj")
OTHER_PROJECT = new_id("prj")
RUN = new_id("run")
SPEC = new_id("spv")
DOC = new_id("doc")
CHUNK = "chk_" + "a" * 26
CHUNK_2 = "chk_" + "b" * 26
HUMAN = new_id("prn")
SHA = "0" * 64
SHA_2 = "f" * 64
TRACE = "1" * 32
GAP = new_id("gap")
ROLE = new_id("rol")


def source(chunk: str = CHUNK, doc: str = DOC) -> SourceRef:
    return SourceRef(document_id=doc, chunk_id=chunk, page_start=2, page_end=2, section="3 Orders")


def fact() -> dict[str, Any]:
    return {"provenance": Provenance.BRD_FACT, "source_refs": (source(),)}


def requirement(key: str = "FR-001", project: str = PROJECT) -> r.Requirement:
    return r.Requirement(
        requirement_id=key,
        project_id=project,
        type=r.RequirementType.FUNCTIONAL if key.startswith("FR") else r.RequirementType.NON_FUNCTIONAL,
        description="Customers can place an order.",
        **fact(),
    )


def task(
    role: AgentRole = AgentRole.BACKEND_DEVELOPER,
    *,
    task_id: str | None = None,
    depends_on: tuple[str, ...] = (),
    requirement_ids: tuple[str, ...] = ("FR-001",),
    permitted_paths: tuple[str, ...] = ("backend/",),
    **overrides: Any,
) -> AgentTask:
    fields: dict[str, Any] = {
        "task_id": task_id or new_id("tsk"),
        "project_id": PROJECT,
        "run_id": RUN,
        "agent_type": role,
        "operation_id": f"{role.value}.implement",
        "objective": "Implement the order endpoint.",
        "requirement_ids": requirement_ids,
        "depends_on": depends_on,
        "permitted_paths": permitted_paths,
        "expected_artifacts": (),
        "done_criteria": ("Endpoint exists",),
        "spec_version_id": SPEC,
    }
    fields.update(overrides)
    return AgentTask(**fields)


def plan(*tasks: AgentTask) -> TaskPlan:
    return TaskPlan(
        plan_id=new_id("pln"), project_id=PROJECT, run_id=RUN, spec_version_id=SPEC, tasks=tasks or (task(),)
    )


def checklist(found: bool = True) -> tuple[a.ChecklistItem, ...]:
    return tuple(
        a.ChecklistItem(
            category=c,
            status=a.ChecklistStatus.FOUND if found else a.ChecklistStatus.MISSING_OR_UNCLEAR,
            source_refs=(source(),) if found else (),
        )
        for c in r.GapCategory
    )


def gap(**overrides: Any) -> r.Gap:
    fields: dict[str, Any] = {
        "gap_id": GAP,
        "project_id": PROJECT,
        "category": r.GapCategory.LOGIN,
        "kind": r.GapKind.MISSING,
        "criticality": r.Criticality.CRITICAL,
        "description": "The BRD does not say how users sign in.",
    }
    fields.update(overrides)
    return r.Gap(**fields)


def question(gap_id: str = GAP) -> r.ClarificationQuestion:
    return r.ClarificationQuestion(
        question_id=new_id("cq"),
        project_id=PROJECT,
        gap_id=gap_id,
        topic=r.GapCategory.LOGIN,
        prompt="How should users sign in?",
        options=("Email and password", "No login"),
        reason="Access control depends on it.",
        impact_rank=1,
    )


def analyst_input() -> a.BrdAnalystInput:
    return a.BrdAnalystInput(project_id=PROJECT, run_id=RUN, document_ids=(DOC,), chunk_ids=(CHUNK,))


def analyst_output(**overrides: Any) -> a.BrdAnalystOutput:
    fields: dict[str, Any] = {
        "project_id": PROJECT,
        "run_id": RUN,
        "requirements": (requirement(),),
        "checklist": checklist(),
    }
    fields.update(overrides)
    return a.BrdAnalystOutput(**fields)


def build_input(cls: type[a.BuildTaskInput], the_task: AgentTask, **extra: Any) -> a.BuildTaskInput:
    return cls(project_id=PROJECT, run_id=RUN, task=the_task, spec_version_id=SPEC, **extra)


def build_output(cls: type[a.BuildTaskOutput], the_task: AgentTask, **extra: Any) -> a.BuildTaskOutput:
    return cls(project_id=PROJECT, run_id=RUN, task_id=the_task.task_id, **extra)


def error_ref(category: ErrorCategory = ErrorCategory.POLICY_DENIED) -> ErrorRef:
    return ErrorRef(category=category, code="policy.denied", message="Not permitted.", occurred_at=NOW)


def model_request(**overrides: Any) -> llm.ModelRequest:
    fields: dict[str, Any] = {
        "request_id": new_id("mrq"),
        "role": AgentRole.BRD_ANALYST,
        "operation_id": "brd_analyst.extract_section",
        "slot": ModelSlot.INSTRUCT,
        "messages": (
            llm.ChatMessage(
                role=llm.ChatRole.SYSTEM, content="You extract requirements.", trust=llm.Trust.TRUSTED
            ),
            llm.ChatMessage(
                role=llm.ChatRole.USER,
                content="Orders ship in 2 days.",
                trust=llm.Trust.UNTRUSTED,
                label="brd_chunk",
            ),
        ),
        "output_schema": "BrdAnalystOutput",
        "prompt_id": "brd_analyst.extract",
        "prompt_version": 1,
        "prompt_hash": SHA,
    }
    fields.update(overrides)
    return llm.ModelRequest(**fields)


def tool_call(**overrides: Any) -> t.ToolCall:
    task_id = overrides.pop("task_id", new_id("tsk"))
    fields: dict[str, Any] = {
        "call_id": new_id("tcl"),
        "tool": "read_project_file",
        "tool_version": 1,
        "principal": t.agent_principal(AgentRole.BACKEND_DEVELOPER, task_id),
        "task_id": task_id,
        "args": {"path": "backend/app.py"},
    }
    fields.update(overrides)
    return t.ToolCall(**fields)


def _event_base() -> dict[str, Any]:
    return {
        "event_id": new_id("evt"),
        "ts": NOW,
        "project_id": PROJECT,
        "run_id": RUN,
        "actor": "service:executor",
    }


def _samples() -> dict[str, Callable[[], Contract]]:
    the_task = task()
    debug_task = task(AgentRole.DEBUGGER_REVIEWER)
    file_out = a.FileOutput(path="backend/app.py", sha256=SHA)
    return {
        "SourceRef": source,
        "ErrorRef": error_ref,
        "Requirement": requirement,
        "BusinessRule": lambda: r.BusinessRule(
            rule_id="BR-001", project_id=PROJECT, description="Orders over 100 need approval.", **fact()
        ),
        "Role": lambda: r.Role(role_id=ROLE, project_id=PROJECT, name="Customer", **fact()),
        "Permission": lambda: r.Permission(
            permission_id=new_id("prm"),
            project_id=PROJECT,
            role_id=ROLE,
            action="create",
            resource="order",
            **fact(),
        ),
        "EntityDefinition": lambda: r.EntityDefinition(
            entity_id=new_id("ent"),
            project_id=PROJECT,
            name="Order",
            attributes=(r.EntityAttribute(name="total", data_type="decimal", required=True),),
            relationships=(
                r.EntityRelationship(target_entity="Customer", kind=r.RelationshipKind.MANY_TO_ONE),
            ),
            **fact(),
        ),
        "EntityAttribute": lambda: r.EntityAttribute(name="total"),
        "EntityRelationship": lambda: r.EntityRelationship(
            target_entity="Customer", kind=r.RelationshipKind.MANY_TO_ONE
        ),
        "Workflow": lambda: r.Workflow(
            workflow_id=new_id("wfl"),
            project_id=PROJECT,
            name="Checkout",
            description="Buy.",
            steps=("Pay",),
            **fact(),
        ),
        "Integration": lambda: r.Integration(
            integration_id=new_id("itg"), project_id=PROJECT, name="Mail", description="Send mail.", **fact()
        ),
        "TechnologyNeed": lambda: r.TechnologyNeed(
            tech_id=new_id("tec"),
            project_id=PROJECT,
            layer=r.TechnologyLayer.BACKEND,
            name="FastAPI",
            **fact(),
        ),
        "AcceptanceCriterion": lambda: r.AcceptanceCriterion(
            criterion_id=new_id("acc"),
            project_id=PROJECT,
            requirement_ids=("FR-001",),
            description="OK.",
            **fact(),
        ),
        "Constraint": lambda: r.Constraint(
            constraint_id=new_id("con"),
            project_id=PROJECT,
            category=r.ConstraintCategory.SECURITY,
            description="HTTPS.",
            **fact(),
        ),
        "Gap": gap,
        "FeasibilityItem": lambda: r.FeasibilityItem(
            item_id=new_id("fea"),
            project_id=PROJECT,
            requirement_id="FR-001",
            status=FeasibilityStatus.SUPPORTED,
        ),
        "ClarificationQuestion": question,
        "ClarificationAnswer": lambda: r.ClarificationAnswer(
            answer_id=new_id("ca"),
            question_id=new_id("cq"),
            project_id=PROJECT,
            selected_options=("No login",),
            source_user=HUMAN,
        ),
        "SpecVersion": lambda: r.SpecVersion(
            version_id=SPEC,
            project_id=PROJECT,
            version=1,
            checksum=SHA,
            approved_by=HUMAN,
            approved_at=NOW,
            approval_id=new_id("apr"),
        ),
        "DecisionRecord": lambda: r.DecisionRecord(
            decision_id=new_id("dec"),
            project_id=PROJECT,
            decision="Use a blue theme.",
            provenance=Provenance.ASSUMPTION,
            rationale="Colours are a minor detail (Spec §5.4.2).",
            timestamp=NOW,
        ),
        "Artifact": lambda: r.Artifact(
            artifact_id=new_id("art"),
            project_id=PROJECT,
            path="backend/app.py",
            type=r.ArtifactType.SOURCE_FILE,
            version=1,
            checksum=SHA,
        ),
        "TestRun": lambda: r.TestRun(
            test_run_id=new_id("trn"),
            project_id=PROJECT,
            run_id=RUN,
            test_type=r.TestType.API,
            command="pytest_api",
            result=r.TestResult.PASSED,
            exit_code=0,
            passed=3,
            failed=0,
            skipped=0,
            errors=0,
            timestamp=NOW,
        ),
        "RequirementEvidence": lambda: r.RequirementEvidence(
            evidence_id=new_id("evd"), project_id=PROJECT, requirement_id="FR-001"
        ),
        "ChangeRequest": lambda: r.ChangeRequest(
            change_id=new_id("chg"), project_id=PROJECT, description="Add a field.", created_at=NOW
        ),
        "Approval": lambda: r.Approval(
            approval_id=new_id("apr"),
            project_id=PROJECT,
            run_id=RUN,
            approval_type=ApprovalType.SPEC_APPROVAL,
            spec_version_id=SPEC,
            spec_checksum=SHA,
            decision=r.ApprovalDecision.APPROVED,
            decided_by=HUMAN,
            decided_at=NOW,
        ),
        "SecretReference": lambda: r.SecretReference(
            secret_id=new_id("sec"), name="api token", ref="secret://platform/api_token", created_at=NOW
        ),
        "AgentTask": lambda: the_task,
        "TaskPlan": plan,
        "BrdAnalystInput": analyst_input,
        "BrdAnalystOutput": analyst_output,
        "RequirementsFinalizerInput": lambda: a.RequirementsFinalizerInput(
            project_id=PROJECT, run_id=RUN, gap_ids=(GAP,), critical_gap_ids=(GAP,)
        ),
        "RequirementsFinalizerOutput": lambda: a.RequirementsFinalizerOutput(
            project_id=PROJECT, run_id=RUN, questions=(question(),)
        ),
        "HeadInput": lambda: a.HeadInput(
            project_id=PROJECT, run_id=RUN, spec_version_id=SPEC, spec_checksum=SHA, in_scope_keys=("FR-001",)
        ),
        "HeadOutput": lambda: a.HeadOutput(project_id=PROJECT, run_id=RUN, plan=plan()),
        "ArchitectInput": lambda: build_input(
            a.ArchitectInput, task(AgentRole.ARCHITECT, permitted_paths=("docs/",))
        ),
        "ArchitectOutput": lambda: a.ArchitectOutput(
            project_id=PROJECT,
            run_id=RUN,
            task_id=the_task.task_id,
            openapi=a.FileOutput(path="contracts/openapi.json", sha256=SHA),
        ),
        "DatabaseDeveloperInput": lambda: build_input(
            a.DatabaseDeveloperInput, task(AgentRole.DATABASE_DEVELOPER)
        ),
        "DatabaseDeveloperOutput": lambda: build_output(a.DatabaseDeveloperOutput, the_task),
        "BackendDeveloperInput": lambda: build_input(a.BackendDeveloperInput, the_task),
        "BackendDeveloperOutput": lambda: build_output(a.BackendDeveloperOutput, the_task, files=(file_out,)),
        "FrontendDeveloperInput": lambda: build_input(
            a.FrontendDeveloperInput, task(AgentRole.FRONTEND_DEVELOPER)
        ),
        "FrontendDeveloperOutput": lambda: build_output(a.FrontendDeveloperOutput, the_task),
        "TesterInput": lambda: build_input(
            a.TesterInput, task(AgentRole.TESTER, permitted_paths=("tests/",))
        ),
        "TesterOutput": lambda: build_output(
            a.TesterOutput,
            the_task,
            test_mappings=(
                a.TestMapping(test_id="tests/test_orders.py::test_place", requirement_ids=("FR-001",)),
            ),
        ),
        "DebuggerReviewerInput": lambda: build_input(a.DebuggerReviewerInput, debug_task),
        "DebuggerReviewerOutput": lambda: build_output(
            a.DebuggerReviewerOutput,
            debug_task,
            root_cause="Missing null check.",
            review_findings=(
                a.ReviewFinding(severity=a.Severity.LOW, path="backend/app.py", description="Long function."),
            ),
        ),
        "DocumenterInput": lambda: a.DocumenterInput(
            project_id=PROJECT,
            run_id=RUN,
            spec_version_id=SPEC,
            task=task(AgentRole.DOCUMENTER, operation_id="documenter.write_docs", permitted_paths=("docs/",)),
        ),
        "DocumenterOutput": lambda: a.DocumenterOutput(
            project_id=PROJECT,
            run_id=RUN,
            task_id=the_task.task_id,
            documents=(a.FileOutput(path="README.md", sha256=SHA),),
        ),
        "ChecklistItem": lambda: checklist()[0],
        "FileOutput": lambda: file_out,
        "ExecutionRequest": lambda: a.ExecutionRequest(
            kind=a.ExecutionKind.TEST_RUN, requirement_ids=("FR-001",)
        ),
        "RequirementMapping": lambda: a.RequirementMapping(
            requirement_id="FR-001", paths=("backend/app.py",)
        ),
        "Blocker": lambda: a.Blocker(description="Payment provider not in catalog."),
        "RoutingDecision": lambda: a.RoutingDecision(
            task_id=the_task.task_id,
            failure_ref=new_id("trn"),
            owner=AgentRole.BACKEND_DEVELOPER,
            method=a.RoutingMethod.RULES,
            reason="Failing test is under backend/.",
        ),
        "TestMapping": lambda: a.TestMapping(test_id="tests/test_x.py::test_y", requirement_ids=("FR-001",)),
        "ReviewFinding": lambda: a.ReviewFinding(
            severity=a.Severity.INFO, path="backend/app.py", description="Ok."
        ),
        "ToolCall": tool_call,
        "ToolResult": lambda: t.ToolResult(
            call_id=new_id("tcl"), outcome=t.ToolOutcome.OK, value={"bytes": 10}, audit_event_id=new_id("aud")
        ),
        "ChatMessage": lambda: llm.ChatMessage(role=llm.ChatRole.USER, content="hi", trust=llm.Trust.TRUSTED),
        "ModelParams": llm.ModelParams,
        "ModelRequest": model_request,
        "ModelResponse": lambda: llm.ModelResponse(
            request_id=new_id("mrq"),
            provider=llm.Provider.FAKE,
            model_id="fake-model",
            output_text='{"ok": true}',
            parsed={"ok": True},
            usage=llm.ModelUsage(input_tokens=100, output_tokens=20),
            duration_ms=5,
            validation=llm.OutputValidation(valid=True),
        ),
        "OutputValidation": lambda: llm.OutputValidation(valid=False, errors=("missing field",)),
        "ModelUsage": lambda: llm.ModelUsage(input_tokens=1, output_tokens=2),
        "RunTransitioned": lambda: ev.RunTransitioned(
            **_event_base(), from_status=RunStatus.CREATED, to_status=RunStatus.RUNNING
        ),
        "TaskTransitioned": lambda: ev.TaskTransitioned(
            **_event_base(), from_status=TaskStatus.PENDING, to_status=TaskStatus.READY
        ),
        "ToolCallRecorded": lambda: ev.ToolCallRecorded(
            **_event_base(),
            tool="read_project_file",
            tool_version=1,
            outcome=t.ToolOutcome.DENIED,
            args_summary={"path": "../secret"},
            policy_rule_id="default_deny",
            duration_ms=1,
        ),
        "ModelCallRecorded": lambda: ev.ModelCallRecorded(
            **_event_base(),
            provider=llm.Provider.FAKE,
            model_id="fake-model",
            operation_id="brd_analyst.extract_section",
            prompt_id="brd_analyst.extract",
            prompt_version=1,
            prompt_hash=SHA,
            input_tokens=10,
            output_tokens=5,
            duration_ms=3,
            output_valid=True,
            attempt=1,
        ),
        "ApprovalRecorded": lambda: ev.ApprovalRecorded(
            **{**_event_base(), "actor": HUMAN},
            approval_id=new_id("apr"),
            approval_type=ApprovalType.SPEC_APPROVAL,
            decision=r.ApprovalDecision.APPROVED,
        ),
        "FileChanged": lambda: ev.FileChanged(
            **_event_base(), path="backend/app.py", before_sha256=SHA, after_sha256=SHA_2, bytes_written=12
        ),
        "RunState": lambda: RunState(
            project_id=PROJECT,
            run_id=RUN,
            run_kind="initial",
            stage=Stage.ANALYSIS,
            trace_id=TRACE,
            run_mode=o.RunMode.STUB,
        ),
        # ---- Part D.1 orchestration contracts and progress events
        "NodeStarted": lambda: ev.NodeStarted(
            **_event_base(), node=o.NodeName.BRD_ANALYST, node_kind=o.NodeKind.AGENT, run_mode=o.RunMode.STUB
        ),
        "NodeFinished": lambda: ev.NodeFinished(
            **_event_base(),
            node=o.NodeName.FEASIBILITY,
            node_kind=o.NodeKind.SERVICE,
            outcome="ok",
            duration_ms=4,
        ),
        "GateOpened": lambda: ev.GateOpened(**_event_base(), gate=GateName.PROJECT_APPROVAL),
        "RunProgress": lambda: ev.RunProgress(
            **_event_base(),
            stage=Stage.BUILD,
            status=RunStatus.RUNNING,
            run_mode=o.RunMode.STUB,
            current_node=o.NodeName.ARCHITECT,
            completed_tasks=1,
            total_tasks=5,
        ),
        "MetricSample": lambda: ev.MetricSample(**_event_base(), metric="free_ram_mb", value=2048),
        "GateDecision": lambda: o.GateDecision(
            run_id=RUN, gate=GateName.PROJECT_APPROVAL, decision=r.ApprovalDecision.APPROVED
        ),
        "GateResume": lambda: o.GateResume(
            run_id=RUN, gate=GateName.PROJECT_APPROVAL, approval_id=new_id("apr")
        ),
        "DispatchRecord": _dispatch,
        "PlanIssue": lambda: o.PlanIssue(code=o.PlanIssueCode.CYCLE, detail="tasks form a cycle"),
        "PlanValidationReport": lambda: o.PlanValidationReport(
            plan_id=new_id("pln"),
            run_id=RUN,
            ok=False,
            issues=(o.PlanIssue(code=o.PlanIssueCode.UNCOVERED_REQUIREMENT, detail="FR-002 has no task"),),
        ),
        # ---- Part D.2
        "ServiceStepRecorded": lambda: ev.ServiceStepRecorded(
            **_event_base(),
            node=o.NodeName.PACKAGER,
            status=o.ServiceOutcomeStatus.NOT_BUILT,
            stand_in=True,
            available_in_part="O",
        ),
        "RunSummaryRecorded": lambda: ev.RunSummaryRecorded(
            **_event_base(),
            status=RunStatus.COMPLETED,
            run_mode=o.RunMode.STUB,
            completed_tasks=5,
            total_tasks=5,
            model_calls=9,
            stand_in_steps=7,
        ),
        "ChecklistFinding": lambda: dr.ChecklistFinding(
            category=r.GapCategory.GOAL, found=True, evidence=(1,)
        ),
        "RequirementDraft": lambda: dr.RequirementDraft(
            kind="functional", description="Lend books", evidence=2
        ),
        "AnalysisDraft": lambda: dr.AnalysisDraft(
            checklist=(dr.ChecklistFinding(category=r.GapCategory.GOAL, found=False),),
            requirements=(dr.RequirementDraft(kind="non_functional", description="Fast", evidence=1),),
        ),
        "ScopeDraft": lambda: dr.ScopeDraft(note="All accepted keys stay in scope."),
        "QuestionsDraft": lambda: dr.QuestionsDraft(questions=("Who may borrow books?",)),
        "SummaryDraft": lambda: dr.SummaryDraft(summary="Build five components."),
        "DocumentDraft": lambda: dr.DocumentDraft(title="Design", content="Notes."),
        "RepairDraft": lambda: dr.RepairDraft(root_cause="A missing import.", content="Add it."),
        "ServiceOutcome": lambda: o.ServiceOutcome(
            project_id=PROJECT,
            run_id=RUN,
            node=o.NodeName.TEST_RUNNER,
            status=o.ServiceOutcomeStatus.NOT_RUN,
            stand_in=True,
            reason="no sandbox yet: nothing was run",
            available_in_part="M",
            recorded_at=NOW,
        ),
        **_sandbox_samples(),
    }


_LIMITS = {
    "memory_mb": 1536,
    "cpus": 1.5,
    "pids": 256,
    "wall_clock_s": 600,
    "tmpfs_mb": 256,
    "work_copy_mb": 512,
    "output_kb": 1024,
}
_DIGEST = "sha256:" + "a" * 64


def _counts() -> sb.TestCounts:
    return sb.TestCounts(tests=3, passed=3, failed=0, errors=0, skipped=0)


def _outcome() -> sb.RecipeOutcome:
    return sb.RecipeOutcome(
        recipe="pytest_api",
        status=sb.SandboxExecutionStatus.COMPLETED,
        execution_id=new_id("sbx"),
        exit_code=0,
        tests=_counts(),
        reports_tests=True,
        duration_ms=1200,
        peak_memory_mb=180.5,
    )


def _sandbox_samples() -> dict[str, Callable[[], Contract]]:
    return {
        "ContainerLimits": lambda: sb.ContainerLimits(**_LIMITS),
        "TestCounts": _counts,
        "RecipeSpec": lambda: sb.RecipeSpec(
            recipe="pytest_api",
            image="python_api",
            argv=("python", "-m", "pytest", "tests"),
            workdir="/work/backend",
            env={"CI": "1"},
            timeout_s=300,
            reports_tests=True,
            outputs=("/work/.devteam/out/junit.xml",),
            tool="request_test_run",
        ),
        "SandboxExecution": lambda: sb.SandboxExecution(
            execution_id=new_id("sbx"),
            project_id=PROJECT,
            run_id=RUN,
            recipe="pytest_api",
            image="devteam-sandbox/python_api",
            image_digest=_DIGEST,
            engine_version="29.1.3",
            limits=sb.ContainerLimits(**_LIMITS),
            status=sb.SandboxExecutionStatus.COMPLETED,
            exit_code=0,
            duration_ms=1200,
            peak_memory_mb=180.5,
            tests=_counts(),
            outputs=("runs/x/sandbox/junit.xml",),
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
        ),
        "RecipeOutcome": _outcome,
        "TemplateVerification": lambda: sb.TemplateVerification(
            verification_id=new_id("tvf"),
            template_id="react_fastapi_sqlite",
            template_version="1.0.0",
            template_checksum=SHA,
            status=sb.TemplateVerificationStatus.PASSED,
            recipes=(_outcome(),),
            image_digests={"python_api": _DIGEST},
            duration_ms=60_000,
            peak_memory_mb=400.0,
            requested_by="owner",
            requested_at=NOW,
            finished_at=NOW + timedelta(minutes=1),
        ),
        "TemplateDependencies": lambda: sb.TemplateDependencies(
            python=("fastapi==0.120.0",), node=("react==19.2.0", "@vitejs/plugin-react==5.1.0")
        ),
        "TemplateManifest": lambda: sb.TemplateManifest(
            template_id="html_fastapi_sqlite",
            version="1.0.0",
            description="Plain HTML frontend with a FastAPI backend and SQLite.",
            frontend="html",
            backend="fastapi",
            database="sqlite",
            auth="none",
            writable_slots=("backend/app/api/**",),
            tester_slots=("backend/tests/**",),
            protected_files=("backend/app/main.py",),
            self_test=("ruff_check", "pytest_api"),
            dependencies=sb.TemplateDependencies(python=("fastapi==0.120.0",)),
            app_runtime_dependencies=("fastapi",),
            env_placeholders={"DATABASE_URL": "sqlite:///./data/app.db"},
        ),
        "MigrationCheckRequest": lambda: sb.MigrationCheckRequest(recipe="alembic_upgrade"),
    }


def _dispatch() -> o.DispatchRecord:
    task = new_id("tsk")
    return o.DispatchRecord(
        project_id=PROJECT,
        run_id=RUN,
        task_id=task,
        agent_type=AgentRole.ARCHITECT,
        node=o.NodeName.ARCHITECT,
        attempt=1,
        idempotency_key=o.attempt_key(RUN, "architect", task, 1),
        permitted_paths=("generated/docs/",),
        done_criteria=("architecture document written",),
        dispatched_at=NOW,
    )


SAMPLES: dict[str, Callable[[], Contract]] = _samples()
LATER = NOW + timedelta(minutes=5)
