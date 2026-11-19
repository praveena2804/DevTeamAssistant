"""HC-15 token handling and HC-18 token buckets."""

from pathlib import Path

import pytest

from devteam_core.logging import redact_value
from devteam_harness.iam import (
    LOCAL_HUMAN_ROLES,
    Principal,
    Role,
    load_or_create_token,
    session_value,
    token_matches,
)
from devteam_harness.ratelimit import RateLimiter

pytestmark = pytest.mark.security


def test_token_is_created_once_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "data" / "api_token"
    first = load_or_create_token(path)
    assert len(first) >= 40
    assert load_or_create_token(path) == first
    assert path.read_text(encoding="utf-8") == first


def test_tokens_differ_per_install(tmp_path: Path) -> None:
    assert load_or_create_token(tmp_path / "a") != load_or_create_token(tmp_path / "b")


def test_short_token_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "api_token"
    path.write_text("short", encoding="utf-8")
    with pytest.raises(ValueError, match="too short"):
        load_or_create_token(path)


def test_token_and_session_value_are_redacted_from_logs(tmp_path: Path) -> None:
    token = load_or_create_token(tmp_path / "api_token")
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
