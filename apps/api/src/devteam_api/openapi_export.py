"""Export the API's OpenAPI document (consumed by the web client generator)."""

import json
from pathlib import Path
from typing import Any

from devteam_api.main import create_app
from devteam_core.config import Settings


def openapi_document(settings: Settings) -> dict[str, Any]:
    return create_app(settings, configure_logs=False).openapi()


def write_openapi(settings: Settings, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(openapi_document(settings), indent=2, sort_keys=True) + "\n"
    destination.write_text(document, encoding="utf-8", newline="\n")
    return destination
