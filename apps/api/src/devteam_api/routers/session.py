"""Sign-in for the local UI (D-29 (a)): exchange the install token for an HttpOnly session cookie."""

from fastapi import APIRouter, Request, Response, status

from devteam_api.deps import (
    Audit,
    CurrentPrincipal,
    DbSession,
    api_error,
    check_origin,
    record_separately,
    request_id,
)
from devteam_api.schemas import ERROR_RESPONSES, SessionInfo, SessionRequest
from devteam_harness import audit
from devteam_harness.iam import Principal, token_matches
from devteam_harness.ratelimit import RateLimiter

router = APIRouter(prefix="/api", tags=["session"])


def _info(principal: Principal) -> SessionInfo:
    return SessionInfo(
        principal_id=principal.id, display_name=principal.display_name, roles=sorted(principal.roles)
    )


@router.post(
    "/session",
    response_model=SessionInfo,
    name="session.create",
    summary="Sign in with the local install token",
    responses=ERROR_RESPONSES,
)
def create_session(body: SessionRequest, request: Request, response: Response, db: DbSession) -> SessionInfo:
    state = request.app.state
    principal: Principal = state.principal
    client = request.client.host if request.client else "unknown"
    if state.settings.ratelimit.enabled:
        limiter: RateLimiter = state.rate_limiters["session"]
        allowed, retry_after = limiter.try_acquire(f"session:{client}")
        if not allowed:
            record_separately(
                request,
                action="ratelimit.exceeded",
                outcome="denied",
                principal_id=None,
                details={"group": "session"},
            )
            raise api_error(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "ratelimit.exceeded",
                "Too many sign-in attempts. Please wait a moment.",
                headers={"Retry-After": str(max(1, round(retry_after)))},
            )
    reason = check_origin(request, cookie_auth=False)
    if reason is not None:
        record_separately(
            request,
            action="auth.denied",
            outcome="denied",
            principal_id=None,
            details={"reason": reason, "path": request.url.path},
        )
        raise api_error(status.HTTP_403_FORBIDDEN, "auth.origin_denied", "Request origin is not allowed.")
    if not token_matches(body.token, state.api_token):
        record_separately(
            request,
            action="auth.denied",
            outcome="denied",
            principal_id=None,
            details={"reason": "invalid_token", "path": request.url.path},
        )
        raise api_error(status.HTTP_401_UNAUTHORIZED, "auth.invalid_token", "The sign-in key is not valid.")
    response.set_cookie(
        state.settings.auth.cookie_name,
        state.session_cookie_value,
        httponly=True,
        samesite="strict",
        path="/api",
        secure=False,  # loopback HTTP only; the API refuses non-loopback binding
    )
    audit.record(
        db,
        action="session.create",
        outcome="success",
        principal_id=principal.id,
        request_id=request_id(request),
    )
    return _info(principal)


@router.get("/session", response_model=SessionInfo, name="session.read", responses=ERROR_RESPONSES)
def read_session(principal: CurrentPrincipal) -> SessionInfo:
    return _info(principal)


@router.delete(
    "/session", status_code=status.HTTP_204_NO_CONTENT, name="session.delete", responses=ERROR_RESPONSES
)
def delete_session(request: Request, scope: Audit) -> Response:
    scope.set(target_type="session")
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(request.app.state.settings.auth.cookie_name, path="/api")
    return response
