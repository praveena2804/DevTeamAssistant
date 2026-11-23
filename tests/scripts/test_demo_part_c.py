"""The Part C demonstration script runs end to end in a temporary folder (plan §8)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_demo_shows_allowed_refused_stopped_and_tamper_detection() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "demo_part_c.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout
    assert "-> ALLOWED  (completed)" in lines
    assert "-> REFUSED, LOGGED  (deny.path_invalid)" in lines
    assert "-> REFUSED, LOGGED  (deny.tool_not_declared)" in lines
    assert "-> STOPPED  (after 3 attempts: failed)" in lines
    assert "-> INTACT" in lines
    assert "-> BROKEN  (at entry" in lines
    assert "Traceback" not in result.stderr
