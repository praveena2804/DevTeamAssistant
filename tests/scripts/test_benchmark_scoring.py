"""Benchmark scoring is deterministic, strict, and never executes model output."""

import json

import pytest

from tests.scripts.test_scripts import load_script

bench = load_script("benchmark_models")

GOOD_STRUCTURED = {
    "requirements": [{"id": "FR-001", "text": "Register members"}],
    "business_rules": [
        {"id": "BR-001", "text": "Max 3 books per member"},
        {"id": "BR-002", "text": "Loans due after 14 days"},
    ],
    "roles": ["Librarian", "Member"],
}


def test_valid_structured_answer_passes() -> None:
    assert bench.score_structured(json.dumps(GOOD_STRUCTURED)) == (True, "valid")


@pytest.mark.parametrize(
    "mutation",
    [
        {"requirements": [{"id": "REQ-1", "text": "Register members"}]},
        {"business_rules": [{"id": "BR-001", "text": "Only one rule"}]},
        {"roles": ["Member"]},
        {"requirements": []},
    ],
)
def test_invalid_structured_answers_fail(mutation: dict[str, object]) -> None:
    ok, _ = bench.score_structured(json.dumps({**GOOD_STRUCTURED, **mutation}))
    assert not ok


def test_non_json_fails() -> None:
    assert bench.score_structured("not json")[0] is False


GOOD_CODE = """```python
import re

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
```"""


def test_good_code_passes_static_checks() -> None:
    ok, detail = bench.score_code(GOOD_CODE)
    assert ok
    assert "not executed" in detail


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("```python\ndef slugify(text):\n    return (\n```", "syntax"),
        ("```python\ndef other(text):\n    return text\n```", "no slugify"),
        ("```python\ndef slugify(a, b):\n    return a\n```", "exactly one"),
        ("```python\nimport os\ndef slugify(t):\n    return t\n```", "forbidden import"),
        ("```python\nfrom subprocess import run\ndef slugify(t):\n    return t\n```", "forbidden import"),
        ("```python\ndef slugify(t):\n    eval(t)\n    return t\n```", "forbidden call"),
        ("```python\ndef slugify(t):\n    pass\n```", "never returns"),
    ],
)
def test_bad_code_fails_with_reason(code: str, reason: str) -> None:
    ok, detail = bench.score_code(code)
    assert not ok
    assert reason in detail


def test_dangerous_code_is_not_executed(tmp_path: object) -> None:
    """A payload that would create a marker file if executed must be rejected without running."""
    marker = "DEVTEAM_BENCH_MARKER_SHOULD_NOT_EXIST"
    payload = (
        f"```python\nimport pathlib\npathlib.Path('{marker}').write_text('x')\n"
        "def slugify(t):\n    return t\n```"
    )
    ok, _ = bench.score_code(payload)
    assert not ok
    from pathlib import Path  # noqa: PLC0415

    assert not Path(marker).exists()


def test_recommendation_requires_every_run_to_pass() -> None:
    summaries = [
        {
            "model": "a",
            "slot": "instruct",
            "runs": 3,
            "structured_pass": 3,
            "code_pass": 0,
            "median_tokens_per_s": 5,
        },
        {
            "model": "b",
            "slot": "instruct",
            "runs": 3,
            "structured_pass": 2,
            "code_pass": 3,
            "median_tokens_per_s": 50,
        },
        {
            "model": "c",
            "slot": "coder",
            "runs": 3,
            "structured_pass": 3,
            "code_pass": 2,
            "median_tokens_per_s": 9,
        },
    ]
    picks = bench.recommend(summaries)
    assert picks["instruct"].startswith("a ")
    assert "no candidate" in picks["coder"]
    assert "no candidate" in picks["fallback"]
