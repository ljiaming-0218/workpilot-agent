"""Plain-text knowledge documents; retrieval is implemented in a later phase."""

from sqlalchemy import String
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.common import TABLE_OPTIONS, TimestampMixin


class KnowledgeDoc(TimestampMixin, Base):
    __tablename__ = "knowledge_docs"
    __table_args__ = TABLE_OPTIONS.copy()

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(LONGTEXT)
    category: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(512))
