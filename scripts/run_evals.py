"""Run a prompt evaluation with the local model and write its scorecard (HC-09; Working Plan §13.3).

    uv run python scripts/run_evals.py --agent brd_analyst --dataset smoke [--prompt-version 1]

Owner-run, real local Ollama only (never in CI). Every call goes through the LLM Gateway as the
``service:evaluator`` principal under a job budget. Model-call records go to a separate evaluation database
(``<data_dir>/evals/state.db``) so the platform's own database is never touched. The scorecard is written to
``evals/reports/<agent>-v<N>.json`` and ``docs/evals/<agent>-v<N>.md``.
"""

from __future__ import annotations

import argparse
import sys

from devteam_core.config import load_settings
from devteam_core.contracts import CONTRACT_MODELS
from devteam_core.contracts.statuses import AgentRole
from devteam_core.logging import configure_logging
from devteam_core.paths import find_repo_root
from devteam_harness.budgets.jobs import JobBudget, JobLimits
from devteam_harness.evaluation import Runner, write_report
from devteam_harness.guardrails.input import InputGuard
from devteam_harness.prompts.files import build_file_registries, code_root
from devteam_orchestration.runtime import build_runtime
from devteam_state import create_sqlite_engine, session_factory, upgrade


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a prompt evaluation with the local model")
    parser.add_argument("--agent", required=True, choices=[a.value for a in AgentRole])
    parser.add_argument("--dataset", default="smoke")
    parser.add_argument("--prompt-version", type=int, default=None)
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings)
    config_dir = find_repo_root() / "config"
    prompts, _skills = build_file_registries(config_dir)
    prompt_id = f"{args.agent}.system"
    version = args.prompt_version or prompts.config.prompts[prompt_id].active

    db = settings.data_dir / "evals" / "state.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    upgrade(db)
    engine = create_sqlite_engine(db)
    runtime = build_runtime(settings, session_factory(engine))
    try:
        gateway = runtime.gateway
        if gateway is None:
            raise SystemExit("the runtime has no LLM gateway")

        def budget() -> JobBudget:
            limits = JobLimits(
                max_model_calls=60,
                max_tokens=400_000,
                max_minutes=60,
                model_call_timeout_s=300,
                min_free_ram_mb=1024,
            )
            return JobBudget(limits, project_id=None)

        runner = Runner(
            gateway,
            prompts,
            InputGuard(runtime.guardrails.input),
            CONTRACT_MODELS.get,
            new_budget=budget,
            now=runtime.clock.now,
        )
        root = code_root()
        card = runner.run(AgentRole(args.agent), prompt_id, version, args.dataset, root / "evals")
        json_path, md_path = write_report(card, root)
    finally:
        runtime.close()
        engine.dispose()
    print(f"{card.prompt_id} v{card.prompt_version} on {card.model_tag}: {card.totals}")
    print(f"wrote {json_path.relative_to(root).as_posix()} and {md_path.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
