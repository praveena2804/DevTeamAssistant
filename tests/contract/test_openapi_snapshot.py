"""The committed OpenAPI document (input of the web client generator) must match the live API."""

import json
from pathlib import Path

import pytest

from devteam_api.openapi_export import openapi_document
from devteam_core.config import Settings

pytestmark = pytest.mark.contract

SNAPSHOT = Path(__file__).resolve().parents[2] / "apps" / "api" / "openapi.json"


def test_openapi_snapshot_matches_live_api(isolated_settings: Settings) -> None:
    assert SNAPSHOT.is_file(), "run: uv run python scripts/export_openapi.py"
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    live = json.loads(json.dumps(openapi_document(isolated_settings), sort_keys=True))
    assert committed == live, "OpenAPI drift: run scripts/export_openapi.py and `npm run gen:api` in apps/web"
