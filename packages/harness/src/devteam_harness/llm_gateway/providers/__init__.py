"""Model providers behind the LLM Gateway (HC-02). The only package allowed to import HTTP clients.

* ``fake``: scripted answers for the Part C tests and ``scripts/demo_part_c.py`` only. Workflow runs, the
  worker, the API and agents can never import it (import-linter contract, D-38 as revised).
* ``ollama``: the direct local Ollama adapter (loopback only, no LiteLLM, D-35), brought forward from
  Part F into Part D by owner decision D-44.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from devteam_core.contracts.llm import ModelRequest, Provider


@dataclass(frozen=True, slots=True)
class ProviderReply:
    text: str
    input_tokens: int
    output_tokens: int
    model_id: str


class ModelProvider(Protocol):
    name: Provider

    def generate(self, request: ModelRequest) -> ProviderReply:
        """Answer one request. Raise ``TransientError`` for timeouts/busy, ``FatalError`` otherwise."""
        ...


__all__ = ["ModelProvider", "ProviderReply"]
