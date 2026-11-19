import io
import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from devteam_core import tracing
from devteam_core.config import Settings, load_settings
from devteam_core.logging import (
    CORRELATION_FIELDS,
    REDACTED,
    bound_context,
    clear_context,
    configure_logging,
    get_logger,
    is_sensitive_key,
    redact_text,
    redact_value,
    secret_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CANARY = "CANARY-7f3a9c1e-do-not-log"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    return load_settings(root=tmp_path, profile="test", environ={})


@pytest.fixture
def stream(settings: Settings) -> Iterator[io.StringIO]:
    buffer = io.StringIO()
    configure_logging(settings, stream=buffer)
    clear_context()
    yield buffer
    clear_context()
    secret_registry.clear()


def lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def test_every_line_is_json_with_correlation_fields(stream: io.StringIO) -> None:
    get_logger("t").info("hello", answer=42)
    (record,) = lines(stream)
    assert record["event"] == "hello"
    assert record["level"] == "info"
    assert record["answer"] == 42
    assert "ts" in record
    for field in (*CORRELATION_FIELDS, "trace_id", "span_id"):
        assert field in record


def test_bound_ids_and_trace_context_appear_in_lines(stream: io.StringIO) -> None:
    with tracing.span() as ctx, bound_context(project_id="proj_1", run_id="run_1", task_id="task_1"):
        get_logger("t").info("inside")
    get_logger("t").info("outside")
    inside, outside = lines(stream)
    assert inside["project_id"] == "proj_1"
    assert inside["run_id"] == "run_1"
    assert inside["task_id"] == "task_1"
    assert inside["trace_id"] == ctx.trace_id
    assert inside["span_id"] == ctx.span_id
    assert outside["project_id"] is None
    assert outside["trace_id"] is None


def test_stdlib_loggers_share_the_pipeline(stream: io.StringIO) -> None:
    secret_registry.register(CANARY)
    logging.getLogger("third.party").warning("connecting with %s", CANARY)
    (record,) = lines(stream)
    assert record["logger"] == "third.party"
    assert CANARY not in stream.getvalue()


def test_canary_secret_never_reaches_output(stream: io.StringIO) -> None:
    """Canary test (AC-C-05 foundation): a registered secret is removed from every part of a log line."""
    secret_registry.register(CANARY)
    log = get_logger("t")
    log.info(f"message with {CANARY}", nested={"deep": [f"x{CANARY}y"]}, plain=CANARY)
    with bound_context(principal=CANARY):
        log.info("bound")
    try:
        raise RuntimeError(f"boom {CANARY}")
    except RuntimeError:
        log.exception("failed")
    output = stream.getvalue()
    assert CANARY not in output
    assert output.count(REDACTED) >= 5


def test_negative_control_canary_leaks_without_redaction(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the canary test above can fail: with the redaction step removed, the canary is emitted."""
    import devteam_core.logging.setup as setup_module  # noqa: PLC0415

    monkeypatch.setattr(setup_module, "redact_event", lambda _l, _m, event_dict: event_dict)
    buffer = io.StringIO()
    configure_logging(settings, stream=buffer)
    secret_registry.register(CANARY)
    try:
        get_logger("t").info("leak", plain=CANARY)
        assert CANARY in buffer.getvalue()
    finally:
        secret_registry.clear()
        monkeypatch.undo()
        configure_logging(settings, stream=io.StringIO())


def test_sensitive_keys_are_redacted_wholesale(stream: io.StringIO) -> None:
    get_logger("t").info(
        "login",
        password="hunter2-xyz",
        api_key="k-123456",
        headers={"Authorization": "whatever", "Accept": "json"},
        input_tokens=120,
    )
    (record,) = lines(stream)
    assert record["password"] == REDACTED
    assert record["api_key"] == REDACTED
    assert record["headers"] == {"Authorization": REDACTED, "Accept": "json"}
    assert record["input_tokens"] == 120


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("token ghp_" + "a" * 36, "ghp_" + "a" * 36),
        ("pat github_pat_" + "B" * 40, "github_pat_" + "B" * 40),
        ("Authorization: Bearer abc.def.ghi-123456", "abc.def.ghi-123456"),
        ("postgresql://admin:S3cr3t!@db:5432/app", "S3cr3t!"),
        ("password=hunter2xyz&user=a", "hunter2xyz"),
        # Synthetic, non-functional values used only to prove redaction works.
        ("api_key: 'abcd1234efgh'", "abcd1234efgh"),  # gitleaks:allow
        ("AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),  # gitleaks:allow
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow"),
        ("jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2Q", "SflKxwRJSMeKKF2Q"),
    ],
)
def test_token_patterns_are_redacted(text: str, leaked: str) -> None:
    out = redact_text(text)
    assert leaked not in out
    assert REDACTED in out


def test_redaction_keeps_ordinary_text() -> None:
    assert redact_text("tokens/s = 12.5 for qwen2.5:3b") == "tokens/s = 12.5 for qwen2.5:3b"
    assert redact_value({"eval_count": 10, "event": "x"}) == {"eval_count": 10, "event": "x"}


def test_registry_rejects_short_values_and_supports_unregister() -> None:
    with pytest.raises(ValueError, match="at least"):
        secret_registry.register("abc")
    secret_registry.register("longer-secret")
    assert redact_text("x longer-secret y", secret_registry.values()) == f"x {REDACTED} y"
    secret_registry.unregister("longer-secret")
    assert "longer-secret" in redact_text("longer-secret", secret_registry.values())


def test_is_sensitive_key() -> None:
    for key in ("password", "DB_PASSWORD", "x-api-key", "client_secret", "Set-Cookie", "DATABASE_URL"):
        assert is_sensitive_key(key)
    for key in ("event", "trace_id", "token_count", "prompt_tokens", "eval_count", "principal"):
        assert not is_sensitive_key(key)


def test_unknown_objects_are_stringified_and_redacted() -> None:
    class Leaky:
        def __str__(self) -> str:
            return "password=topsecret1"

    assert "topsecret1" not in redact_value(Leaky())


def test_file_logging_writes_json(tmp_path: Path) -> None:
    shutil.copytree(REPO_ROOT / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    settings = load_settings(root=tmp_path, profile="test", environ={"DEVTEAM_LOGGING__FILE_ENABLED": "true"})
    log_file = configure_logging(settings, stream=io.StringIO())
    assert log_file is not None
    get_logger("t").info("to_file")
    for handler in logging.getLogger().handlers:
        handler.flush()
    record = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "to_file"
    # reconfigure without file logging so the handle is released (Windows file locking)
    configure_logging(load_settings(root=tmp_path, profile="test", environ={}), stream=io.StringIO())
