"""Request dependencies: database session, authentication (HC-15), rate limits (HC-18), audit (HC-21).

Security is enforced here, in code, for every ``/api`` route (except signing in). The route-inventory
security test fails if a mutating route is added without these dependencies.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.orm import Session, sessionmaker

from devteam_harness import audit
from devteam_harness.iam import Principal, Role, token_matches
from devteam_harness.ratelimit import RateLimiter
from devteam_state.repositories.audit import Outcome

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def api_error(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message}, headers=headers)


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def get_db(request: Request) -> Iterator[Session]:
    """One session per request: committed when the endpoint succeeds, rolled back otherwise."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def record_separately(
    request: Request,
    *,
    action: str,
    outcome: Outcome,
    principal_id: str | None,
    project_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Write an audit event in its own transaction (used when the request itself fails)."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory.begin() as session:
        audit.record(
            session,
            action=action,
            outcome=outcome,
            principal_id=principal_id,
            request_id=request_id(request),
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def check_origin(request: Request, *, cookie_auth: bool) -> str | None:
    """Return a denial reason for unsafe cross-site requests, or ``None`` when acceptable."""
    if request.method in SAFE_METHODS:
        return None
    origin = request.headers.get("origin")
    allowed = request.app.state.settings.auth.allowed_origins
    if origin is not None and origin.rstrip("/") not in allowed:
        return "origin_not_allowed"
    if origin is None and cookie_auth:
        return "origin_missing"  # browsers always send Origin on unsafe requests; its absence is suspicious
    return None


def authenticate(request: Request) -> Principal:
    """Resolve the caller to the local principal via bearer token or session cookie (D-29 (a))."""
    state = request.app.state
    bearer = _bearer(request)
    cookie = request.cookies.get(state.settings.auth.cookie_name)
    via_bearer = token_matches(bearer, state.api_token)
    via_cookie = not via_bearer and token_matches(cookie, state.session_cookie_value)
    if not (via_bearer or via_cookie):
        record_separately(
            request,
            action="auth.denied",
            outcome="denied",
            principal_id=None,
            details={
                "reason": "missing_or_invalid_credentials",
                "method": request.method,
                "path": request.url.path,
            },
        )
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "auth.required",
            "Sign in first: open the sign-in link printed by scripts/dev.ps1.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    reason = check_origin(request, cookie_auth=via_cookie)
    principal: Principal = state.principal
    if reason is not None:
        record_separately(
            request,
            action="auth.denied",
            outcome="denied",
            principal_id=principal.id,
            details={"reason": reason, "method": request.method, "path": request.url.path},
        )
        raise api_error(status.HTTP_403_FORBIDDEN, "auth.origin_denied", "Request origin is not allowed.")
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(authenticate)]


def _limiter_for(request: Request) -> tuple[RateLimiter, str] | None:
    if request.method in SAFE_METHODS or not request.app.state.settings.ratelimit.enabled:
        return None
    limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
    group = "upload" if request.url.path.endswith("/documents") else "mutation"
    return limiters[group], group


def limit_rate(request: Request, principal: CurrentPrincipal) -> None:
    found = _limiter_for(request)
    if found is None:
        return
    limiter, group = found
    allowed, retry_after = limiter.try_acquire(f"{principal.id}:{group}")
    if not allowed:
        record_separately(
            request,
            action="ratelimit.exceeded",
            outcome="denied",
            principal_id=principal.id,
            details={"group": group, "method": request.method, "path": request.url.path},
        )
        raise api_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "ratelimit.exceeded",
            "Too many requests. Please wait a moment and try again.",
            headers={"Retry-After": str(max(1, round(retry_after)))},
        )


def require_role(role: Role) -> Callable[[Principal], Principal]:
    def dependency(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(role):
            raise api_error(
                status.HTTP_403_FORBIDDEN, "auth.forbidden", f"The '{role.value}' role is required."
            )
        return principal

    return dependency


@dataclass
class AuditScope:
    """Filled in by a mutating endpoint; written exactly once when the request finishes."""

    action: str
    project_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    written: bool = False

    def set(
        self,
        *,
        project_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        **details: Any,
    ) -> None:
        if project_id is not None:
            self.project_id = project_id
        if target_type is not None:
            self.target_type = target_type
        if target_id is not None:
            self.target_id = target_id
        self.details.update(details)


def audited(request: Request, db: DbSession, principal: CurrentPrincipal) -> Iterator[AuditScope]:
    """HC-21: one audit event per mutating request — in the same transaction on success,
    in a separate transaction on failure (so failures are recorded even though the change is rolled back)."""
    route = request.scope.get("route")
    default_action = getattr(route, "name", None) or f"{request.method} {request.url.path}"
    project_id = request.path_params.get("project_id")
    scope = AuditScope(action=default_action, project_id=project_id)
    try:
        yield scope
    except HTTPException as exc:
        raw: Any = exc.detail
        detail: dict[str, Any] = raw if isinstance(raw, dict) else {}
        outcome: Outcome = "denied" if exc.status_code in {401, 403} else "error"
        record_separately(
            request,
            action=scope.action,
            outcome=outcome,
            principal_id=principal.id,
            project_id=scope.project_id,
            target_type=scope.target_type,
            target_id=scope.target_id,
            details={**scope.details, "status": exc.status_code, "code": detail.get("code")},
        )
        raise
    except RequestValidationError as exc:
        record_separately(
            request,
            action=scope.action,
            outcome="error",
            principal_id=principal.id,
            project_id=scope.project_id,
            details={"status": 422, "code": "request.invalid", "errors": len(exc.errors())},
        )
        raise
    except Exception:
        record_separately(
            request,
            action=scope.action,
            outcome="error",
            principal_id=principal.id,
            project_id=scope.project_id,
            target_type=scope.target_type,
            target_id=scope.target_id,
            details={**scope.details, "status": 500},
        )
        raise
    else:
        audit.record(
            db,
            action=scope.action,
            outcome="success",
            principal_id=principal.id,
            request_id=request_id(request),
            project_id=scope.project_id,
            target_type=scope.target_type,
            target_id=scope.target_id,
            details=scope.details,
        )
        scope.written = True


Audit = Annotated[AuditScope, Depends(audited)]
ProjectUser = Annotated[Principal, Depends(require_role(Role.PROJECT_USER))]
