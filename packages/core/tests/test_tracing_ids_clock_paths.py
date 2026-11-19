import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devteam_core import tracing
from devteam_core.clock import FixedClock, SystemClock
from devteam_core.errors import ConfigError
from devteam_core.ids import id_timestamp, is_valid_id, new_id, new_ulid
from devteam_core.paths import find_repo_root, resolve_under

# ------------------------------------------------------------------ tracing


def test_span_nesting_and_restoration() -> None:
    assert tracing.current() is None
    with tracing.span() as root:
        assert root.parent_span_id is None
        with tracing.span() as child:
            assert child.trace_id == root.trace_id
            assert child.parent_span_id == root.span_id
            assert child.span_id != root.span_id
        assert tracing.current() == root
    assert tracing.current() is None


def test_traceparent_round_trip() -> None:
    ctx = tracing.new_root()
    parsed = tracing.parse_traceparent(tracing.format_traceparent(ctx))
    assert parsed is not None
    assert (parsed.trace_id, parsed.span_id, parsed.sampled) == (ctx.trace_id, ctx.span_id, True)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "garbage",
        "00-" + "0" * 32 + "-" + "1" * 16 + "-01",  # all-zero trace id
        "00-" + "1" * 32 + "-" + "0" * 16 + "-01",  # all-zero span id
        "ff-" + "1" * 32 + "-" + "1" * 16 + "-01",  # forbidden version
        "00-" + "1" * 31 + "-" + "1" * 16 + "-01",  # wrong length
    ],
)
def test_invalid_traceparent_is_ignored(header: str | None) -> None:
    assert tracing.parse_traceparent(header) is None


@given(st.text(max_size=80))
def test_parse_traceparent_never_raises(header: str) -> None:
    tracing.parse_traceparent(header)


def test_context_is_isolated_between_asyncio_tasks() -> None:
    async def worker() -> str:
        with tracing.span() as ctx:
            await asyncio.sleep(0)
            current = tracing.current()
            assert current is not None
            assert current.trace_id == ctx.trace_id
            return ctx.trace_id

    async def main() -> tuple[str, str]:
        return await asyncio.gather(worker(), worker())

    first, second = asyncio.run(main())
    assert first != second


# ------------------------------------------------------------------ ids


def test_new_id_format_and_validation() -> None:
    value = new_id("proj")
    assert value.startswith("proj_")
    assert len(value) == len("proj_") + 26
    assert is_valid_id(value)
    assert is_valid_id(value, prefix="proj")
    assert not is_valid_id(value, prefix="run")


@pytest.mark.parametrize("prefix", ["", "P", "1abc", "a", "has-dash", "toolongprefix1"])
def test_invalid_prefixes_are_rejected(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        new_id(prefix)


@pytest.mark.parametrize(
    "value", ["", "proj", "proj_", "proj_" + "U" * 26, "PROJ_" + "0" * 26, "proj-" + "0" * 26]
)
def test_invalid_ids(value: str) -> None:
    assert not is_valid_id(value)


def test_ids_are_time_ordered_and_timestamp_round_trips() -> None:
    start = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
    clock = FixedClock(start)
    earlier = new_id("run", clock)
    clock.set(start + timedelta(milliseconds=5))
    later = new_id("run", clock)
    assert earlier < later
    assert id_timestamp(earlier) == start
    with pytest.raises(ValueError, match="not a valid identifier"):
        id_timestamp("nope")


def test_ulids_are_unique() -> None:
    assert len({new_ulid() for _ in range(2000)}) == 2000


# ------------------------------------------------------------------ clock


def test_clocks() -> None:
    assert SystemClock().now().tzinfo is not None
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 1, 1))
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2026, 1, 2))


# ------------------------------------------------------------------ paths


def test_find_repo_root_from_this_file() -> None:
    root = find_repo_root(Path(__file__))
    assert (root / "config" / "defaults.yaml").is_file()


def test_find_repo_root_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTEAM_ROOT", str(tmp_path))
    with pytest.raises(ConfigError, match="does not point"):
        find_repo_root()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "defaults.yaml").write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert find_repo_root() == tmp_path.resolve()


def test_find_repo_root_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVTEAM_ROOT", raising=False)
    with pytest.raises(ConfigError, match="could not locate"):
        find_repo_root(tmp_path)


def test_resolve_under(tmp_path: Path) -> None:
    assert resolve_under(tmp_path, Path("a")) == (tmp_path / "a").resolve()
    assert resolve_under(tmp_path, tmp_path / "b") == tmp_path / "b"
