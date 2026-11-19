# ADR-0004: Platform stack

- **Status:** Accepted. Mandated by the owner (roadmap §9.3; Spec §8).
- **Date:** 2026-11-19

## Decision

The DevTeam AI **platform** uses:

| Layer | Technology |
|---|---|
| UI | React + TypeScript + Vite |
| API | Python 3.12 + FastAPI + Pydantic v2 |
| Orchestration | LangGraph |
| Local LLM serving | Ollama |
| Platform state | SQLite (SQLAlchemy 2 + Alembic) |
| Retrieval | ChromaDB (embedded) |
| Generated-code isolation | Docker sandbox |
| Tracing | OpenTelemetry API with a local exporter (D-30) |
| Secrets | `keyring` (Windows Credential Manager) |

There are no paid services. PyMuPDF (AGPL) is excluded.

The **generated target application** stack is separate. It is governed by the Capability Catalog and must never be confused with the platform stack.

## Consequences

- Exact versions are recorded in `uv.lock` and `apps/web/package-lock.json`.
- Embedding and LLM model tags are chosen by benchmark and recorded in configuration; they are never hardcoded as permanent requirements.
