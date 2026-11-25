"""Vector store interface (HC-08). Implementations hold vectors and metadata only; never the source of truth.

:class:`ChromaVectorStore` is the ChromaDB implementation (D-54 (a); risk accepted in AP-024, KI-045): the
**only** module allowed to import ``chromadb`` (import contract), imported lazily. It runs in-process only:
no Chroma server, no auth provider, no built-in embedding function (collections are created without one,
vectors always come from the LLM Gateway), anonymised telemetry off, and start-up is refused if any
``CHROMA_*`` environment variable could redirect the client. Without a store the runtime fails closed
(``rag.store_unavailable``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from devteam_core.errors import ConfigError


@dataclass(frozen=True, slots=True)
class VectorHit:
    item_id: str
    distance: float  # cosine distance: 0 = identical
    metadata: Mapping[str, str | int]


class VectorStore(Protocol):
    def ensure(self, name: str) -> None:
        """Create the collection if it does not exist (cosine space, no built-in embedding function)."""
        ...

    def drop(self, name: str) -> None: ...

    def upsert(
        self,
        name: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        metadatas: Sequence[Mapping[str, str | int]],
    ) -> None: ...

    def delete_where(self, name: str, where: Mapping[str, str]) -> None: ...

    def query(
        self, name: str, vector: Sequence[float], *, limit: int, where: Mapping[str, object]
    ) -> list[VectorHit]: ...

    def count(self, name: str) -> int: ...


_ENV_PREFIXES = ("CHROMA_", "ANONYMIZED_TELEMETRY", "IS_PERSISTENT", "PERSIST_DIRECTORY")


class ChromaVectorStore:
    """In-process ChromaDB at ``path`` (cosine collections, caller-supplied vectors only)."""

    def __init__(self, path: Path) -> None:
        offending = sorted(k for k in os.environ if k.upper().startswith(_ENV_PREFIXES))
        if offending:
            raise ConfigError(
                f"ChromaDB environment variables are not allowed (they could redirect it): {offending}"
            )
        self._path = path
        self._client: Any = None

    def _api(self) -> Any:
        if self._client is None:
            import chromadb  # noqa: PLC0415 - lazy: only loaded when retrieval is used
            from chromadb.config import Settings as ChromaSettings  # noqa: PLC0415

            self._path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(self._path),
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False, is_persistent=True),
            )
        return self._client

    def _collection(self, name: str) -> Any:
        return self._api().get_or_create_collection(
            name=name, embedding_function=None, metadata={"hnsw:space": "cosine"}
        )

    def ensure(self, name: str) -> None:
        self._collection(name)

    def drop(self, name: str) -> None:
        api = self._api()
        if name in {c.name if hasattr(c, "name") else str(c) for c in api.list_collections()}:
            api.delete_collection(name)

    def upsert(
        self,
        name: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        metadatas: Sequence[Mapping[str, str | int]],
    ) -> None:
        if ids:
            self._collection(name).upsert(
                ids=list(ids), embeddings=[list(v) for v in vectors], metadatas=[dict(m) for m in metadatas]
            )

    def delete_where(self, name: str, where: Mapping[str, str]) -> None:
        self._collection(name).delete(where=dict(where))

    def query(
        self, name: str, vector: Sequence[float], *, limit: int, where: Mapping[str, object]
    ) -> list[VectorHit]:
        collection = self._collection(name)
        if collection.count() == 0:
            return []
        result = collection.query(
            query_embeddings=[list(vector)],
            n_results=limit,
            where=dict(where),
            include=["metadatas", "distances"],
        )
        ids, distances, metas = result["ids"][0], result["distances"][0], result["metadatas"][0]
        return [VectorHit(i, float(d), dict(m or {})) for i, d, m in zip(ids, distances, metas, strict=True)]

    def count(self, name: str) -> int:
        return int(self._collection(name).count())


__all__ = ["ChromaVectorStore", "VectorHit", "VectorStore"]
