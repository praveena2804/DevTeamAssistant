"""HC-15 token handling and HC-18 token buckets."""

from pathlib import Path

import pytest

from devteam_core.logging import redact_value
from devteam_harness.iam import (
    API_TOKEN_REF,
    LOCAL_HUMAN_ROLES,
    Principal,
    Role,
    load_api_token,
    session_value,
    token_matches,
)
from devteam_harness.ratelimit import RateLimiter
from devteam_harness.secrets import MemoryBackend, SecretsManager, SecretsUnavailableError

pytestmark = pytest.mark.security


def _store() -> SecretsManager:
    return SecretsManager(MemoryBackend())


def test_token_is_created_once_in_the_credential_store_and_reused(tmp_path: Path) -> None:
    store = _store()
    first = load_api_token(store, tmp_path / "data" / "api_token")
    assert len(first) >= 40
    assert load_api_token(store, tmp_path / "data" / "api_token") == first
    assert store.require(API_TOKEN_REF).reveal() == first
    assert not (tmp_path / "data" / "api_token").exists()  # D-18: never written to a file


def test_tokens_differ_per_install(tmp_path: Path) -> None:
    assert load_api_token(_store(), tmp_path / "a") != load_api_token(_store(), tmp_path / "b")


def test_short_stored_token_is_refused(tmp_path: Path) -> None:
    store = SecretsManager(MemoryBackend({"platform/api_token": "short-token"}))
    with pytest.raises(SecretsUnavailableError, match="too short"):
        load_api_token(store, tmp_path / "api_token")


def test_token_and_session_value_are_redacted_from_logs(tmp_path: Path) -> None:
    token = load_api_token(_store(), tmp_path / "api_token")
    cookie = session_value(token)
    assert cookie != token and token not in cookie
    text = str(redact_value(f"value {token} cookie {cookie}"))  # the same path every log line takes
    assert token not in text and cookie not in text


def test_token_comparison() -> None:
    assert token_matches("abc", "abc")
    assert not token_matches("abd", "abc")
    assert not token_matches(None, "abc")
    assert not token_matches("", "abc")


def test_local_human_holds_every_spec_role() -> None:
    principal = Principal(id="prn_x", kind="human", display_name="Owner", roles=LOCAL_HUMAN_ROLES)
    assert all(principal.has_role(role) for role in Role)
    assert not Principal(id="p", kind="human", display_name="x", roles=frozenset()).has_role(Role.REVIEWER)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_bucket_allows_capacity_then_refuses_then_refills() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, period_s=60, clock=clock)
    assert [limiter.try_acquire("k")[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.try_acquire("k")
    assert not allowed and 19 < retry_after <= 20
    assert limiter.try_acquire("other")[0]  # keys are independent
    clock.now = 20.0
    assert limiter.try_acquire("k")[0]
    assert not limiter.try_acquire("k")[0]


def test_bucket_rejects_bad_configuration() -> None:
    with pytest.raises(ValueError, match="capacity"):
        RateLimiter(capacity=0)
