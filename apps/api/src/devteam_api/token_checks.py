"""In-process verification used by the D-18a key migration (checks 3 and 4, AP-007).

Both checks start the real application (same settings, same database) with the key taken from the
credential store, and talk to it through an in-process ASGI client. Nothing is sent over the network.

* Check 3 ``api_authenticates``: an authenticated API request with the stored key succeeds, and the
  same request with a wrong key is refused (so the check cannot pass vacuously).
* Check 4 ``signin_link_works``: the sign-in link built from the stored key is identical to the existing
  link (built from the original file key); the token in its fragment signs in through ``POST /api/session``
  exactly as the browser does, and the resulting session cookie is accepted.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from devteam_api.main import create_app
from devteam_api.signin_link import build_link
from devteam_core.config import Settings


def _client(settings: Settings, token: str) -> TestClient:
    return TestClient(create_app(settings, configure_logs=False, token_provider=lambda: token))


def api_authenticates(settings: Settings) -> Callable[[str], bool]:
    def check(stored_token: str) -> bool:
        with _client(settings, stored_token) as client:
            accepted = client.get("/api/projects", headers={"Authorization": f"Bearer {stored_token}"})
            refused = client.get("/api/projects", headers={"Authorization": "Bearer " + "x" * 43})
            return bool(accepted.status_code == 200 and refused.status_code == 401)

    return check


def signin_link_works(settings: Settings) -> Callable[[str, str], bool]:
    def check(original_token: str, stored_token: str) -> bool:
        existing_link = build_link(original_token)
        new_link = build_link(stored_token)
        if new_link != existing_link:
            return False
        fragment = urlsplit(new_link).fragment
        if not fragment.startswith("token="):
            return False
        origin = settings.auth.allowed_origins[0]
        with _client(settings, stored_token) as client:
            signed_in = client.post(
                "/api/session", json={"token": fragment[len("token=") :]}, headers={"Origin": origin}
            )
            cookie_set = settings.auth.cookie_name in signed_in.cookies
            session = client.get("/api/session", headers={"Origin": origin})
            return bool(signed_in.status_code == 200 and cookie_set and session.status_code == 200)

    return check
