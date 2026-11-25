"""Part F.5 (HC-08; AC-F-03): ingestion, freshness, rebuild, project isolation, references, context budgets,
project memory and the search tool. Embeddings run through the real gateway and ``OllamaProvider`` against a
respx-mocked ``/api/embed`` (deterministic keyword vectors); the vector store is an in-memory test double of
the ``VectorStore`` interface (the ChromaDB implementation is pending the owner's ruling on KI-045).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from devteam_core.config.models import load_model_catalog
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.ids import new_id
from devteam_harness.audit import DatabaseAuditSink
from devteam_harness.budgets.jobs import JobBudget, JobLimits
from devteam_harness.context import ContextItem, ContextManager, budgets_for_roles
from devteam_harness.errors import BudgetExhaustedError, ResourceUnavailableError
from devteam_harness.guardrails.input import InputGuard
from devteam_harness.llm_gateway import LLMGateway
from devteam_harness.llm_gateway.cache import EmbeddingCache
from devteam_harness.llm_gateway.gateway import GatewayOptions
from devteam_harness.llm_gateway.providers.ollama import OllamaProvider
from devteam_harness.llm_gateway.router import Router
from devteam_harness.memory import ProjectMemory
from devteam_harness.rag import Ingestor, Retriever, VectorHit
from devteam_harness.resilience import RetryPolicy
from devteam_state.models import (
    ApprovalRow,
    DocumentChunk,
    ModelCallRow,
    Project,
    SourceDocument,
    SpecVersionRow,
)
from devteam_state.repositories import rag as repo
from tests.fixtures.harness import REPO_ROOT, Stack, make_stack

pytestmark = pytest.mark.security
BASE = "http://127.0.0.1:11434"
WORDS = ("loan", "book", "member", "fee", "catalogue")


def _vector(text: str) -> list[float]:
    t = text.lower()
    return [float(t.count(w)) for w in WORDS] + [0.1]


class MemoryStore:
    """In-memory ``VectorStore`` (cosine). ``ignore_filter`` simulates a store dropping the project filter."""

    def __init__(self, *, ignore_filter: bool = False) -> None:
        self.collections: dict[str, dict[str, tuple[list[float], dict[str, Any]]]] = {}
        self.ignore_filter = ignore_filter

    def ensure(self, name: str) -> None:
        self.collections.setdefault(name, {})

    def drop(self, name: str) -> None:
        self.collections.pop(name, None)

    def upsert(self, name: str, ids: Sequence[str], vectors: Sequence[Sequence[float]],
               metadatas: Sequence[Mapping[str, str | int]]) -> None:  # fmt: skip
        for item_id, vector, meta in zip(ids, vectors, metadatas, strict=True):
            self.collections[name][item_id] = (list(vector), dict(meta))

    def delete_where(self, name: str, where: Mapping[str, str]) -> None:
        col = self.collections.get(name, {})
        for item_id in [i for i, (_v, m) in col.items() if all(m.get(k) == v for k, v in where.items())]:
            del col[item_id]

    def _match(self, meta: Mapping[str, Any], where: Mapping[str, Any]) -> bool:
        if self.ignore_filter:
            return True
        if "$and" in where:
            return all(self._match(meta, w) for w in where["$and"])
        for key, value in where.items():
            if isinstance(value, dict) and "$in" in value:
                if meta.get(key) not in value["$in"]:
                    return False
            elif meta.get(key) != value:
                return False
        return True

    def query(
        self, name: str, vector: Sequence[float], *, limit: int, where: Mapping[str, object]
    ) -> list[VectorHit]:
        def distance(a: Sequence[float], b: Sequence[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b, strict=True))
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
            return 1.0 - dot / (na * nb) if na and nb else 1.0

        pool: list[VectorHit] = []
        for col in self.collections.values() if self.ignore_filter else [self.collections.get(name, {})]:
            pool += [
                VectorHit(i, distance(vector, v), m) for i, (v, m) in col.items() if self._match(m, where)
            ]
        return sorted(pool, key=lambda h: h.distance)[:limit]

    def count(self, name: str) -> int:
        return len(self.collections.get(name, {}))


def _job(project: str | None) -> JobBudget:
    return JobBudget(
        JobLimits(
            max_model_calls=500, max_tokens=10**7, max_minutes=30, model_call_timeout_s=60, min_free_ram_mb=0
        ),
        project_id=project,
    )


@pytest.fixture
def world(tmp_path: Path) -> Iterator[dict[str, Any]]:
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        digests = {"value": "e" * 64}
        mock.get("/api/tags").mock(
            side_effect=lambda _r: httpx.Response(
                200, json={"models": [{"name": "nomic-embed-text:latest", "digest": digests["value"]}]}
            )
        )
        mock.get("/api/ps").mock(return_value=httpx.Response(200, json={"models": []}))
        embed = mock.post("/api/embed").mock(
            side_effect=lambda r: httpx.Response(
                200,
                json={
                    "embeddings": [_vector(t) for t in json.loads(r.content)["input"]],
                    "prompt_eval_count": 3,
                },
            )
        )
        stack = make_stack(tmp_path)
        provider = OllamaProvider(BASE, {ModelSlot.INSTRUCT: "qwen2.5:3b"}, timeout_s=30, keep_alive="0s")
        gateway = LLMGateway(
            provider, InputGuard(stack.guardrails.input), DatabaseAuditSink(stack.sessions), stack.tracer,
            RetryPolicy(stack.budgets.budgets.retries, jitter=lambda: 0.0), sleep=lambda _s: None,
            router=Router(load_model_catalog(REPO_ROOT / "config", max_model_ram_mb=3072)),
            sessions=stack.sessions, embedding_cache=EmbeddingCache(stack.sessions),
            options=GatewayOptions(digest_refresh_s=0.0), free_ram=lambda: 64_000,
        )  # fmt: skip
        yield {"stack": stack, "gateway": gateway, "embed": embed, "digests": digests}
        provider.close()


def _add_document(stack: Stack, project: str, texts: list[str], *, version: str = "1") -> str:
    doc = new_id("doc")
    sha = hashlib.sha256("|".join(texts).encode()).hexdigest()
    now = datetime.now(UTC)
    with stack.sessions.begin() as session:
        session.add(
            SourceDocument(
                id=doc, project_id=project, role="primary_brd", original_filename="brd.md",
                stored_name=f"{doc}.md", media_type="text/markdown", size_bytes=100, sha256=sha,
                status="extracted", chunk_count=len(texts), char_count=100, warnings=[], outline=[],
                extractor="md", extractor_version="1", uploaded_by=stack.owner_id, created_at=now,
            )
        )  # fmt: skip
        session.flush()
        for n, text in enumerate(texts):
            session.add(
                DocumentChunk(
                    id=f"chk_{hashlib.sha256(f'{doc}:{n}'.encode()).hexdigest()}", project_id=project,
                    document_id=doc,
                    ordinal=n, kind="text", text=text, section_path="", paragraph_start=n, paragraph_end=n,
                    reference=f"brd.md ¶{n + 1}", content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    chunker_version=version,
                )
            )  # fmt: skip
    return doc


def _other_project(stack: Stack) -> str:
    project = new_id("prj")
    now = datetime.now(UTC)
    with stack.sessions.begin() as session:
        session.add(Project(id=project, name="Other", name_key=f"other-{project}", description="",
                            status="active",
                            created_by=stack.owner_id, created_at=now, updated_at=now))  # fmt: skip
    return project


def _tools(
    world: dict[str, Any], store: MemoryStore | None, chunker: str = "1"
) -> tuple[Ingestor, Retriever]:
    stack: Stack = world["stack"]
    ingestor = Ingestor(world["gateway"], store or MemoryStore(), stack.sessions, chunker_version=chunker,
                        new_budget=_job, batch_size=2)  # fmt: skip
    retriever = Retriever(world["gateway"], store, stack.sessions, chunker_version=chunker, new_budget=_job)
    return ingestor, retriever


LIB = ["A member may borrow at most five books on loan.", "The librarian edits the catalogue of books.",
       "A loan lasts fourteen days."]  # fmt: skip


def test_ingestion_is_idempotent_and_every_embedding_is_a_recorded_gateway_call(
    world: dict[str, Any],
) -> None:
    stack, store = world["stack"], MemoryStore()
    _add_document(stack, stack.project_id, LIB)
    ingestor, _ = _tools(world, store)
    first = ingestor.index_project(stack.project_id)
    second = ingestor.index_project(stack.project_id)
    assert first.rebuilt and len(first.indexed) == 1 and first.chunks == 3
    assert not second.rebuilt and second.indexed == [] and len(second.unchanged) == 1
    assert world["embed"].call_count == 2  # 3 chunks in batches of 2; nothing re-embedded the second time
    with stack.sessions() as session:
        calls = session.query(ModelCallRow).all()
    assert [c.kind for c in calls] == ["embed", "embed"] and {c.service for c in calls} == {"rag_ingestor"}


def test_retrieval_returns_sqlite_references_ranked_and_project_scoped(world: dict[str, Any]) -> None:
    stack, store = world["stack"], MemoryStore()
    doc = _add_document(stack, stack.project_id, LIB)
    other = _other_project(stack)
    _add_document(stack, other, ["loan loan loan loan fee", "loan member book", "loan book fee", "loan"] * 3)
    ingestor, retriever = _tools(world, store)
    ingestor.index_project(stack.project_id)
    ingestor.index_project(other)
    outcome = retriever.search(stack.project_id, "how long is a loan", limit=3)
    assert outcome.hits and outcome.dropped == 0 and not outcome.index_incomplete
    with stack.sessions() as session:
        own = {
            c.id: c for c in session.query(DocumentChunk).filter(DocumentChunk.project_id == stack.project_id)
        }
    assert all(h.chunk_id in own and h.document_id == doc for h in outcome.hits)  # AC-F-03: own project only
    assert all(
        h.reference == own[h.chunk_id].reference and h.text == own[h.chunk_id].text for h in outcome.hits
    )
    assert outcome.hits[0].text == "A loan lasts fourteen days."  # best match first


def test_isolation_holds_even_if_the_store_ignored_the_project_filter(world: dict[str, Any]) -> None:
    """Negative control: a store that returns other projects' vectors is caught by the SQLite cross-check."""
    stack = world["stack"]
    leaky = MemoryStore(ignore_filter=True)
    _add_document(stack, stack.project_id, LIB)
    other = _other_project(stack)
    _add_document(stack, other, ["loan " * 9] * 4)
    ingestor, retriever = _tools(world, leaky)
    ingestor.index_project(stack.project_id)
    ingestor.index_project(other)
    outcome = retriever.search(stack.project_id, "loan", limit=8)
    assert outcome.dropped > 0  # foreign vectors were returned by the store …
    with stack.sessions() as session:
        own = {
            c.id for c in session.query(DocumentChunk).filter(DocumentChunk.project_id == stack.project_id)
        }
    assert {h.chunk_id for h in outcome.hits} <= own  # … but never reach the caller


def test_a_new_upload_is_reported_until_it_is_indexed(world: dict[str, Any]) -> None:
    stack, store = world["stack"], MemoryStore()
    _add_document(stack, stack.project_id, LIB)
    ingestor, retriever = _tools(world, store)
    ingestor.index_project(stack.project_id)
    _add_document(stack, stack.project_id, ["Members pay a fee for late returns."])
    assert retriever.search(stack.project_id, "fee").index_incomplete
    ingestor.index_project(stack.project_id)
    outcome = retriever.search(stack.project_id, "late fee")
    assert not outcome.index_incomplete and "fee" in outcome.hits[0].text


def test_a_changed_embedding_model_makes_the_index_stale_until_rebuilt(world: dict[str, Any]) -> None:
    stack, store = world["stack"], MemoryStore()
    _add_document(stack, stack.project_id, LIB)
    ingestor, retriever = _tools(world, store)
    old = ingestor.index_project(stack.project_id).collection
    world["digests"]["value"] = "f" * 64  # the owner updated the embedding model
    with pytest.raises(ResourceUnavailableError) as info:
        retriever.search(stack.project_id, "loan")
    assert info.value.code == "rag.index_stale"  # never stale vectors
    rebuilt = ingestor.index_project(stack.project_id)
    assert rebuilt.rebuilt and rebuilt.collection != old and old not in store.collections
    assert retriever.search(stack.project_id, "loan").hits


def test_documents_from_another_chunker_are_skipped_not_mixed(world: dict[str, Any]) -> None:
    stack, store = world["stack"], MemoryStore()
    _add_document(stack, stack.project_id, LIB, version="1")
    ingestor, _ = _tools(world, store, chunker="2")
    result = ingestor.index_project(stack.project_id)
    assert result.indexed == [] and len(result.skipped) == 1 and result.chunks == 0


def test_fail_closed_without_a_store_or_an_index(world: dict[str, Any]) -> None:
    stack = world["stack"]
    _ingestor, no_store = _tools(world, None)
    with pytest.raises(ResourceUnavailableError, match="vector store"):
        no_store.search(stack.project_id, "loan")
    _i, retriever = _tools(world, MemoryStore())
    with pytest.raises(ResourceUnavailableError) as info:
        retriever.search(stack.project_id, "loan")
    assert info.value.code == "rag.index_unavailable"


def test_dropping_a_project_removes_its_collections(world: dict[str, Any]) -> None:
    stack, store = world["stack"], MemoryStore()
    _add_document(stack, stack.project_id, LIB)
    ingestor, _ = _tools(world, store)
    ingestor.index_project(stack.project_id)
    assert ingestor.drop_project(stack.project_id) == 1 and store.collections == {}
    with stack.sessions() as session:
        assert repo.collections(session, stack.project_id) == []


def test_the_search_tool_without_a_store_fails_closed_and_uses_the_tasks_project(
    world: dict[str, Any],
) -> None:
    from devteam_harness.tools import build_default_registry  # noqa: PLC0415

    registry = build_default_registry()
    spec = registry.get("search_project_knowledge", 2)
    assert spec is not None
    assert spec.version == 2 and spec.timeout_s == 30.0
    assert "project_id" not in spec.input_model.model_fields  # the project never comes from arguments
    store = MemoryStore()
    stack = world["stack"]
    _add_document(stack, stack.project_id, LIB)
    ingestor, retriever = _tools(world, store)
    ingestor.index_project(stack.project_id)
    live = build_default_registry(retriever=retriever).get("search_project_knowledge", 2)
    assert live is not None
    search_args = live.input_model(query="loan", limit=2)

    class _Ctx:
        class task:  # noqa: N801
            project_id = stack.project_id

    class _Inv:
        ctx = _Ctx
        args = search_args

    result: Any = live.handler(_Inv)  # type: ignore[arg-type]
    assert len(result.hits) == 2 and all(h.reference.startswith("brd.md") for h in result.hits)


# ----------------------------------------------------------------------------------- context + memory


def test_context_keeps_required_first_and_drops_whole_items_by_score() -> None:
    manager = ContextManager(budgets_for_roles(100, discovery=(40, 10), build=(20, 10), documenter=(10, 10)),
                             estimate=len)  # fmt: skip
    assembled = manager.assemble(
        AgentRole.BRD_ANALYST,
        required=[ContextItem("required", "task", "x" * 50)],
        retrieved=[
            ContextItem("retrieved", "low", "y" * 30, score=0.2),
            ContextItem("retrieved", "high", "z" * 30, score=0.9),
        ],
        memory=[ContextItem("memory", "m1", "m" * 8), ContextItem("memory", "m2", "n" * 8)],
    )  # fmt: skip
    assert [i.label for i in assembled.items] == ["task", "high", "m1"]
    assert assembled.dropped == ("low", "m2") and assembled.tokens == 88
    with pytest.raises(BudgetExhaustedError):
        manager.assemble(AgentRole.HEAD, required=[ContextItem("required", "task", "x" * 101)])


def test_project_memory_lists_frozen_specs_and_decisions_without_document_text(world: dict[str, Any]) -> None:
    stack: Stack = world["stack"]
    now = datetime.now(UTC)
    with stack.sessions.begin() as session:
        spec = SpecVersionRow(
            id=new_id("spv"), project_id=stack.project_id, run_id=stack.run_id, version=7, status="draft",
            checksum="c" * 64, payload={"requirement_keys": ["FR-001", "BR-001"]}, created_at=now,
        )  # fmt: skip
        session.add(spec)
        approval = ApprovalRow(
            id=new_id("apr"), project_id=stack.project_id, run_id=stack.run_id,
            approval_type="project_approval",
            spec_version_id=spec.id, spec_checksum="c" * 64, decision_key="k1", decision="approved",
            decided_by=stack.owner_id, decided_at=now, audit_event_id=new_id("aud"),
        )  # fmt: skip
        session.add(approval)
        session.flush()
        # a version is created as a draft and frozen only with its approval recorded (database trigger)
        spec.status, spec.frozen_at = "frozen", now
        spec.approval_id, spec.approved_by = approval.id, stack.owner_id
    facts = ProjectMemory(stack.sessions).facts(stack.project_id)
    texts = [f.text for f in facts]
    assert any("v7" in t and "FR-001, BR-001" in t for t in texts) and any(
        "project_approval" in t for t in texts
    )


def test_retrieval_is_project_scoped_with_the_real_chromadb(world: dict[str, Any], tmp_path: Path) -> None:
    """AC-F-03 on the real store (D-54): the same scenario as above, ChromaDB in-process."""
    from devteam_harness.rag.store import ChromaVectorStore  # noqa: PLC0415

    stack = world["stack"]
    store = ChromaVectorStore(tmp_path / "chroma")
    doc = _add_document(stack, stack.project_id, LIB)
    other = _other_project(stack)
    _add_document(stack, other, ["loan loan loan loan fee", "loan member book", "loan book fee", "loan"] * 3)
    ingestor, retriever = _tools(world, store)  # type: ignore[arg-type]
    ingestor.index_project(stack.project_id)
    ingestor.index_project(other)
    outcome = retriever.search(stack.project_id, "how long is a loan", limit=3)
    assert outcome.hits and outcome.dropped == 0 and {h.document_id for h in outcome.hits} == {doc}
    assert outcome.hits[0].text == "A loan lasts fourteen days."
    assert ingestor.drop_project(other) == 1
