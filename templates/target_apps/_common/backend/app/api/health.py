"""Health and readiness endpoints (part of the starter kit)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(session: Session = Depends(get_session)) -> dict[str, str]:  # noqa: B008 - FastAPI dependency
    session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable"}
