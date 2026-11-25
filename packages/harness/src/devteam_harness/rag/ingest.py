"""Index a project's extracted documents (HC-08; Spec §10.2). Run by the worker, never by the API.

Incremental and idempotent: a document whose content checksum and chunker version already sit in the ready
collection is skipped; a changed document is replaced; a removed document is deleted from the index. When
the embedding model digest or the chunker version changes, a **new** collection is built in full and the
old one is retired only after the new one is ready (retrieval never sees a half-built index).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.llm import EmbedRequest
from devteam_core.ids import new_id
from devteam_harness.budgets.jobs import JobBudget
from devteam_harness.errors import ResourceUnavailableError
from devteam_harness.llm_gateway.gateway import LLMGateway
from devteam_harness.rag.store import VectorStore
from devteam_state.models import RagCollectionRow
from devteam_state.repositories import rag as repo

RAG_INGESTOR = "service:rag_ingestor"


def collection_name(project_id: str, chunker_version: str, digest: str) -> str:
    return f"p_{project_id.lower()}__c{chunker_version}__e{digest[:12]}"


@dataclass
class IndexResult:
    collection: str
    rebuilt: bool = False
    indexed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # chunked by another chunker version: re-extract first
    chunks: int = 0


class Ingestor:
    def __init__(
        self,
        gateway: LLMGateway,
        store: VectorStore,
        sessions: sessionmaker[Session],
        *,
        chunker_version: str,
        new_budget: Callable[[str], JobBudget],
        batch_size: int = 16,
        clock: Clock = system_clock,
    ) -> None:
        self._gateway = gateway
        self._store = store
        self._sessions = sessions
        self._chunker = chunker_version
        self._new_budget = new_budget
        self._batch = batch_size
        self._clock = clock

    def _identity(self) -> tuple[str, str]:
        router = self._gateway.router
        if router is None:
            raise ResourceUnavailableError("rag.embedding_not_configured", "no model router is configured")
        tag = router.embedding().tag
        digest = self._gateway.model_digest(tag)
        if digest is None:
            raise ResourceUnavailableError(
                "rag.embedding_unavailable", f"the embedding model '{tag}' is not installed or not reachable"
            )
        return tag, digest

    def index_project(self, project_id: str, *, rebuild: bool = False) -> IndexResult:
        tag, digest = self._identity()
        name = collection_name(project_id, self._chunker, digest)
        now = self._clock.now()
        with self._sessions.begin() as session:
            ready = repo.ready_collection(session, project_id)
            documents = repo.project_documents(session, project_id)
            if ready is not None and ready.name == name and not rebuild:
                target, building = ready, False
            else:
                stale_builds = [c for c in repo.collections(session, project_id) if c.status == "building"]
                for old in stale_builds:  # an earlier build that never finished
                    self._store.drop(old.name)
                    repo.drop_collection_record(session, old.id)
                if ready is not None and ready.name == name:  # forced rebuild of the same identity
                    name = f"{name}__r{now.strftime('%Y%m%d%H%M%S')}"
                target = RagCollectionRow(
                    id=new_id("rcl", self._clock), project_id=project_id, name=name, embedding_model=tag,
                    embedding_digest=digest, chunker_version=self._chunker, dimensions=None,
                    status="building",
                    created_at=now, updated_at=now,
                )  # fmt: skip
                repo.add_collection(session, target)
                building = True
            target_id, target_name = target.id, target.name
            have = repo.indexed_documents(session, target_id)
        self._store.ensure(target_name)
        result = IndexResult(collection=target_name, rebuilt=building)
        budget = self._new_budget(project_id)
        current = {d.document_id: d for d in documents}
        for document in documents:
            if document.chunker_version != self._chunker:
                result.skipped.append(document.document_id)
                continue
            known = have.get(document.document_id)
            if known and (known.content_sha256, known.chunker_version) == (
                document.content_sha256,
                self._chunker,
            ):
                result.unchanged.append(document.document_id)
                continue
            if known:
                self._store.delete_where(target_name, {"document_id": document.document_id})
            result.chunks += self._index_document(project_id, target_name, document.document_id, budget)
            with self._sessions.begin() as session:
                repo.record_document(
                    session,
                    collection_id=target_id,
                    project_id=project_id,
                    state=document,
                    now=self._clock.now(),
                )
            result.indexed.append(document.document_id)
        for document_id in sorted(set(have) - set(current)):
            self._store.delete_where(target_name, {"document_id": document_id})
            with self._sessions.begin() as session:
                repo.forget_document(session, target_id, document_id)
            result.removed.append(document_id)
        if building:
            with self._sessions.begin() as session:
                repo.set_collection_status(session, target_id, "ready", now=self._clock.now())
                for old in repo.collections(session, project_id):
                    if old.id != target_id and old.status == "ready":
                        self._store.drop(old.name)
                        repo.drop_collection_record(session, old.id)
        return result

    def _index_document(self, project_id: str, name: str, document_id: str, budget: JobBudget) -> int:
        with self._sessions() as session:
            chunks = [(c.id, c.text, c.ordinal) for c in repo.chunks_of(session, project_id, document_id)]
        for start in range(0, len(chunks), self._batch):
            batch = chunks[start : start + self._batch]
            response = self._gateway.embed(
                EmbedRequest(
                    request_id=new_id("mrq", self._clock), principal_id=RAG_INGESTOR, purpose="rag.index",
                    project_id=project_id, texts=tuple(text[:8_000] for _id, text, _o in batch),
                ),
                budget=budget,
            )  # fmt: skip
            self._store.upsert(
                name,
                [chunk_id for chunk_id, _t, _o in batch],
                [list(v) for v in response.vectors],
                [{"project_id": project_id, "document_id": document_id, "ordinal": o} for _c, _t, o in batch],
            )
        return len(chunks)

    def drop_project(self, project_id: str) -> int:
        """Remove every collection of a project (project deletion)."""
        with self._sessions.begin() as session:
            rows = repo.collections(session, project_id)
            for row in rows:
                self._store.drop(row.name)
                repo.drop_collection_record(session, row.id)
        return len(rows)


__all__ = ["RAG_INGESTOR", "IndexResult", "Ingestor", "collection_name"]
