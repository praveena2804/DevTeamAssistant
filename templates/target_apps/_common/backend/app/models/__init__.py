"""ORM models. Every model module is imported here so Alembic sees the whole schema."""

from app.db import Base
from app.models.app_info import AppInfo

__all__ = ["AppInfo", "Base"]
