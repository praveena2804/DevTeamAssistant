"""Model providers behind the LLM Gateway (HC-02/GW-01). The only package allowed to import HTTP clients.

* ``fake``: scripted answers for the Part C tests and ``scripts/demo_part_c.py`` only. Workflow runs, the
  worker, the API and agents can never import it (import-linter contract, D-38 as revised).
* ``ollama``: the direct local Ollama adapter (loopback only, no LiteLLM, D-35), brought forward from
  Part F into Part D by owner decision D-44 and completed in Part F (embeddings, inventory, digests).
* ``policy``: which adapters may exist at all (Ollama only; any other is refused, Part F plan §5 F.1).

Providers never choose a model themselves: the gateway's router passes the model tag (Part F).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from devteam_core.contracts.llm import ModelRequest, Provider


@dataclass(frozen=True, slots=True)
class ProviderReply:
    text: str
    input_tokens: int
    output_tokens: int
    model_id: str


@dataclass(frozen=True, slots=True)
class EmbedReply:
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int
    model_id: str


@dataclass(frozen=True, slots=True)
class CallTimeouts:
    connect_s: float
    read_s: float


class ModelProvider(Protocol):
    name: Provider

    def generate(
        self, request: ModelRequest, *, model: str | None = None, timeouts: CallTimeouts | None = None
    ) -> ProviderReply:
        """Answer one request with ``model`` (chosen by the router). Raise ``TransientError`` for
        timeouts/busy, ``ResourceUnavailableError`` for resource problems, ``FatalError`` otherwise."""
        ...

    def close(self) -> None: ...


@runtime_checkable
class LocalModelServer(Protocol):
    """The extra operations of a local model server (GW-01): embeddings, inventory and residency."""

    def embed(
        self, model: str, texts: tuple[str, ...], *, timeouts: CallTimeouts | None = None
    ) -> EmbedReply: ...

    def list_models(self) -> dict[str, str]:
        """Installed model tag → digest (``sha256`` hex)."""
        ...

    def loaded_models(self) -> tuple[str, ...]: ...

    def unload(self, model: str) -> None: ...

    def version(self) -> str: ...


__all__ = ["CallTimeouts", "EmbedReply", "LocalModelServer", "ModelProvider", "ProviderReply"]
