"""Part F.4 (HC-09, AC-F-05, D-56): deterministic scorers, dataset loading, the runner (through a gateway
double; the real-model run is TR-0139), and the CI eval gate with negative controls."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from devteam_core.contracts import CONTRACT_MODELS
from devteam_core.contracts.statuses import AgentRole
from devteam_harness.budgets.jobs import JobBudget, JobLimits
from devteam_harness.errors import HarnessError, ResourceUnavailableError
from devteam_harness.evaluation import (
    Expected,
    Runner,
    Scorecard,
    check_report,
    load_dataset,
    score,
    write_report,
)
from devteam_harness.guardrails import load_guardrails
from devteam_harness.guardrails.input import InputGuard
from devteam_harness.prompts.files import build_file_registries
from tests.fixtures.harness import REPO_ROOT

pytestmark = pytest.mark.security
GATE = REPO_ROOT / "scripts" / "check_eval_gate.py"


# ---------------------------------------------------------------------------------------- scorers


def test_scores_detect_bad_citations_missing_terms_and_invented_technology() -> None:
    parsed = {
        "checklist": [{"category": "actors", "found": True, "evidence": [1, 9]}],
        "requirements": [{"kind": "functional", "description": "Use PostgreSQL", "evidence": 2}],
    }
    text = json.dumps(parsed)
    scores = score(
        parsed,
        text,
        Expected(max_citation=3, required_terms=("loan", "postgresql"), forbidden_terms=("postgresql",),
                 expect_found=("actors", "rules")),
    )  # fmt: skip
    assert scores == {
        "schema_valid": 1.0,
        "citation_validity": pytest.approx(2 / 3),  # 9 is outside the 3 pieces
        "term_recall": 0.5,
        "no_forbidden_terms": 0.0,
        "checklist_recall": 0.5,
    }
    assert score(None, "not json", Expected(max_citation=3)) == {"schema_valid": 0.0}


def test_the_dataset_loads_and_its_hash_covers_cases_and_expectations(tmp_path: Path) -> None:
    shutil.copytree(REPO_ROOT / "evals", tmp_path / "evals")
    cases, digest = load_dataset(tmp_path / "evals", AgentRole.BRD_ANALYST, "smoke")
    assert len(cases) == 3 and len(digest) == 64
    expected = tmp_path / "evals" / "expected" / "brd_analyst" / "smoke" / "lib-goal-scope.json"
    expected.write_text(expected.read_text().replace("lending", "borrowing"))
    assert load_dataset(tmp_path / "evals", AgentRole.BRD_ANALYST, "smoke")[1] != digest
    case = tmp_path / "evals" / "datasets" / "brd_analyst" / "smoke" / "lib-goal-scope.json"
    case.rename(case.with_name("renamed.json"))
    with pytest.raises(HarnessError, match="does not match"):
        load_dataset(tmp_path / "evals", AgentRole.BRD_ANALYST, "smoke")


# ---------------------------------------------------------------------------------------- runner


class _Gateway:
    """Gateway double: records requests; answers from a script (the runner never sees a provider)."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.requests: list[Any] = []

    def generate(self, request: Any, *, budget: Any, principal_id: str, output_model: Any = None) -> Any:
        self.requests.append((request, principal_id, budget))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        from devteam_core.contracts.llm import ModelResponse, ModelUsage, OutputValidation, Provider  # noqa: PLC0415

        output_model.model_validate(answer)
        return ModelResponse(
            request_id=request.request_id, provider=Provider.OLLAMA, model_id="qwen2.5:3b",
            output_text=json.dumps(answer), parsed=answer, usage=ModelUsage(input_tokens=1, output_tokens=1),
            duration_ms=10, validation=OutputValidation(valid=True),
        )  # fmt: skip

    def model_digest(self, _tag: str) -> str:
        return "a" * 64


def _runner(gateway: _Gateway) -> Runner:
    prompts, _ = build_file_registries(REPO_ROOT / "config", root=REPO_ROOT)
    return Runner(
        gateway,
        prompts,
        InputGuard(load_guardrails(REPO_ROOT / "config").input),
        CONTRACT_MODELS.get,
        new_budget=lambda: JobBudget(
            JobLimits(
                max_model_calls=9,
                max_tokens=99_999,
                max_minutes=5,
                model_call_timeout_s=30,
                min_free_ram_mb=0,
            ),
            project_id=None,
        ),
        now=lambda: datetime(2026, 11, 25, tzinfo=UTC),
    )


def test_the_runner_uses_the_gateway_as_the_evaluator_with_untrusted_data() -> None:
    good = {"checklist": [{"category": "goal", "found": True, "evidence": [1]}], "requirements": []}
    gateway = _Gateway([good, good, ResourceUnavailableError("model.exhausted", "x")])
    card = _runner(gateway).run(AgentRole.BRD_ANALYST, "brd_analyst.system", 1, "smoke", REPO_ROOT / "evals")
    assert [p for _r, p, _b in gateway.requests] == ["service:evaluator"] * 3
    request = gateway.requests[0][0]
    assert request.messages[0].role.value == "system" and request.messages[1].trust.value == "untrusted"
    assert request.prompt_hash == card.prompt_hash and request.task_id is None
    assert [c.error_code for c in card.cases].count(
        "model.exhausted"
    ) == 1  # a failed case is recorded, not hidden
    assert card.totals["cases"] == 3.0 and card.model_digest == "a" * 64


# ---------------------------------------------------------------------------------------- CI gate


def _gate(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    shutil.copytree(REPO_ROOT / "evals", tmp_path / "evals")
    return tmp_path


def _require(root: Path, prompt_id: str) -> None:
    path = root / "config" / "prompts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["prompts"][prompt_id]["eval_required"] = True
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_the_gate_passes_on_the_repository_and_lists_unevaluated_stubs() -> None:
    result = _gate(REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "eval gate: passed" in result.stdout
    assert result.stdout.count("not evaluated (stub until its agent part)") == 10
    assert (
        "brd_analyst.system v1: not evaluated" in result.stdout and "report present: matches" in result.stdout
    )


def test_the_gate_fails_for_a_required_prompt_without_a_report(repo: Path) -> None:
    _require(repo, "head.system")  # negative control: no report exists for head
    result = _gate(repo)
    assert result.returncode == 1 and "FAIL  head.system v1: no report" in result.stdout


def test_the_gate_passes_a_required_prompt_with_a_matching_report(repo: Path) -> None:
    _require(repo, "brd_analyst.system")
    result = _gate(repo)
    assert result.returncode == 0, result.stdout
    assert "PASS  brd_analyst.system v1" in result.stdout


def test_the_gate_fails_a_stale_or_malformed_report(repo: Path) -> None:
    _require(repo, "brd_analyst.system")
    report = repo / "evals" / "reports" / "brd_analyst-v1.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    data["prompt_hash"] = "f" * 64
    report.write_text(json.dumps(data), encoding="utf-8")
    assert "stale hash" in _gate(repo).stdout
    report.write_text("{}", encoding="utf-8")
    assert "not a valid scorecard" in _gate(repo).stdout


def test_written_reports_round_trip(tmp_path: Path) -> None:
    card = Scorecard.model_validate_json(
        (REPO_ROOT / "evals" / "reports" / "brd_analyst-v1.json").read_text()
    )
    json_path, md_path = write_report(card, tmp_path)
    assert check_report(json_path, prompt_hash=card.prompt_hash) is None
    assert card.prompt_hash in md_path.read_text(encoding="utf-8")
