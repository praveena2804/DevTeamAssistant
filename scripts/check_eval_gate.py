"""CI eval gate (HC-09/HC-16; AC-F-05; D-56). Offline: never calls a model.

    uv run python scripts/check_eval_gate.py [--root <repository root>]

For every **active** prompt version in ``config/prompts.yaml``:

* ``eval_required: true`` → ``evals/reports/<agent>-v<N>.json`` must exist, be a valid scorecard and carry
  exactly the prompt's released hash (and a model digest). Otherwise the gate fails (exit 1).
* ``eval_required: false`` (the Part D stub prompts until their agent part supplies a dataset) → listed as
  "not evaluated", never silently passed. A report that exists anyway is still checked and reported.

Pass thresholds are not enforced here; they are set per agent part (D-12).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from devteam_core.contracts.statuses import AgentRole
from devteam_harness.evaluation import check_report, report_paths
from devteam_harness.prompts.files import eval_required, load_prompt_config

REPO = Path(__file__).resolve().parents[1]


def check(root: Path) -> tuple[bool, list[str]]:
    config = load_prompt_config(root / "config")
    lines: list[str] = []
    ok = True
    for prompt_id, version, required in eval_required(config):
        agent = AgentRole(prompt_id.split(".", 1)[0])
        prompt_hash = config.prompts[prompt_id].released[version]
        report, _md = report_paths(root, agent, version)
        problem = check_report(report, prompt_hash=prompt_hash)
        if required:
            if problem is None:
                lines.append(f"PASS  {prompt_id} v{version}: report matches the prompt hash")
            else:
                ok = False
                lines.append(f"FAIL  {prompt_id} v{version}: {problem}")
        else:
            note = "" if problem == "no report" else f" (report present: {problem or 'matches'})"
            lines.append(f"SKIP  {prompt_id} v{version}: not evaluated (stub until its agent part){note}")
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI eval gate")
    parser.add_argument("--root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    ok, lines = check(args.root)
    print("\n".join(lines))
    print("eval gate: passed" if ok else "eval gate: FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
