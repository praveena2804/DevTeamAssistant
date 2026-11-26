"""Regenerate the Part G fixture analyses (validated ``BrdAnalystOutput`` JSON; D-65).

    uv run python tests/fixtures/feasibility/generate.py

These are **test fixtures**, not agent output: the Part D BRD Analyst does not extract technologies
(that is Part H), so the Power Apps, Angular, SQL Server, "SQL", PostgreSQL and mixed scenarios are proven
on these files. Runs never use them (D-38). IDs are fixed so the files are stable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devteam_core.contracts.agents import BrdAnalystOutput
from devteam_core.contracts.records import GapCategory

HERE = Path(__file__).parent
PROJECT = "prj_01KFE0000000000000000000PG"
RUN = "run_01KFE0000000000000000000PG"
DOC = "doc_01KFE0000000000000000000PG"


def ulid(prefix: str, n: int) -> str:
    return f"{prefix}_01KFE{n:021d}"


def source(n: int, quote: str) -> dict[str, Any]:
    return {"document_id": DOC, "chunk_id": f"chk_{n:026x}", "page_start": 1 + n // 4, "quote": quote}


def fact(n: int, quote: str) -> dict[str, Any]:
    return {"provenance": "brd_fact", "source_refs": [source(n, quote)]}


REQUIREMENTS = [
    ("FR-001", "functional", "Members can borrow books."),
    ("FR-002", "functional", "Members can return books."),
    ("FR-003", "functional", "Members pay late fees online."),
    ("FR-004", "functional", "Staff sign in with their company account."),
    ("NFR-001", "non_functional", "Pages load within two seconds."),
]


def base() -> dict[str, Any]:
    return {
        "project_id": PROJECT,
        "run_id": RUN,
        "requirements": [
            {"requirement_id": key, "project_id": PROJECT, "type": kind, "description": text, **fact(i, text)}
            for i, (key, kind, text) in enumerate(REQUIREMENTS, start=1)
        ],
        "business_rules": [
            {
                "rule_id": "BR-001",
                "project_id": PROJECT,
                "description": "A member may borrow at most five books.",
                "requirement_ids": ["FR-001"],
                **fact(6, "at most five books"),
            }
        ],
        "checklist": [
            {"category": c.value, "status": "found", "source_refs": [source(7, "overview")]}
            for c in GapCategory
        ],
    }


def need(n: int, layer: str, name: str, quote: str, **extra: Any) -> dict[str, Any]:
    ids = {"tech_id": ulid("tec", n), "project_id": PROJECT}
    return {**ids, "layer": layer, "name": name, **fact(10 + n, quote), **extra}


def integration(n: int, name: str, quote: str, **extra: Any) -> dict[str, Any]:
    return {"integration_id": ulid("itg", n), "project_id": PROJECT, "name": name, "description": quote,
            **fact(20 + n, quote), **extra}  # fmt: skip


SCENARIOS: dict[str, dict[str, Any]] = {
    # Spec §23 scenario #3 / AC-G-01
    "power_apps": {
        "technology_needs": [need(1, "deployment", "Power Apps", "The app must run in Power Apps.")]
    },
    "angular": {
        "technology_needs": [need(1, "frontend", "Angular", "Built with Angular 15.", version_spec="15")]
    },
    "sql_server": {"technology_needs": [need(1, "database", "SQL Server", "Data is kept in SQL Server.")]},
    "sql_ambiguous": {"technology_needs": [need(1, "database", "SQL", "Store the data in SQL.")]},
    "postgresql": {"technology_needs": [need(1, "database", "PostgreSQL", "Use PostgreSQL.")]},
    "mixed": {
        "technology_needs": [
            need(1, "frontend", "React 18", "A React 18 web front end."),
            need(2, "backend", "FastAPI", "A FastAPI service."),
            need(3, "database", "a relational database", "Keep records in a relational database."),
            need(4, "authentication", "Azure AD", "Staff use Azure AD.", requirement_ids=["FR-004"]),
        ],
        "integrations": [integration(1, "Stripe", "Fees are paid by Stripe.", requirement_ids=["FR-003"])],
    },
    "all_supported": {
        "technology_needs": [
            need(1, "frontend", "React", "A React front end."),
            need(2, "backend", "FastAPI", "A FastAPI service."),
            need(3, "database", "SQLite", "SQLite is enough."),
            need(4, "authentication", "No login", "No login is needed."),
        ]
    },
    "none": {},
}


def build(name: str) -> BrdAnalystOutput:
    return BrdAnalystOutput.model_validate({**base(), **SCENARIOS[name]})


def main() -> None:
    for name in SCENARIOS:
        text = json.dumps(build(name).model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False)
        (HERE / f"{name}.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(SCENARIOS)} fixture analyses")


if __name__ == "__main__":
    main()
