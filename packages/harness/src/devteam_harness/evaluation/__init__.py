"""Prompt evaluation (HC-09, HS-24; Working Plan §13.3; Part F plan §5 F.4, D-56).

* **Datasets** live in ``evals/datasets/<agent>/<dataset>/<case_id>.json`` with expected outcomes in
  ``evals/expected/<agent>/<dataset>/<case_id>.json``. Each case carries the exact DATA text the agent would
  send (already rendered, e.g. numbered BRD pieces) and the answer contract it must satisfy.
* The **runner** sends each case through the **LLM Gateway** as the ``service:evaluator`` principal under a
  ``JobBudget``: the active prompt (verified file registry) as the only trusted system message, the case data
  wrapped as untrusted by the input guard (exactly as the executor does). It never calls a provider.
* **Scorers** are deterministic: schema validity, citation validity (every ``evidence`` number within the
  case's material), required-term recall, forbidden-term absence, expected checklist findings.
* A **scorecard** (report) records the prompt ID/version/hash, the model tag and digest, the dataset hash,
  per-case scores and totals. Reports: ``evals/reports/<agent>-v<N>.json`` + ``docs/evals/<agent>-v<N>.md``.
* The **CI eval gate** (``scripts/check_eval_gate.py``) requires a matching report for every active prompt
  marked ``eval_required`` (D-56). Pass thresholds are set per agent part (D-12), not here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from devteam_core.contracts.llm import ChatMessage, ChatRole, ModelParams, ModelRequest, Trust
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.ids import new_id
from devteam_harness.budgets.jobs import JobBudget
from devteam_harness.errors import HarnessError
from devteam_harness.guardrails.input import InputGuard
from devteam_harness.prompts import PromptRegistry

EVALUATOR = "service:evaluator"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvalCase(_Strict):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,60}$")
    operation_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}\.[a-z][a-z0-9_]{1,60}$")
    slot: ModelSlot = ModelSlot.INSTRUCT
    output_schema: str
    data_label: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    data: str = Field(min_length=1, max_length=20_000)
    description: str = ""


class Expected(_Strict):
    max_citation: int | None = Field(default=None, ge=1)
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    expect_found: tuple[str, ...] = ()  # checklist categories that must be reported found (BRD Analyst)


class CaseResult(_Strict):
    case_id: str
    valid: bool
    error_code: str | None = None
    scores: dict[str, float]
    duration_ms: int = Field(ge=0)


class Scorecard(_Strict):
    schema_version: Literal[1] = 1
    agent: AgentRole
    prompt_id: str
    prompt_version: int = Field(ge=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_tag: str
    model_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    cases: tuple[CaseResult, ...] = Field(min_length=1)
    totals: dict[str, float]


# ----------------------------------------------------------------------------------------- scorers


def _numbers_under(data: Any, key: str) -> Iterator[int]:
    if isinstance(data, dict):
        for k, v in data.items():
            if k == key:
                values = v if isinstance(v, list) else [v]
                yield from (x for x in values if isinstance(x, int) and not isinstance(x, bool))
            else:
                yield from _numbers_under(v, key)
    elif isinstance(data, list):
        for item in data:
            yield from _numbers_under(item, key)


def score(parsed: dict[str, Any] | None, text: str, expected: Expected) -> dict[str, float]:
    """Deterministic scores in [0, 1] (1 = best). Absent checks are not scored."""
    scores: dict[str, float] = {"schema_valid": 1.0 if parsed is not None else 0.0}
    if parsed is None:
        return scores
    lowered = text.lower()
    if expected.max_citation is not None:
        cited = list(_numbers_under(parsed, "evidence"))
        valid = [n for n in cited if 1 <= n <= expected.max_citation]
        scores["citation_validity"] = len(valid) / len(cited) if cited else 1.0
    if expected.required_terms:
        hits = sum(term.lower() in lowered for term in expected.required_terms)
        scores["term_recall"] = hits / len(expected.required_terms)
    if expected.forbidden_terms:
        scores["no_forbidden_terms"] = (
            0.0 if any(t.lower() in lowered for t in expected.forbidden_terms) else 1.0
        )
    if expected.expect_found:
        found = {
            str(item.get("category"))
            for item in parsed.get("checklist", [])
            if isinstance(item, dict) and item.get("found") is True
        }
        scores["checklist_recall"] = sum(c in found for c in expected.expect_found) / len(
            expected.expect_found
        )
    return scores


# ----------------------------------------------------------------------------------------- datasets


def load_dataset(
    evals_root: Path, agent: AgentRole, dataset: str
) -> tuple[list[tuple[EvalCase, Expected]], str]:
    """Cases with their expectations, and the SHA-256 over every case and expectation file."""
    case_dir = evals_root / "datasets" / agent.value / dataset
    expected_dir = evals_root / "expected" / agent.value / dataset
    files = sorted(case_dir.glob("*.json"))
    if not files:
        raise HarnessError("eval.dataset_missing", f"no cases in {case_dir.as_posix()}")
    digest = hashlib.sha256()
    out: list[tuple[EvalCase, Expected]] = []
    for path in files:
        raw = path.read_bytes()
        case = EvalCase.model_validate_json(raw)
        if path.stem != case.case_id or not case.operation_id.startswith(f"{agent.value}."):
            raise HarnessError("eval.case_mismatch", f"{path.name}: case ID or operation does not match")
        expected_path = expected_dir / path.name
        expected_raw = expected_path.read_bytes() if expected_path.is_file() else b"{}"
        digest.update(path.name.encode() + b"\0" + raw + b"\0" + expected_raw + b"\0")
        out.append((case, Expected.model_validate_json(expected_raw)))
    return out, digest.hexdigest()


# ----------------------------------------------------------------------------------------- runner


class Runner:
    def __init__(
        self,
        gateway: Any,
        prompts: PromptRegistry,
        guard: InputGuard,
        schemas: Callable[[str], type[BaseModel] | None],
        *,
        new_budget: Callable[[], JobBudget],
        now: Callable[[], datetime],
        params: ModelParams | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompts = prompts
        self._guard = guard
        self._schemas = schemas
        self._new_budget = new_budget
        self._now = now
        self._params = params or ModelParams(
            max_output_tokens=1024, context_window=4096, temperature=0.0, seed=7
        )

    def run(
        self, agent: AgentRole, prompt_id: str, version: int, dataset: str, evals_root: Path
    ) -> Scorecard:
        cases, dataset_hash = load_dataset(evals_root, agent, dataset)
        template = self._prompts.get(prompt_id, version)
        results: list[CaseResult] = []
        model_tag = ""
        budget = self._new_budget()
        for case, expected in cases:
            output_model = self._schemas(case.output_schema)
            if output_model is None:
                raise HarnessError("eval.schema_unknown", f"{case.case_id}: unknown answer contract")
            data, _screening = self._guard.wrap_untrusted(case.data, case.data_label)
            request = ModelRequest(
                request_id=new_id("mrq"),
                role=agent,
                operation_id=case.operation_id,
                slot=case.slot,
                messages=(
                    ChatMessage(role=ChatRole.SYSTEM, content=template.text, trust=Trust.TRUSTED),
                    data,
                ),
                output_schema=case.output_schema,
                params=self._params,
                prompt_id=prompt_id,
                prompt_version=version,
                prompt_hash=template.sha256,
            )
            try:
                response = self._gateway.generate(
                    request, budget=budget, principal_id=EVALUATOR, output_model=output_model
                )
            except HarnessError as exc:
                results.append(CaseResult(case_id=case.case_id, valid=False, error_code=exc.code,
                                          scores={"schema_valid": 0.0}, duration_ms=0))  # fmt: skip
                continue
            model_tag = response.model_id
            parsed = response.parsed if response.validation.valid else None
            results.append(
                CaseResult(
                    case_id=case.case_id,
                    valid=response.validation.valid,
                    scores=score(parsed, response.output_text, expected),
                    duration_ms=response.duration_ms,
                )
            )
        digest = self._gateway.model_digest(model_tag) if model_tag else None
        if not model_tag or digest is None:
            raise HarnessError("eval.no_model_answer", "no case was answered; nothing to report")
        keys = sorted({k for r in results for k in r.scores})
        totals = {
            k: round(
                sum(r.scores[k] for r in results if k in r.scores) / sum(k in r.scores for r in results), 4
            )
            for k in keys
        }
        totals["cases"] = float(len(results))
        return Scorecard(
            agent=agent,
            prompt_id=prompt_id,
            prompt_version=version,
            prompt_hash=template.sha256,
            model_tag=model_tag,
            model_digest=digest,
            dataset=dataset,
            dataset_sha256=dataset_hash,
            created_at=self._now(),
            cases=tuple(results),
            totals=totals,
        )


def report_paths(repo_root: Path, agent: AgentRole, version: int) -> tuple[Path, Path]:
    name = f"{agent.value}-v{version}"
    return repo_root / "evals" / "reports" / f"{name}.json", repo_root / "docs" / "evals" / f"{name}.md"


def write_report(card: Scorecard, repo_root: Path) -> tuple[Path, Path]:
    json_path, md_path = report_paths(repo_root, card.agent, card.prompt_version)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(card.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    rows = "\n".join(
        f"| `{c.case_id}` | {'yes' if c.valid else 'no'} | "
        + ", ".join(f"{k} {v:.2f}" for k, v in sorted(c.scores.items()))
        + f" | {c.duration_ms / 1000:.1f} s |"
        for c in card.cases
    )
    md_path.write_text(
        f"# Evaluation: {card.prompt_id} v{card.prompt_version}\n\n"
        f"| Field | Value |\n|---|---|\n"
        f"| Prompt hash | `{card.prompt_hash}` |\n"
        f"| Model | `{card.model_tag}` (digest `{card.model_digest[:12]}…`) |\n"
        f"| Dataset | `{card.dataset}` (`{card.dataset_sha256[:12]}…`), {len(card.cases)} cases |\n"
        f"| Created | {card.created_at.isoformat(timespec='seconds')} |\n\n"
        "Scores are 0 to 1 (1 = best). Pass thresholds are set per agent part (D-12).\n\n"
        "| Case | Schema valid | Scores | Time |\n|---|---|---|---|\n"
        f"{rows}\n\n"
        "**Totals:** " + ", ".join(f"{k} {v:g}" for k, v in sorted(card.totals.items())) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return json_path, md_path


def check_report(path: Path, *, prompt_hash: str) -> str | None:
    """``None`` if the report is a well-formed scorecard for exactly this prompt hash, else the problem."""
    if not path.is_file():
        return "no report"
    try:
        card = Scorecard.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as exc:
        return f"report is not a valid scorecard ({type(exc).__name__})"
    if card.prompt_hash != prompt_hash:
        return "report is for another prompt text (stale hash)"
    return None


__all__ = [
    "EVALUATOR",
    "CaseResult",
    "EvalCase",
    "Expected",
    "Runner",
    "Scorecard",
    "check_report",
    "load_dataset",
    "report_paths",
    "score",
    "write_report",
]
