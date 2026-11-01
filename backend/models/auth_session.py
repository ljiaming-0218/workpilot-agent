"""Persistent browser sessions; only hashes of opaque tokens are stored."""
from datetime import datetime
from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column
from backend.database import Base
from backend.models.common import TABLE_OPTIONS, utc_now


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = TABLE_OPTIONS.copy()
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), index=True)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
