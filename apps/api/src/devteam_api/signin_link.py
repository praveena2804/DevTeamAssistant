"""Print the one-time browser sign-in link: ``uv run python -m devteam_api.signin_link``.

The token travels in the URL *fragment* (after ``#``), which browsers never send to any server or proxy.
The UI posts it once to ``POST /api/session`` and receives an HttpOnly cookie (D-29 (a)).
"""

import sys

from devteam_core.config import load_settings
from devteam_harness.iam import load_or_create_token

UI_URL = "http://127.0.0.1:5173"


def signin_link() -> str:
    settings = load_settings()
    token = load_or_create_token(settings.api_token_path)
    return f"{UI_URL}/signin#token={token}"


def main() -> int:
    sys.stdout.write(signin_link() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
