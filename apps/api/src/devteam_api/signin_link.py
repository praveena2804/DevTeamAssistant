"""Print the one-time browser sign-in link: ``uv run python -m devteam_api.signin_link``.

The token travels in the URL *fragment* (after ``#``), which browsers never send to any server or proxy.
The UI posts it once to ``POST /api/session`` and receives an HttpOnly cookie (D-29 (a)). The token is read
from the OS credential store (HC-17, D-18); nothing is read from or written to a file.
"""

import sys

from devteam_core.config import Settings, load_settings
from devteam_harness.iam import load_api_token
from devteam_harness.secrets import SecretsManager, secrets_manager_from_settings

UI_URL = "http://127.0.0.1:5173"


def build_link(token: str) -> str:
    return f"{UI_URL}/signin#token={token}"


def signin_link(settings: Settings | None = None, secret_store: SecretsManager | None = None) -> str:
    resolved = settings or load_settings()
    store = secret_store or secrets_manager_from_settings(resolved)
    return build_link(load_api_token(store, resolved.api_token_path))


def main() -> int:
    sys.stdout.write(signin_link() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
