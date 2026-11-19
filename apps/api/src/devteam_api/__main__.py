"""Run the platform API: ``uv run python -m devteam_api``.

Host and port come from the validated configuration (loopback by default). Logging is configured by the
application itself, so uvicorn's own logging configuration is disabled.
"""

import uvicorn

from devteam_api.main import create_app
from devteam_core.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.api.host,
        port=settings.api.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
