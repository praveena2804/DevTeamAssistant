"""Retrieval over project knowledge (HC-08; Spec §10; Part F plan §6.4).

* SQLite is the source of truth (chunks, references, manifest). The vector store only holds vectors and
  metadata, behind :class:`~devteam_harness.rag.store.VectorStore`.
* One collection per project and identity (embedding model digest + chunker version). A change of either
  builds a new collection and retires the old one only after the new one is complete.
* Every embedding goes through the LLM Gateway (``embed``) as the ``service:rag_ingestor`` principal under a
  job budget; this package never reaches a model provider (import contract).
* Retrieval always filters by project, re-reads text and references from SQLite, and reports when the index
  is incomplete (a document not yet indexed) or unavailable (fail closed, never stale vectors).
"""

from devteam_harness.rag.ingest import IndexResult, Ingestor
from devteam_harness.rag.retrieve import Hit, Retriever, SearchOutcome
from devteam_harness.rag.store import VectorHit, VectorStore

__all__ = ["Hit", "IndexResult", "Ingestor", "Retriever", "SearchOutcome", "VectorHit", "VectorStore"]
