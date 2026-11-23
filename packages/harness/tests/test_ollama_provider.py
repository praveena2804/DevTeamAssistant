"""Direct Ollama provider (D-44): local-only, honest failures, schema-constrained answers.

No mock model server is used (D-38 as revised). Request building and reply parsing are pure functions
tested on literal data; network behaviour is tested against a real closed loopback port; real answers
come from the local Ollama in the ``llm``-marked tests.
"""

from __future__ import annotations

import socket

import pytest

from devteam_core.contracts import CONTRACT_MODELS
from devteam_core.contracts.llm import ChatMessage, ChatRole, ModelParams, ModelRequest, Provider, Trust
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.ids import new_id
from devteam_harness.errors import FatalError, ResourceUnavailableError, TransientError
from devteam_harness.llm_gateway.providers.ollama import (
    OllamaProvider,
    build_chat_body,
    check_local_url,
    inline_refs,
    parse_chat_reply,
)

MODELS = {ModelSlot.INSTRUCT: "qwen2.5:3b", ModelSlot.CODER: "qwen2.5-coder:3b"}


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(schema: str | None = "SummaryDraft", slot: ModelSlot = ModelSlot.INSTRUCT) -> ModelRequest:
    return ModelRequest(
        request_id=new_id("mrq"),
        role=AgentRole.HEAD,
        operation_id="head.plan",
        slot=slot,
        messages=(
            ChatMessage(role=ChatRole.SYSTEM, trust=Trust.TRUSTED, content="Summarise. Answer as JSON."),
            ChatMessage(role=ChatRole.USER, trust=Trust.UNTRUSTED, label="plan", content="Five build tasks."),
        ),
        output_schema=schema,
        params=ModelParams(max_output_tokens=200, context_window=2048, seed=7),
        prompt_id="head.system",
        prompt_version=1,
        prompt_hash="0" * 64,
    )


def _schemas(name: str) -> dict[str, object] | None:
    model = CONTRACT_MODELS.get(name)
    return model.model_json_schema() if model else None


# ------------------------------------------------------------------------------------- local only


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:11434",
        "http://10.0.0.5:11434",
        "http://192.168.1.10:11434",
        "https://api.openai.com",
        "http://user:pass@127.0.0.1:11434",
        "ftp://127.0.0.1:11434",
        "127.0.0.1:11434",
    ],
)
def test_only_plain_loopback_urls_are_accepted(url: str) -> None:
    with pytest.raises(FatalError):
        check_local_url(url)
    with pytest.raises(FatalError):
        OllamaProvider(url, MODELS, timeout_s=10)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://localhost:11434/", "http://[::1]:11434"])
def test_loopback_urls_are_accepted(url: str) -> None:
    assert check_local_url(url) == url.rstrip("/")


def test_proxy_environment_is_ignored_and_redirects_are_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.com:8080")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example.com:8080")
    provider = OllamaProvider("http://127.0.0.1:11434", MODELS, timeout_s=10)
    try:
        client = provider._client
        assert client.trust_env is False
        assert client.follow_redirects is False
        assert not client._mounts
    finally:
        provider.close()


def test_an_instruct_model_is_required() -> None:
    with pytest.raises(FatalError, match="instruct"):
        OllamaProvider("http://127.0.0.1:11434", {ModelSlot.CODER: "x"}, timeout_s=10)


# ------------------------------------------------------------------------------- request / reply


def test_the_chat_body_carries_limits_and_the_inlined_answer_schema() -> None:
    request = _request()
    body = build_chat_body(request, "qwen2.5:3b", keep_alive="1m", schema=_schemas("SummaryDraft"))
    assert body["model"] == "qwen2.5:3b"
    assert body["stream"] is False
    assert body["keep_alive"] == "1m"
    assert body["options"] == {"temperature": 0.0, "num_ctx": 2048, "num_predict": 200, "seed": 7}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["format"]["properties"]["summary"]["type"] == "string"
    assert "$defs" not in str(body["format"])


def test_inline_refs_resolves_nested_definitions() -> None:
    schema = inline_refs(CONTRACT_MODELS["AnalysisDraft"].model_json_schema())
    text = str(schema)
    assert "$ref" not in text and "$defs" not in text
    item = schema["properties"]["checklist"]["items"]
    assert "goal" in item["properties"]["category"]["enum"]


def test_no_schema_means_free_text() -> None:
    assert "format" not in build_chat_body(_request(None), "m", keep_alive="1m", schema=None)


def test_reply_parsing() -> None:
    reply = parse_chat_reply(
        {
            "model": "qwen2.5:3b",
            "message": {"content": '{"summary": "x"}'},
            "done": True,
            "prompt_eval_count": 40,
            "eval_count": 9,
        },
        "fallback",
    )
    assert (reply.text, reply.model_id, reply.input_tokens, reply.output_tokens) == (
        '{"summary": "x"}',
        "qwen2.5:3b",
        40,
        9,
    )
    assert parse_chat_reply({"message": {"content": "a"}}, "m").model_id == "m"
    with pytest.raises(FatalError):
        parse_chat_reply({"message": {}}, "m")
    with pytest.raises(TransientError):
        parse_chat_reply({"message": {"content": "partial"}, "done": False}, "m")


def test_slots_fall_back_to_the_instruct_model() -> None:
    provider = OllamaProvider("http://127.0.0.1:11434", MODELS, timeout_s=10)
    try:
        assert provider.name is Provider.OLLAMA
        assert provider.model_for(ModelSlot.CODER) == "qwen2.5-coder:3b"
        assert provider.model_for(ModelSlot.REVIEW) == "qwen2.5:3b"
    finally:
        provider.close()


# ------------------------------------------------------------------------- honest network failures


def test_an_unreachable_ollama_is_a_resource_problem_not_an_answer() -> None:
    provider = OllamaProvider(f"http://127.0.0.1:{_closed_port()}", MODELS, timeout_s=5, schemas=_schemas)
    try:
        with pytest.raises(ResourceUnavailableError) as caught:
            provider.generate(_request())
        assert caught.value.code == "ollama.unreachable"
        with pytest.raises(ResourceUnavailableError):
            provider.installed_models()
    finally:
        provider.close()


def test_an_unregistered_answer_contract_is_refused_before_any_call() -> None:
    provider = OllamaProvider(
        f"http://127.0.0.1:{_closed_port()}", MODELS, timeout_s=5, schemas=lambda _n: None
    )
    try:
        with pytest.raises(FatalError, match="not registered"):
            provider.generate(_request("SummaryDraft"))
    finally:
        provider.close()


# ----------------------------------------------------------------------------- real local model


@pytest.mark.llm
def test_a_real_local_answer_matches_its_contract() -> None:
    provider = OllamaProvider("http://127.0.0.1:11434", MODELS, timeout_s=180, schemas=_schemas)
    try:
        reply = provider.generate(_request())
        parsed = CONTRACT_MODELS["SummaryDraft"].model_validate_json(reply.text)
        assert parsed.summary  # type: ignore[attr-defined]
        assert reply.model_id.startswith("qwen2.5")
        assert reply.input_tokens > 0 and reply.output_tokens > 0
    finally:
        provider.close()


@pytest.mark.llm
def test_a_missing_model_is_reported_never_pulled() -> None:
    provider = OllamaProvider(
        "http://127.0.0.1:11434",
        {ModelSlot.INSTRUCT: "no-such-model-devteam:1b"},
        timeout_s=60,
        schemas=_schemas,
    )
    try:
        assert provider.missing_models() == ("no-such-model-devteam:1b",)
        with pytest.raises(ResourceUnavailableError) as caught:
            provider.generate(_request())
        assert caught.value.code == "ollama.model_missing"
        assert "no-such-model-devteam:1b" not in provider.installed_models()  # nothing was downloaded
    finally:
        provider.close()
