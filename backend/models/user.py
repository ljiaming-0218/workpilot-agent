"""Minimal user identity; authentication is outside Phase 1."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, TimestampMixin, enum_type
from backend.models.enums import UserRole


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = TABLE_OPTIONS.copy()

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole), default=UserRole.DEVELOPER)
