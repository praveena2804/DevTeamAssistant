"""Project-isolated retrieval (HC-08; Spec §10.2; AC-F-03).

A query is embedded through the gateway (``rag.query``), matched in the project's **ready** collection with a
mandatory ``project_id`` filter (and optional document filter), then every hit is **re-read from SQLite**
restricted to the same project: a vector that points at a foreign or deleted chunk is dropped and counted.
The result carries chunk IDs and source references; the text is untrusted data (the executor wraps it).

Fails closed: no store, no ready collection, or a collection built with another embedding model or chunker
→ ``ResourceUnavailableError`` (never stale vectors). ``index_incomplete`` reports documents not yet indexed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.llm import EmbedRequest
from devteam_core.ids import new_id
from devteam_core.logging import get_logger
from devteam_harness.budgets.jobs import JobBudget
from devteam_harness.errors import ResourceUnavailableError
from devteam_harness.llm_gateway.gateway import LLMGateway
from devteam_harness.rag.ingest import RAG_INGESTOR
from devteam_harness.rag.store import VectorStore
from devteam_state.repositories import rag as repo

_log = get_logger("devteam_harness.rag")


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: str
    document_id: str
    reference: str
    text: str
    score: float  # 1 - cosine distance


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    hits: tuple[Hit, ...]
    index_incomplete: bool
    dropped: int  # vector hits not confirmed by SQLite (foreign or deleted chunks)


class Retriever:
    def __init__(
        self,
        gateway: LLMGateway,
        store: VectorStore | None,
        sessions: sessionmaker[Session],
        *,
        chunker_version: str,
        new_budget: Callable[[str], JobBudget],
        max_distance: float = 0.8,
        clock: Clock = system_clock,
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._sessions = sessions
        self._chunker = chunker_version
        self._new_budget = new_budget
        self._max_distance = max_distance
        self._clock = clock

    def search(
        self, project_id: str, query: str, *, limit: int = 6, sources: Sequence[str] = ()
    ) -> SearchOutcome:
        if self._store is None:
            raise ResourceUnavailableError("rag.store_unavailable", "the vector store is not available")
        router = self._gateway.router
        if router is None:
            raise ResourceUnavailableError("rag.embedding_not_configured", "no model router is configured")
        tag = router.embedding().tag
        digest = self._gateway.model_digest(tag)
        with self._sessions() as session:
            ready = repo.ready_collection(session, project_id)
            if ready is None:
                raise ResourceUnavailableError(
                    "rag.index_unavailable", "the project's documents are not indexed yet"
                )
            if (ready.embedding_digest, ready.chunker_version) != (digest, self._chunker):
                raise ResourceUnavailableError(
                    "rag.index_stale",
                    "the index was built with another embedding model or chunker; rebuilding",
                )
            indexed = repo.indexed_documents(session, ready.id)
            pending = any(
                (d.document_id not in indexed or indexed[d.document_id].content_sha256 != d.content_sha256)
                for d in repo.project_documents(session, project_id)
            )
            name = ready.name
        response = self._gateway.embed(
            EmbedRequest(
                request_id=new_id("mrq", self._clock), principal_id=RAG_INGESTOR, purpose="rag.query",
                project_id=project_id, texts=(query,),
            ),
            budget=self._new_budget(project_id),
        )  # fmt: skip
        where: dict[str, object] = {"project_id": project_id}
        if sources:
            where = {"$and": [{"project_id": project_id}, {"document_id": {"$in": list(sources)}}]}
        found = self._store.query(name, response.vectors[0], limit=limit, where=where)
        found = [h for h in found if h.distance <= self._max_distance]
        with self._sessions() as session:
            rows = repo.chunks_by_ids(session, project_id, [h.item_id for h in found])
        hits = tuple(
            Hit(h.item_id, rows[h.item_id].document_id, rows[h.item_id].reference, rows[h.item_id].text,
                round(1.0 - h.distance, 4))
            for h in found
            if h.item_id in rows and (not sources or rows[h.item_id].document_id in sources)
        )  # fmt: skip
        dropped = len(found) - len(hits)
        if dropped:
            _log.warning("rag_hits_dropped", project_id=project_id, dropped=dropped)
        return SearchOutcome(hits=hits, index_incomplete=pending, dropped=dropped)


__all__ = ["Hit", "Retriever", "SearchOutcome"]
