"""Request correlation and trace-context middleware (HC-10 / HC-11).

* Accepts a client ``X-Request-ID`` only when it is a short, safe token; otherwise generates one.
* Continues an incoming W3C ``traceparent`` as the parent of a new server span, or starts a new trace.
* Binds ``correlation_id`` for every log line emitted while handling the request.
* Echoes ``X-Request-ID`` and ``traceparent`` on the response and logs one ``http_request`` line.

Implemented as pure ASGI middleware so that context variables propagate into the endpoint task.
"""

import re
import time
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from devteam_core import tracing
from devteam_core.ids import new_id
from devteam_core.logging import bound_context, get_logger

REQUEST_ID_HEADER = "x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_log = get_logger("devteam_api.request")


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return str(value.decode("latin-1"))
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = _header(scope, REQUEST_ID_HEADER.encode())
        request_id = supplied if supplied and _SAFE_REQUEST_ID.fullmatch(supplied) else new_id("req")
        parent = tracing.parse_traceparent(_header(scope, b"traceparent"))
        ctx = tracing.child_of(parent) if parent else tracing.new_root()
        status: dict[str, Any] = {"code": 500}
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["traceparent"] = tracing.format_traceparent(ctx)
            await send(message)

        with tracing.use_context(ctx), bound_context(correlation_id=request_id):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                _log.info(
                    "http_request",
                    method=scope.get("method"),
                    path=scope.get("path"),
                    status=status["code"],
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
