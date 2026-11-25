"""LLM response cache (D-28) and embedding cache (HC-18; Part F plan §6.3). Local SQLite only.

**Response cache**: off unless ``llm.response_cache`` is enabled (D-28). Key = SHA-256 over project, model
digest, prompt hash, input hash, parameters and schema name. A hit returns an earlier *real* model answer,
which the gateway **re-validates** against the contract before use (D-28 ruling: not a prepared answer
under D-38). Entries are scoped per project (a key never matches another project) and are only usable with
the same model digest and prompt hash, so a model or prompt change misses. Text containing a detected
secret is never stored.

**Embedding cache**: always on. Key = project + embedding-model digest + text hash; vectors are stored as
little-endian float32.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.logging import redact_text, secret_registry
from devteam_state.models import EmbeddingCacheRow, LlmResponseCacheRow


def contains_secret(text: str) -> bool:
    """True if redaction would change ``text`` (a registered secret or a token pattern)."""
    return redact_text(text, secret_registry.values()) != text


def response_key(
    *, project_id: str, model_digest: str, prompt_hash: str, input_hash: str, params: Mapping[str, object],
    schema_name: str | None,
) -> str:  # fmt: skip
    material = json.dumps(
        [project_id, model_digest, prompt_hash, input_hash, dict(sorted(params.items())), schema_name],
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    text: str
    input_tokens: int
    output_tokens: int


class ResponseCache:
    def __init__(self, sessions: sessionmaker[Session], *, clock: Clock = system_clock) -> None:
        self._sessions = sessions
        self._clock = clock

    def get(self, key: str, *, project_id: str) -> CachedAnswer | None:
        with self._sessions.begin() as session:
            row = session.get(LlmResponseCacheRow, key)
            if row is None or row.project_id != project_id:
                return None
            session.execute(
                update(LlmResponseCacheRow).where(LlmResponseCacheRow.key == key).values(hits=row.hits + 1)
            )
            return CachedAnswer(row.output_text, row.input_tokens, row.output_tokens)

    def put(
        self, key: str, *, project_id: str, model_digest: str, prompt_hash: str, text: str,
        input_tokens: int, output_tokens: int,
    ) -> bool:  # fmt: skip
        if contains_secret(text):
            return False
        with self._sessions.begin() as session:
            if session.get(LlmResponseCacheRow, key) is not None:
                return True
            session.add(
                LlmResponseCacheRow(
                    key=key, project_id=project_id, model_digest=model_digest, prompt_hash=prompt_hash,
                    output_text=text, input_tokens=input_tokens, output_tokens=output_tokens,
                    created_at=self._clock.now(), hits=0,
                )
            )  # fmt: skip
        return True

    def forget_project(self, project_id: str) -> int:
        with self._sessions.begin() as session:
            result = session.execute(
                delete(LlmResponseCacheRow).where(LlmResponseCacheRow.project_id == project_id)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]


def embedding_key(project_id: str, model_digest: str, text: str) -> str:
    material = f"{project_id}\0{model_digest}\0{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _pack(vector: Iterable[float]) -> bytes:
    values = list(vector)
    return struct.pack(f"<{len(values)}f", *values)


def _unpack(data: bytes, dims: int) -> tuple[float, ...]:
    return struct.unpack(f"<{dims}f", data)


class EmbeddingCache:
    def __init__(self, sessions: sessionmaker[Session], *, clock: Clock = system_clock) -> None:
        self._sessions = sessions
        self._clock = clock

    def get_many(
        self, project_id: str, model_digest: str, texts: Iterable[str]
    ) -> dict[str, tuple[float, ...]]:
        keys = {embedding_key(project_id, model_digest, t): t for t in texts}
        if not keys:
            return {}
        with self._sessions() as session:
            rows = session.scalars(select(EmbeddingCacheRow).where(EmbeddingCacheRow.key.in_(list(keys))))
            return {
                keys[r.key]: _unpack(r.vector, r.dimensions)
                for r in rows
                if r.project_id == project_id and r.model_digest == model_digest
            }

    def put_many(self, project_id: str, model_digest: str, items: Mapping[str, tuple[float, ...]]) -> int:
        stored = 0
        with self._sessions.begin() as session:
            for text, vector in items.items():
                if contains_secret(text):
                    continue
                key = embedding_key(project_id, model_digest, text)
                if session.get(EmbeddingCacheRow, key) is not None:
                    continue
                session.add(
                    EmbeddingCacheRow(
                        key=key, project_id=project_id, model_digest=model_digest, dimensions=len(vector),
                        vector=_pack(vector), created_at=self._clock.now(),
                    )
                )  # fmt: skip
                stored += 1
        return stored

    def forget_project(self, project_id: str) -> int:
        with self._sessions.begin() as session:
            result = session.execute(
                delete(EmbeddingCacheRow).where(EmbeddingCacheRow.project_id == project_id)
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]


__all__ = [
    "CachedAnswer",
    "EmbeddingCache",
    "ResponseCache",
    "contains_secret",
    "embedding_key",
    "response_key",
]
