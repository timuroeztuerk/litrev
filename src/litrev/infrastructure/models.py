from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from litrev.domain.documents import ConversionStatus
from litrev.domain.sources import ReadingStatus, SourceType
from litrev.infrastructure.database import Base

source_tags = Table(
    "source_tags",
    Base.metadata,
    Column("source_id", ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

source_collections = Table(
    "source_collections",
    Base.metadata,
    Column("source_id", ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "collection_id",
        ForeignKey("collections.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class SourceRecord(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(
        String(20),
        default=SourceType.OTHER.value,
        server_default=SourceType.OTHER.value,
    )
    title: Mapped[str] = mapped_column(String(500))
    authors: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    publication_year: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(String(500))
    doi: Mapped[str | None] = mapped_column(String(255), unique=True)
    url: Mapped[str | None] = mapped_column(String(2048))
    abstract: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(35))
    reading_status: Mapped[str] = mapped_column(
        String(20),
        default=ReadingStatus.UNREAD.value,
        server_default=ReadingStatus.UNREAD.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    notes: Mapped[list[NoteRecord]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    attachments: Mapped[list[AttachmentRecord]] = relationship(back_populates="source")
    tags: Mapped[list[TagRecord]] = relationship(
        secondary=source_tags,
        back_populates="sources",
        order_by="TagRecord.normalized_name",
        passive_deletes=True,
    )
    collections: Mapped[list[CollectionRecord]] = relationship(
        secondary=source_collections,
        back_populates="sources",
        order_by="CollectionRecord.normalized_name",
        passive_deletes=True,
    )


class TagRecord(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_tags_normalized_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(300))

    sources: Mapped[list[SourceRecord]] = relationship(
        secondary=source_tags,
        back_populates="tags",
        passive_deletes=True,
    )


class CollectionRecord(Base):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_collections_normalized_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    normalized_name: Mapped[str] = mapped_column(String(300))

    sources: Mapped[list[SourceRecord]] = relationship(
        secondary=source_collections,
        back_populates="collections",
        passive_deletes=True,
    )


class NoteRecord(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    source: Mapped[SourceRecord] = relationship(back_populates="notes")


class AttachmentRecord(Base):
    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_attachments_byte_size_nonnegative"),
        UniqueConstraint("checksum", name="uq_attachments_checksum"),
        UniqueConstraint("managed_path", name="uq_attachments_managed_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), index=True
    )
    original_filename: Mapped[str] = mapped_column(String(1024))
    managed_path: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(255))
    byte_size: Mapped[int] = mapped_column()
    checksum: Mapped[str] = mapped_column(String(64))
    detected_format: Mapped[str | None] = mapped_column(String(100))
    conversion_status: Mapped[str] = mapped_column(
        String(32), default=ConversionStatus.PENDING.value
    )
    extracted_path: Mapped[str | None] = mapped_column(String(255))
    conversion_message: Mapped[str | None] = mapped_column(Text)
    conversion_diagnostics: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    source: Mapped[SourceRecord] = relationship(back_populates="attachments")
