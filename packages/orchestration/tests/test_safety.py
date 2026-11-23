"""D.1 local-only guard for the LangGraph stack (D-30; Part D plan §8)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from devteam_orchestration.safety import (
    REMOTE_SETTINGS,
    TRACING_SWITCHES,
    CloudTracingConfiguredError,
    cloud_tracing_settings,
    ensure_local_only,
)

pytestmark = pytest.mark.security


def test_a_clean_environment_passes_and_switches_are_pinned_off() -> None:
    env: dict[str, str] = {"PATH": "x"}
    ensure_local_only(env)
    for name in TRACING_SWITCHES:
        assert env[name] == "false"
    assert cloud_tracing_settings(env).violations == ()


@pytest.mark.parametrize("name", TRACING_SWITCHES)
@pytest.mark.parametrize("value", ["true", "1", "TRUE", " yes "])
def test_any_tracing_switch_turned_on_is_refused(name: str, value: str) -> None:
    env = {name: value}
    with pytest.raises(CloudTracingConfiguredError) as caught:
        ensure_local_only(env)
    assert caught.value.variables == (name,)
    assert env[name] == value  # nothing is changed when refusing


@pytest.mark.parametrize("value", ["false", "0", "off", "no", ""])
def test_switches_explicitly_off_are_accepted(value: str) -> None:
    ensure_local_only({"LANGSMITH_TRACING": value})


@pytest.mark.parametrize("name", REMOTE_SETTINGS)
def test_remote_endpoint_or_key_settings_are_refused(name: str) -> None:
    with pytest.raises(CloudTracingConfiguredError):
        ensure_local_only({name: "https://example.invalid"})


def test_the_error_names_variables_but_never_echoes_values() -> None:
    secret = "lsv2_canary_value_123"
    with pytest.raises(CloudTracingConfiguredError) as caught:
        ensure_local_only({"LANGSMITH_API_KEY": secret, "LANGSMITH_TRACING": "true"})
    message = str(caught.value)
    assert "LANGSMITH_API_KEY" in message and "LANGSMITH_TRACING" in message
    assert secret not in message


def test_negative_control_langsmith_would_trace_without_the_guard() -> None:
    """With tracing switched on, LangSmith reports tracing enabled; after the guard it never is.

    Run in a subprocess so the library's process-wide caches cannot leak between tests.
    """
    code = (
        "import os, sys\n"
        "from langsmith import utils\n"
        "print(bool(utils.tracing_is_enabled()))\n"
        "from devteam_orchestration.safety import ensure_local_only, CloudTracingConfiguredError\n"
        "try:\n"
        "    ensure_local_only()\n"
        "    print('accepted')\n"
        "except CloudTracingConfiguredError:\n"
        "    print('refused')\n"
    )
    base = {k: v for k, v in os.environ.items() if not k.startswith(("LANGSMITH_", "LANGCHAIN_"))}
    on = subprocess.run(
        [sys.executable, "-c", code],
        env={**base, "LANGSMITH_TRACING": "true"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert on.stdout.split() == ["True", "refused"]
    off = subprocess.run([sys.executable, "-c", code], env=base, capture_output=True, text=True, check=True)
    assert off.stdout.split() == ["False", "accepted"]
