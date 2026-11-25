"""Part F import boundaries (AC-F-01, D-57): the Part F contracts hold on the real repository and break on
planted violations (negative controls)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.security.test_orchestration_static_rules import _lint_imports, _planted_copy

pytestmark = pytest.mark.security

PROVIDER = "only the composition root builds the Ollama provider; everything else uses the gateway (AC-F-01)"
API = "the API never calls models or opens the vector store (D-57)"


def test_part_f_contracts_kept_on_the_real_repository() -> None:
    result = _lint_imports(None)
    assert result.returncode == 0, result.stdout + result.stderr
    flat = " ".join(result.stdout.split())
    for contract in (PROVIDER, API):
        assert f"{contract} KEPT" in flat, contract


@pytest.mark.parametrize(
    ("package", "code", "contract"),
    [
        (
            "devteam_services",
            "from devteam_harness.llm_gateway.providers.ollama import OllamaProvider\n",
            PROVIDER,
        ),
        ("devteam_worker", "import devteam_harness.llm_gateway.providers.ollama\n", PROVIDER),
        (
            "devteam_api",
            "from devteam_harness.llm_gateway.providers.ollama import OllamaProvider\n",
            PROVIDER,
        ),
        ("devteam_api", "from devteam_harness.llm_gateway import LLMGateway\n", API),
    ],
)
def test_each_planted_violation_breaks_its_contract(
    tmp_path: Path, package: str, code: str, contract: str
) -> None:
    result = _lint_imports(_planted_copy(tmp_path, package, code))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{contract} BROKEN" in " ".join(result.stdout.split()), result.stdout
