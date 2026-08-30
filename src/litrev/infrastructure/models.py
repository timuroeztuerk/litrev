from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from litrev.infrastructure.database import Base


class SourceRecord(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    doi: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    notes: Mapped[list[NoteRecord]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class NoteRecord(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    source: Mapped[SourceRecord] = relationship(back_populates="notes")
