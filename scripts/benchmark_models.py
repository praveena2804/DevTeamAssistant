"""Local model benchmark (Spec §22 Phase 0, roadmap §13 Part A, AC-A-03).

For each candidate model and each run (default 3, medians reported):

1. unload every loaded model (``keep_alive: 0``) so the first call measures a cold load;
2. **structured task**: requirements extraction into a JSON schema (Ollama ``format``), validated with
   Pydantic, the instruct-slot quality signal;
3. **code task**: write a small Python function, the coder-slot quality signal. Scored by **static
   analysis only** (parse, expected signature, no dangerous imports/calls). Model-generated code is never
   executed on the host (Spec §11.3);
4. unload again.

Measured: load time, tokens/s (``eval_count / eval_duration``), prompt tokens, wall time, peak resident
memory of the Ollama processes (psutil sampling every 250 ms), minimum system free RAM.

Only the configured loopback Ollama endpoint is contacted (validated by devteam_core.config).
The recommendation is advisory: the owner approves model selection at gate G-A.

Usage:  uv run python scripts/benchmark_models.py [--models a b ...] [--runs 3] [--out-dir docs/benchmarks]
"""

from __future__ import annotations

import argparse
import ast
import json
import platform
import re
import statistics
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
from pydantic import BaseModel, Field, ValidationError, field_validator

from devteam_core.config import Settings, load_settings

SLOTS: dict[str, list[str]] = {
    "instruct": ["qwen2.5:3b", "llama3.2:3b"],
    "coder": ["qwen2.5-coder:3b"],
    "fallback": ["qwen2.5:1.5b"],
}

BRD_SNIPPET = (
    "The Library Lending System lets librarians register members and record book loans. "
    "Members can search the catalogue and view their current loans. "
    "A member may borrow at most 3 books at a time. Loans are due 14 days after checkout. "
    "Only librarians can register members."
)
STRUCTURED_PROMPT = (
    "Extract requirements from the BRD text below. Use ids FR-001, FR-002, ... for functional "
    "requirements and BR-001, BR-002, ... for business rules. List the user roles. Use only facts "
    "stated in the text.\n\nBRD TEXT:\n" + BRD_SNIPPET
)
CODE_PROMPT = (
    "Write a Python function `slugify(text: str) -> str` that lowercases the text, replaces every run of "
    "characters that are not letters or digits with a single hyphen, and strips leading and trailing "
    "hyphens. Use only the `re` module. Return only the code in one ```python block."
)
FORBIDDEN_IMPORTS = {"os", "sys", "subprocess", "socket", "shutil", "pathlib", "ctypes", "importlib"}
FORBIDDEN_CALLS = {"eval", "exec", "open", "compile", "__import__"}


class _Requirement(BaseModel):
    # The pattern is part of the JSON schema sent to Ollama, so the output grammar enforces it. The check
    # therefore measures schema reliability (Spec §22 Phase 0), not whether a model guesses an
    # unstated format. In the platform itself, stable IDs are assigned by deterministic code (Spec §6.2).
    id: str = Field(pattern=r"^FR-[0-9]{3}$")
    text: str = Field(min_length=3)


class _Rule(BaseModel):
    id: str = Field(pattern=r"^BR-[0-9]{3}$")
    text: str = Field(min_length=3)


class StructuredAnswer(BaseModel):
    requirements: list[_Requirement] = Field(min_length=1)
    business_rules: list[_Rule] = Field(min_length=2)
    roles: list[str] = Field(min_length=1)

    @field_validator("roles")
    @classmethod
    def _librarian(cls, roles: list[str]) -> list[str]:
        if not any("librarian" in r.lower() for r in roles):
            raise ValueError("the librarian role stated in the text is missing")
        return roles


def structured_schema() -> dict[str, Any]:
    return StructuredAnswer.model_json_schema()


def score_structured(raw: str) -> tuple[bool, str]:
    try:
        StructuredAnswer.model_validate_json(raw)
    except ValidationError as exc:
        return False, f"{exc.error_count()} validation error(s): {exc.errors()[0]['msg']}"
    return True, "valid"


def score_code(raw: str) -> tuple[bool, str]:  # noqa: PLR0911 - one return per failed check
    """Static checks only; the code is parsed, never executed."""
    match = re.search(r"```(?:python)?\s*\n(.*?)```", raw, re.DOTALL)
    source = match.group(1) if match else raw
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"syntax error: {exc.msg}"
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "slugify"]
    if not functions:
        return False, "no slugify function"
    if len(functions[0].args.args) != 1:
        return False, "slugify must take exactly one argument"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = (
                [a.name.split(".")[0] for a in node.names]
                if isinstance(node, ast.Import)
                else [(node.module or "").split(".")[0]]
            )
            if FORBIDDEN_IMPORTS.intersection(names):
                return False, f"forbidden import {names}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            return False, f"forbidden call {node.func.id}"
    if not any(isinstance(n, ast.Return) for n in ast.walk(functions[0])):
        return False, "slugify never returns"
    return True, "parses; signature and imports OK (static only, not executed)"


class MemorySampler:
    """Samples the summed RSS of Ollama processes and the system's available memory."""

    def __init__(self, interval: float = 0.25) -> None:
        self.interval = interval
        self.peak_ollama_mb = 0.0
        self.min_available_mb = float("inf")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def ollama_rss_mb() -> float:
        total = 0
        for proc in psutil.process_iter(["name", "memory_info"]):
            name = (proc.info.get("name") or "").lower()
            mem = proc.info.get("memory_info")
            if name.startswith(("ollama", "llama")) and mem is not None:
                total += mem.rss
        return total / 1024**2

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak_ollama_mb = max(self.peak_ollama_mb, self.ollama_rss_mb())
            self.min_available_mb = min(self.min_available_mb, psutil.virtual_memory().available / 1024**2)
            self._stop.wait(self.interval)

    def __enter__(self) -> MemorySampler:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()


@dataclass
class CallResult:
    ok: bool
    detail: str
    load_s: float
    tokens_per_s: float | None
    prompt_tokens: int
    output_tokens: int
    wall_s: float
    excerpt: str = ""


@dataclass
class RunResult:
    run: int
    structured: CallResult
    code: CallResult
    peak_ollama_rss_mb: float
    min_available_mb: float


@dataclass
class ModelReport:
    model: str
    slot: str
    digest: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    family: str | None = None
    runs: list[RunResult] = field(default_factory=list)
    error: str | None = None


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.base = settings.ollama.base_url  # validated loopback-only at config load
        self.http = httpx.Client(timeout=settings.ollama.request_timeout_s)
        self.num_ctx = settings.resources.llm_context_tokens

    def close(self) -> None:
        self.http.close()

    def version(self) -> str:
        return str(self.http.get(f"{self.base}/api/version").json().get("version"))

    def tags(self) -> dict[str, dict[str, Any]]:
        return {m["name"]: m for m in self.http.get(f"{self.base}/api/tags").json().get("models", [])}

    def show(self, model: str) -> dict[str, Any]:
        response = self.http.post(f"{self.base}/api/show", json={"model": model})
        response.raise_for_status()
        return dict(response.json().get("details", {}))

    def loaded(self) -> list[str]:
        return [m["name"] for m in self.http.get(f"{self.base}/api/ps").json().get("models", [])]

    def unload_all(self, timeout: float = 60) -> None:
        for name in self.loaded():
            self.http.post(f"{self.base}/api/generate", json={"model": name, "keep_alive": 0})
        deadline = time.monotonic() + timeout
        while self.loaded() and time.monotonic() < deadline:
            time.sleep(0.5)

    def generate(self, model: str, prompt: str, *, fmt: dict[str, Any] | None, seed: int) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "5m",
            "options": {"temperature": 0, "seed": seed, "num_ctx": self.num_ctx, "num_predict": 512},
        }
        if fmt is not None:
            body["format"] = fmt
        response = self.http.post(f"{self.base}/api/generate", json=body)
        response.raise_for_status()
        return dict(response.json())


def _call(client: OllamaClient, model: str, prompt: str, fmt: dict[str, Any] | None, seed: int) -> CallResult:
    started = time.perf_counter()
    data = client.generate(model, prompt, fmt=fmt, seed=seed)
    wall = time.perf_counter() - started
    eval_count = int(data.get("eval_count", 0))
    eval_ns = int(data.get("eval_duration", 0))
    raw = str(data.get("response", ""))
    ok, detail = (score_structured if fmt is not None else score_code)(raw)
    return CallResult(
        ok=ok,
        detail=detail,
        load_s=int(data.get("load_duration", 0)) / 1e9,
        tokens_per_s=(eval_count / (eval_ns / 1e9)) if eval_ns else None,
        prompt_tokens=int(data.get("prompt_eval_count", 0)),
        output_tokens=eval_count,
        wall_s=wall,
        excerpt=raw[:300],  # synthetic benchmark prompt only; kept for diagnosing failures
    )


def benchmark_model(
    client: OllamaClient, model: str, slot: str, runs: int, tags: dict[str, Any]
) -> ModelReport:
    report = ModelReport(model=model, slot=slot, digest=(tags.get(model) or {}).get("digest"))
    if model not in tags:
        report.error = "model not pulled locally"
        return report
    details = client.show(model)
    report.parameter_size = details.get("parameter_size")
    report.quantization = details.get("quantization_level")
    report.family = details.get("family")
    for run in range(1, runs + 1):
        client.unload_all()
        with MemorySampler() as sampler:
            structured = _call(client, model, STRUCTURED_PROMPT, structured_schema(), seed=41 + run)
            code = _call(client, model, CODE_PROMPT, None, seed=41 + run)
        client.unload_all()
        report.runs.append(
            RunResult(
                run, structured, code, round(sampler.peak_ollama_mb, 1), round(sampler.min_available_mb, 1)
            )
        )
        print(
            f"  {model} run {run}: structured={structured.ok} code={code.ok} "
            f"tps={structured.tokens_per_s or 0:.2f} load={structured.load_s:.1f}s "
            f"peak_rss={sampler.peak_ollama_mb:.0f}MB",
            flush=True,
        )
    return report


def _median(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 2) if present else None


def summarise(report: ModelReport) -> dict[str, Any]:
    runs = report.runs
    return {
        "model": report.model,
        "slot": report.slot,
        "error": report.error,
        "runs": len(runs),
        "structured_pass": sum(r.structured.ok for r in runs),
        "code_pass": sum(r.code.ok for r in runs),
        "median_load_s": _median([r.structured.load_s for r in runs]),
        "median_tokens_per_s": _median(
            [r.structured.tokens_per_s for r in runs] + [r.code.tokens_per_s for r in runs]
        ),
        "median_structured_wall_s": _median([r.structured.wall_s for r in runs]),
        "median_code_wall_s": _median([r.code.wall_s for r in runs]),
        "median_peak_ollama_rss_mb": _median([r.peak_ollama_rss_mb for r in runs]),
        "min_available_mb": min((r.min_available_mb for r in runs), default=None),
    }


def recommend(summaries: list[dict[str, Any]]) -> dict[str, str]:
    """Per slot: among models that passed every run of the slot's quality task, pick the fastest."""
    picks: dict[str, str] = {}
    criteria = {"instruct": "structured_pass", "coder": "code_pass", "fallback": "structured_pass"}
    for slot, key in criteria.items():
        eligible = [s for s in summaries if s["slot"] == slot and s["runs"] and s[key] == s["runs"]]
        if eligible:
            best = max(eligible, key=lambda s: s["median_tokens_per_s"] or 0)
            picks[slot] = (
                f"{best['model']} (passed {best[key]}/{best['runs']}, {best['median_tokens_per_s']} tok/s)"
            )
        else:
            picks[slot] = "no candidate passed every run; see table (owner decision required)"
    return picks


def render_markdown(meta: dict[str, Any], summaries: list[dict[str, Any]], picks: dict[str, str]) -> str:
    lines = [
        f"# Model Benchmark — Baseline ({meta['date']})",
        "",
        "Generated by `scripts/benchmark_models.py` (roadmap §13 Part A, AC-A-03). "
        "**Recommendation only: model selection is approved by the owner at gate G-A.**",
        "",
        "## Environment",
        "",
        "| Item | Value |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in meta.items() if k != "models"]
    lines += [
        "",
        f"## Results (median of {meta['runs_per_model']} runs)",
        "",
        "| Model | Slot | Params | Quant | Structured pass | Code pass (static) | Load s | Tok/s | "
        "Structured wall s | Code wall s | Peak Ollama RSS MB | Min free RAM MB |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    details = {m["model"]: m for m in meta["models"]}
    for s in summaries:
        d = details.get(s["model"], {})
        if s["error"]:
            lines.append(f"| `{s['model']}` | {s['slot']} | — | — | error: {s['error']} | | | | | | | |")
            continue
        lines.append(
            f"| `{s['model']}` | {s['slot']} | {d.get('parameter_size')} | {d.get('quantization')} | "
            f"{s['structured_pass']}/{s['runs']} | {s['code_pass']}/{s['runs']} | {s['median_load_s']} | "
            f"{s['median_tokens_per_s']} | {s['median_structured_wall_s']} | {s['median_code_wall_s']} | "
            f"{s['median_peak_ollama_rss_mb']} | {s['min_available_mb']} |"
        )
    lines += ["", "## Recommendation (advisory)", "", "| Slot | Recommended |", "|---|---|"]
    lines += [f"| {slot} | {pick} |" for slot, pick in picks.items()]
    lines += [
        "",
        "## Method notes",
        "",
        "- Every run starts from an unloaded state, so `Load s` is a cold load.",
        "- Structured task: JSON-schema constrained output validated with Pydantic (ids, roles, rule count).",
        "- Code task: static analysis only (parse, signature, no dangerous imports or calls).",
        "  Generated code is **never executed on the host** (Spec §11.3); correctness is not measured.",
        "- Tok/s is the median over both tasks of `eval_count / eval_duration` reported by Ollama.",
        "- Peak RSS sums all `ollama*`/`llama*` processes; it reflects memory pressure during the run.",
        "- Digests are recorded in the JSON report for reproducibility (NFR-005).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark local Ollama models")
    parser.add_argument("--models", nargs="*", help="override candidate models (slot 'custom')")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--out-dir", type=Path, default=Path(__file__).resolve().parents[1] / "docs" / "benchmarks"
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    candidates = (
        [(m, "custom") for m in args.models]
        if args.models
        else [(m, s) for s, ms in SLOTS.items() for m in ms]
    )
    client = OllamaClient(settings)
    started = datetime.now(UTC)
    try:
        tags = client.tags()
        meta: dict[str, Any] = {
            "date": started.strftime("%Y-%m-%d"),
            "started_utc": started.isoformat(timespec="seconds"),
            "ollama_version": client.version(),
            "ollama_endpoint": settings.ollama.base_url,
            "cpu": platform.processor() or platform.machine(),
            "cores_threads": f"{psutil.cpu_count(logical=False)}/{psutil.cpu_count()}",
            "ram_total_gb": round(psutil.virtual_memory().total / 1024**3, 1),
            "ram_available_at_start_mb": round(psutil.virtual_memory().available / 1024**2),
            "num_ctx": settings.resources.llm_context_tokens,
            "runs_per_model": args.runs,
            "os": platform.platform(),
        }
        reports = []
        for model, slot in candidates:
            print(f"benchmarking {model} ({slot})", flush=True)
            try:
                reports.append(benchmark_model(client, model, slot, args.runs, tags))
            except httpx.HTTPError as exc:
                reports.append(ModelReport(model=model, slot=slot, error=f"{type(exc).__name__}: {exc}"))
        client.unload_all()
    finally:
        client.close()

    meta["finished_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    meta["models"] = [
        {k: getattr(r, k) for k in ("model", "digest", "parameter_size", "quantization", "family")}
        for r in reports
    ]
    summaries = [summarise(r) for r in reports]
    picks = recommend(summaries)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.out_dir / f"{meta['date']}-baseline"
    stem.with_suffix(".json").write_text(
        json.dumps(
            {
                "meta": meta,
                "summary": summaries,
                "recommendation": picks,
                "raw": [asdict(r) for r in reports],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    stem.with_suffix(".md").write_text(render_markdown(meta, summaries, picks), encoding="utf-8")
    print(f"wrote {stem}.json and {stem}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
