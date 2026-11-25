"""Part F.5 with the real ChromaDB (D-54 (a); KI-045, AP-024): in-process only, no telemetry, no built-in
embedding function, no outbound connection, environment cannot redirect it, project-filtered queries, and
only ``devteam_harness.rag.store`` may import it."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from devteam_core.errors import ConfigError
from devteam_harness.rag.store import ChromaVectorStore
from tests.fixtures.harness import REPO_ROOT
from tests.security.test_gateway_static_rules import _planted_in
from tests.security.test_orchestration_static_rules import _lint_imports, _planted_copy

pytestmark = pytest.mark.security
JAIL = REPO_ROOT / "tests" / "fixtures" / "loopback_only"
CONTRACT = "only rag.store imports ChromaDB (D-54, KI-045)"


def test_store_operations_and_project_filter(tmp_path: Path) -> None:
    store = ChromaVectorStore(tmp_path / "chroma")
    store.ensure("p_a")
    store.upsert(
        "p_a",
        ["a1", "a2", "b1"],
        [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [1.0, 0.0, 0.0]],
        [
            {"project_id": "A", "document_id": "d1", "ordinal": 0},
            {"project_id": "A", "document_id": "d2", "ordinal": 1},
            {"project_id": "B", "document_id": "d9", "ordinal": 0},
        ],
    )
    hits = store.query("p_a", [1.0, 0.0, 0.0], limit=5, where={"project_id": "A"})
    assert [h.item_id for h in hits] == ["a1", "a2"] and hits[0].distance == pytest.approx(0.0, abs=1e-6)
    both = {"$and": [{"project_id": "A"}, {"document_id": {"$in": ["d2"]}}]}
    only_d2 = store.query("p_a", [1.0, 0.0, 0.0], limit=5, where=both)
    assert [h.item_id for h in only_d2] == ["a2"]
    store.delete_where("p_a", {"document_id": "d1"})
    assert store.count("p_a") == 2
    assert store._collection("p_a")._embedding_function is None
    store.drop("p_a")
    store.drop("p_a")  # dropping twice is fine
    assert store.query("p_a", [1.0, 0.0, 0.0], limit=5, where={"project_id": "A"}) == []


def test_chroma_environment_variables_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHROMA_SERVER_HOST", "203.0.113.7")
    with pytest.raises(ConfigError, match="CHROMA_SERVER_HOST"):
        ChromaVectorStore(tmp_path / "chroma")


_PROBE = """
import sys
from pathlib import Path
from devteam_harness.rag.store import ChromaVectorStore
store = ChromaVectorStore(Path(sys.argv[1]))
store.ensure("p_x")
store.upsert("p_x", ["x"], [[0.1, 0.2]], [{"project_id": "X"}])
assert [h.item_id for h in store.query("p_x", [0.1, 0.2], limit=1, where={"project_id": "X"})] == ["x"]
if len(sys.argv) > 2:  # negative control: prove the jail records a real outbound attempt
    import socket
    try:
        socket.create_connection(("192.0.2.1", 80), timeout=1)
    except OSError:
        pass
print("ok")
"""


def _probe(tmp_path: Path, *extra: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    log = tmp_path / "net.log"
    env = {**os.environ, "E2E_NET_JAIL_LOG": str(log),
           "PYTHONPATH": os.pathsep.join([str(JAIL), os.environ.get("PYTHONPATH", "")])}  # fmt: skip
    env = {k: v for k, v in env.items() if not k.upper().startswith("CHROMA_")}
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(tmp_path / "chroma"), *extra],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    return result, log


def test_chroma_makes_no_outbound_connection(tmp_path: Path) -> None:
    result, log = _probe(tmp_path)
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr[-2000:]
    assert not log.exists() or log.read_text(encoding="utf-8") == ""  # telemetry off, nothing downloaded


def test_the_network_jail_negative_control_records_an_attempt(tmp_path: Path) -> None:
    result, log = _probe(tmp_path / "neg", "attempt")
    assert result.returncode == 0, result.stderr[-2000:]
    assert "192.0.2.1" in log.read_text(encoding="utf-8")


def test_the_chromadb_contract_holds_and_breaks_on_a_planted_import(tmp_path: Path) -> None:
    flat = " ".join(_lint_imports(None).stdout.split())
    assert f"{CONTRACT} KEPT" in flat
    for src in (
        _planted_copy(tmp_path / "a", "devteam_services", "import chromadb\n"),
        _planted_in(tmp_path / "b", "rag", "import chromadb\n"),  # rag.ingest-like module, not rag.store
    ):
        result = _lint_imports(src)
        assert result.returncode == 1 and f"{CONTRACT} BROKEN" in " ".join(result.stdout.split()), (
            result.stdout
        )
