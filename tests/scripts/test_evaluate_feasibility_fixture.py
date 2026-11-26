"""Part G demo script (D-65): a fixture evaluated with the real rules, written only to the output folder,
labelled as a fixture evaluation, and never recorded as a run."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "evaluate_feasibility_fixture.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "feasibility"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_feasibility_fixture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_power_apps_is_flagged_in_a_labelled_fixture_report(tmp_path: Path) -> None:
    script = _load()
    assert script.main([str(FIXTURES / "power_apps.json"), "--out", str(tmp_path)]) == 0
    text = (tmp_path / "FEASIBILITY_REPORT.md").read_text(encoding="utf-8")
    assert text.startswith("> **FIXTURE EVALUATION — not a run.**")
    assert "Power Apps (deployment platform) is not supported" in text
    assert "**FR-001**: blocked by" in text and "Nothing can be built" in text
    assert list(tmp_path.iterdir()) == [tmp_path / "FEASIBILITY_REPORT.md"]  # nothing else is written


def test_without_a_state_database_no_verification_is_assumed(tmp_path: Path) -> None:
    script = _load()
    script.main([str(FIXTURES / "postgresql.json"), "--out", str(tmp_path)])
    text = (tmp_path / "FEASIBILITY_REPORT.md").read_text(encoding="utf-8")
    assert "`postgresql_docker_local`: not available yet: arrives in Part K" in text
    assert "has never been verified" in text  # kits are demoted honestly, so SQLite cannot be offered either
