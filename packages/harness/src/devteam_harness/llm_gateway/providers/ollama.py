"""Direct Ollama provider (HC-02; D-35 no LiteLLM; brought forward into Part D by owner decision D-44).

Rules:

* **Loopback only.** The base URL must name a loopback host (checked again here, not only at config
  load), credentials in the URL are refused, and the HTTP client ignores proxy environment variables
  (``trust_env=False``) so a ``HTTP(S)_PROXY`` setting can never route a prompt off this machine.
* **Few endpoints.** ``POST /api/chat`` (generation), ``POST /api/embed`` (embeddings, Part F),
  ``GET /api/tags`` (inventory with digests), ``GET /api/version``, ``GET /api/ps`` and
  ``POST /api/generate`` with ``keep_alive: 0`` (unload only). Nothing is pulled or deleted; a missing
  model is reported, never downloaded.
* **The router chooses the model** (Part F): ``generate``/``embed`` receive the model tag and per-model
  timeouts from the gateway.
* **Structured answers.** When the operation names an answer contract, its JSON Schema (with ``$ref``
  inlined) is sent as Ollama's ``format``, so the model is constrained to that shape. The gateway still
  validates the answer against the contract.
* **Honest failures.** Ollama unreachable or a model missing → ``ResourceUnavailableError`` (the run
  waits; nothing is invented). Out of memory or a model that fails to load → ``ResourceUnavailableError``
  ``model.oom`` / ``model.load_failed`` (the gateway's recovery ladder, Part F). Timeout →
  ``TransientError`` ``model.timeout``; other 5xx → ``TransientError`` (bounded retries, F-10b). Any
  other error → ``FatalError``. Messages carry no prompt or answer text.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from devteam_core.config.settings import is_loopback_host
from devteam_core.contracts.llm import ModelRequest, Provider
from devteam_core.contracts.statuses import ModelSlot
from devteam_harness.errors import FatalError, ResourceUnavailableError, TransientError
from devteam_harness.llm_gateway.providers import CallTimeouts, EmbedReply, ProviderReply

SchemaLookup = Callable[[str], Mapping[str, Any] | None]
_SERVER_ERROR = 500
_NOT_FOUND = 404
_OOM_MARKERS = ("memory", "out of memory", "oom")  # e.g. "model requires more system memory"
_LOAD_MARKERS = ("failed to load", "error loading model", "llama runner process has terminated")


def check_local_url(base_url: str) -> str:
    """Return the normalised URL, or raise ``FatalError`` unless it is plain http(s) on loopback."""
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FatalError("ollama.url", "the Ollama URL must be an http(s) URL")
    if parts.username or parts.password:
        raise FatalError("ollama.url", "the Ollama URL must not contain credentials")
    if not is_loopback_host(parts.hostname):
        raise FatalError("ollama.not_local", "Ollama must run on this machine (loopback address only)")
    return base_url.rstrip("/")


def inline_refs(schema: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of a Pydantic JSON Schema with every local ``$ref`` replaced by its definition."""
    root = copy.deepcopy(dict(schema))
    defs = root.pop("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 20:
            raise FatalError("ollama.schema", "the answer schema nests too deeply")
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs.get(ref.removeprefix("#/$defs/"))
                if target is None:
                    raise FatalError("ollama.schema", "the answer schema has an unresolved reference")
                merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged, depth + 1)
            return {k: resolve(v, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v, depth + 1) for v in node]
        return node

    resolved = resolve(root)
    assert isinstance(resolved, dict)  # noqa: S101 - root is a dict
    return resolved


def build_chat_body(
    request: ModelRequest, model: str, *, keep_alive: str, schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The ``/api/chat`` request body (pure; tested without a network)."""
    options: dict[str, Any] = {
        "temperature": request.params.temperature,
        "num_ctx": request.params.context_window,
        "num_predict": request.params.max_output_tokens,
    }
    if request.params.seed is not None:
        options["seed"] = request.params.seed
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": m.role.value, "content": m.content} for m in request.messages],
        "stream": False,
        "keep_alive": keep_alive,
        "options": options,
    }
    if schema is not None:
        body["format"] = inline_refs(schema)
    return body


def parse_chat_reply(data: Mapping[str, Any], model: str) -> ProviderReply:
    """Turn an ``/api/chat`` answer into a ``ProviderReply`` (pure; tested without a network)."""
    message = data.get("message")
    text = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(text, str):
        raise FatalError("ollama.bad_reply", "Ollama returned no message content")
    if data.get("done") is False:
        raise TransientError("ollama.incomplete", "Ollama returned an incomplete answer")
    prompt_tokens = data.get("prompt_eval_count")
    output_tokens = data.get("eval_count")
    reported = data.get("model")
    return ProviderReply(
        text=text,
        input_tokens=prompt_tokens if isinstance(prompt_tokens, int) and prompt_tokens >= 0 else 0,
        output_tokens=output_tokens if isinstance(output_tokens, int) and output_tokens >= 0 else 0,
        model_id=reported if isinstance(reported, str) and reported else model,
    )


class OllamaProvider:
    name = Provider.OLLAMA

    def __init__(
        self,
        base_url: str,
        models: Mapping[ModelSlot, str],
        *,
        timeout_s: float,
        keep_alive: str = "1m",
        schemas: SchemaLookup = lambda _name: None,
    ) -> None:
        self._base = check_local_url(base_url)
        if ModelSlot.INSTRUCT not in models:
            raise FatalError("ollama.models", "an instruct model must be configured")
        self._models = dict(models)
        self._timeout = timeout_s
        self._keep_alive = keep_alive
        self._schemas = schemas
        self._client = self._new_client()

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base,
            timeout=httpx.Timeout(self._timeout, connect=5.0),
            trust_env=False,  # never use proxy settings from the environment
            follow_redirects=False,  # a redirect could point off this machine
        )

    def cancel_inflight(self) -> None:
        """Abort calls still in flight (after a gateway timeout): closing the connection pool makes the
        waiting request fail at once, so the model lock is released and the next rung never waits behind
        a hung call. A fresh client serves later calls."""
        old, self._client = self._client, self._new_client()
        old.close()

    def model_for(self, slot: ModelSlot) -> str:
        return self._models.get(slot) or self._models[ModelSlot.INSTRUCT]

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------------------------ health

    def installed_models(self) -> frozenset[str]:
        """Model tags installed in the local Ollama. Raises ``ResourceUnavailableError`` if unreachable."""
        data = self._get("/api/tags")
        models = data.get("models")
        if not isinstance(models, list):
            raise FatalError("ollama.bad_reply", "Ollama returned no model list")
        names = {m.get("name") for m in models if isinstance(m, Mapping)}
        return frozenset(n.lower() for n in names if isinstance(n, str))

    def missing_models(self, wanted_models: Iterable[str] | None = None) -> tuple[str, ...]:
        installed = self.installed_models()
        wanted = sorted(set(wanted_models if wanted_models is not None else self._models.values()))
        return tuple(
            m for m in wanted if m.lower() not in installed and f"{m.lower()}:latest" not in installed
        )

    def version(self) -> str:
        value = self._get("/api/version").get("version")
        return value if isinstance(value, str) else "unknown"

    # ------------------------------------------------------------- residency (Part E Resource Guard)

    def loaded_models(self) -> tuple[str, ...]:
        """Models currently in memory (``/api/ps``). Ollama not running means nothing is loaded."""
        try:
            data = self._get("/api/ps")
        except ResourceUnavailableError:
            return ()
        models = data.get("models")
        if not isinstance(models, list):
            return ()
        return tuple(str(m.get("name")) for m in models if isinstance(m, Mapping) and m.get("name"))

    def unload(self, model: str) -> None:
        """Ask Ollama to release a model now (``keep_alive: 0``). Never downloads or starts anything."""
        if not isinstance(model, str) or not model:
            return
        try:
            self._client.post("/api/generate", json={"model": model, "keep_alive": 0}, timeout=30.0)
        except httpx.HTTPError:
            return  # not running: nothing to unload

    def _get(self, path: str) -> Mapping[str, Any]:
        try:
            response = self._client.get(path, timeout=10.0)
        except httpx.HTTPError:
            raise ResourceUnavailableError(
                "ollama.unreachable", "the local Ollama service is not reachable"
            ) from None
        if response.status_code != 200:
            raise FatalError("ollama.http", f"Ollama answered HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, Mapping):
            raise FatalError("ollama.bad_reply", "Ollama returned an unexpected answer")
        return data

    # -------------------------------------------------------------------------------- generation

    def generate(
        self, request: ModelRequest, *, model: str | None = None, timeouts: CallTimeouts | None = None
    ) -> ProviderReply:
        model = model or self.model_for(request.slot)
        schema = self._schemas(request.output_schema) if request.output_schema else None
        if request.output_schema and schema is None:
            raise FatalError("ollama.schema", "the operation's answer contract is not registered")
        body = build_chat_body(request, model, keep_alive=self._keep_alive, schema=schema)
        try:
            response = self._client.post("/api/chat", json=body, timeout=self._timeout_for(timeouts))
        except httpx.TimeoutException:
            raise TransientError("model.timeout", "the local model did not answer in time") from None
        except httpx.ConnectError:
            raise ResourceUnavailableError(
                "ollama.unreachable", "the local Ollama service is not reachable"
            ) from None
        except httpx.HTTPError as exc:
            raise TransientError("ollama.transport", f"connection problem ({type(exc).__name__})") from None
        data = _checked_json(response, model)
        return parse_chat_reply(data, model)

    # -------------------------------------------------------------------------------- embeddings

    def embed(
        self, model: str, texts: tuple[str, ...], *, timeouts: CallTimeouts | None = None
    ) -> EmbedReply:
        """Embed ``texts`` with ``model`` (``/api/embed``). Raises like :meth:`generate`."""
        body = {"model": model, "input": list(texts), "keep_alive": self._keep_alive, "truncate": True}
        try:
            response = self._client.post("/api/embed", json=body, timeout=self._timeout_for(timeouts))
        except httpx.TimeoutException:
            raise TransientError(
                "model.timeout", "the local embedding model did not answer in time"
            ) from None
        except httpx.ConnectError:
            raise ResourceUnavailableError(
                "ollama.unreachable", "the local Ollama service is not reachable"
            ) from None
        except httpx.HTTPError as exc:
            raise TransientError("ollama.transport", f"connection problem ({type(exc).__name__})") from None
        data = _checked_json(response, model)
        return parse_embed_reply(data, model, expected=len(texts))

    # -------------------------------------------------------------------------------- inventory

    def list_models(self) -> dict[str, str]:
        """Installed model tag → digest (sha256 hex). Raises ``ResourceUnavailableError`` if unreachable."""
        data = self._get("/api/tags")
        models = data.get("models")
        if not isinstance(models, list):
            raise FatalError("ollama.bad_reply", "Ollama returned no model list")
        out: dict[str, str] = {}
        for item in models:
            if not isinstance(item, Mapping):
                continue
            name, digest = item.get("name"), item.get("digest")
            if isinstance(name, str) and name:
                out[name.lower()] = digest.lower() if isinstance(digest, str) else ""
        return out

    def _timeout_for(self, timeouts: CallTimeouts | None) -> httpx.Timeout:
        if timeouts is None:
            return httpx.Timeout(self._timeout, connect=5.0)
        return httpx.Timeout(min(timeouts.read_s, self._timeout), connect=timeouts.connect_s)


def _checked_json(response: httpx.Response, model: str) -> Mapping[str, Any]:
    """Map an Ollama HTTP answer to data or to a classified error (no prompt or answer text in errors)."""
    if response.status_code == _NOT_FOUND:
        raise ResourceUnavailableError("ollama.model_missing", f"model '{model}' is not installed locally")
    if response.status_code >= _SERVER_ERROR:
        detail = _error_text(response)
        if any(marker in detail for marker in _OOM_MARKERS):
            raise ResourceUnavailableError("model.oom", f"model '{model}' does not fit in free memory")
        if any(marker in detail for marker in _LOAD_MARKERS):
            raise ResourceUnavailableError("model.load_failed", f"model '{model}' could not be loaded")
        raise TransientError("ollama.server", f"Ollama answered HTTP {response.status_code}")
    if response.status_code != 200:
        raise FatalError("ollama.http", f"Ollama answered HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise FatalError("ollama.bad_reply", "Ollama returned something that is not JSON") from None
    if not isinstance(data, Mapping):
        raise FatalError("ollama.bad_reply", "Ollama returned an unexpected answer")
    return data


def _error_text(response: httpx.Response) -> str:
    """Ollama's own error string, lower-cased, for classification only (never logged or returned)."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:500].lower()
    value = data.get("error") if isinstance(data, Mapping) else None
    return value.lower()[:500] if isinstance(value, str) else ""


def parse_embed_reply(data: Mapping[str, Any], model: str, *, expected: int) -> EmbedReply:
    """Turn an ``/api/embed`` answer into an ``EmbedReply`` (pure; tested without a network)."""
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != expected:
        raise FatalError("ollama.bad_reply", "Ollama returned the wrong number of embeddings")
    out: list[tuple[float, ...]] = []
    for vector in vectors:
        if not isinstance(vector, list) or not vector or not all(isinstance(x, (int, float)) for x in vector):
            raise FatalError("ollama.bad_reply", "Ollama returned a malformed embedding")
        out.append(tuple(float(x) for x in vector))
    if len({len(v) for v in out}) != 1:
        raise FatalError("ollama.bad_reply", "Ollama returned embeddings of different sizes")
    tokens = data.get("prompt_eval_count")
    reported = data.get("model")
    return EmbedReply(
        vectors=tuple(out),
        input_tokens=tokens if isinstance(tokens, int) and tokens >= 0 else 0,
        model_id=reported if isinstance(reported, str) and reported else model,
    )


__all__ = [
    "OllamaProvider",
    "build_chat_body",
    "check_local_url",
    "inline_refs",
    "parse_chat_reply",
    "parse_embed_reply",
]
