"""Model providers behind the LLM Gateway (HC-02). The only package allowed to import HTTP clients.

Part C ships the ``fake`` provider only (scripted answers for tests). The direct Ollama adapter (loopback
only, no LiteLLM, D-35) comes in Part F.
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
