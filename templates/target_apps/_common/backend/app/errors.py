"""Standard error format: ``{"error": {"code", "message", "request_id"}}`` for every error response."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def _body(request: Request, code: str, message: str) -> dict[str, dict[str, str]]:
    return {
        "error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", "")}
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            _body(request, f"http_{exc.status_code}", str(exc.detail)), status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(_body(request, "validation_error", "the request is not valid"), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(_body(request, "internal_error", "an unexpected error occurred"), status_code=500)
