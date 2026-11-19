"""Write the platform API's OpenAPI document to apps/api/openapi.json (input for the web client generator).

Usage:  uv run python scripts/export_openapi.py
The committed file is also a contract snapshot: tests/contract/test_openapi_snapshot.py fails on drift.
"""

from __future__ import annotations

from pathlib import Path

from devteam_api.openapi_export import write_openapi
from devteam_core.config import load_settings

DESTINATION = Path(__file__).resolve().parents[1] / "apps" / "api" / "openapi.json"


def main() -> int:
    print(f"wrote {write_openapi(load_settings(profile='test'), DESTINATION)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
