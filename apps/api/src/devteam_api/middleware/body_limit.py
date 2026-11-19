"""Reject oversized upload requests before the multipart body is read (HC-20 / HC-18).

The per-file limit is enforced again while streaming each file; this guard bounds the whole request so
an attacker cannot make the server spool gigabytes to disk first. Uploads must declare Content-Length.
"""

import json
import re

from starlette.types import ASGIApp, Receive, Scope, Send

_UPLOAD_PATH = re.compile(r"^/api/projects/[^/]+/documents/?$")


class UploadBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not _UPLOAD_PATH.match(scope["path"]):
            await self.app(scope, receive, send)
            return
        length = None
        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                length = value.decode("latin-1")
        if length is None or not length.isdigit():
            await _reject(
                send, 411, "upload.length_required", "The upload must declare its size (Content-Length)."
            )
            return
        if int(length) > self.max_bytes:
            limit_mb = self.max_bytes // (1024 * 1024)
            await _reject(
                send, 413, "upload.request_too_large", f"The upload is larger than {limit_mb} MB in total."
            )
            return
        await self.app(scope, receive, send)


async def _reject(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"detail": {"code": code, "message": message}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
