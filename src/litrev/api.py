from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from litrev.diagnostics import run_checks
from litrev.domain.documents import ConversionStatus
from litrev.domain.sources import ReadingStatus, SourceType
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import (
    AttachmentRecord,
    CollectionRecord,
    SourceRecord,
    TagRecord,
)
from litrev.infrastructure.storage import (
    LibraryPaths,
    ManagedFileCleanupError,
    ManagedFileConflictError,
    ManagedFileRecoveryError,
)
from litrev.services.documents import (
    MAX_DOCUMENT_BYTES,
    AttachmentNotConvertedError,
    AttachmentNotFoundError,
    AttachmentRemovalDatabaseError,
    AttachmentRemovalNotAllowedError,
    DocumentConversionFailure,
    DuplicateAttachmentError,
    can_remove_attachment,
    convert_attachment,
    ingest_document_bytes,
    read_attachment_markdown,
    remove_failed_attachment,
)


class SourceCreate(BaseModel):
    source_type: SourceType = SourceType.OTHER
    title: str
    doi: str | None = None


class SourceUpdate(BaseModel):
    source_type: SourceType
    title: str
    authors: list[str]
    publication_year: int | None
    venue: str | None
    doi: str | None
    url: str | None
    abstract: str | None
    language: str | None
    reading_status: ReadingStatus
    tags: list[str]
    collections: list[str]


class SourceRead(BaseModel):
    id: int
    source_type: SourceType
    title: str
    authors: list[str]
    publication_year: int | None
    venue: str | None
    doi: str | None
    url: str | None
    abstract: str | None
    language: str | None
    reading_status: ReadingStatus
    tags: list[str]
    collections: list[str]
    created_at: datetime


class AttachmentRead(BaseModel):
    id: int
    source_id: int
    original_filename: str
    media_type: str | None
    byte_size: int
    detected_format: str | None
    conversion_status: ConversionStatus
    conversion_message: str | None
    conversion_diagnostics: dict[str, object] | None
    has_extracted_text: bool
    can_remove: bool
    created_at: datetime
    updated_at: datetime


class SourceDetailRead(SourceRead):
    attachments: list[AttachmentRead]


class ImportedDocumentRead(BaseModel):
    source: SourceRead
    attachment: AttachmentRead


class ExtractedTextRead(BaseModel):
    attachment_id: int
    markdown: str


def create_app(database: Database | None = None) -> FastAPI:
    active_database = database or Database.from_library(LibraryPaths.default())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        active_database.migrate()
        yield

    application = FastAPI(
        title="Litrev local API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:1420",
            "http://localhost:1420",
            "http://tauri.localhost",
            "tauri://localhost",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "technology": run_checks()}

    @application.get("/api/sources", response_model=list[SourceRead])
    async def list_sources() -> list[SourceRead]:
        with active_database.session() as session:
            records = session.scalars(
                select(SourceRecord)
                .options(
                    selectinload(SourceRecord.tags),
                    selectinload(SourceRecord.collections),
                )
                .order_by(SourceRecord.title)
            )
            return [_source_read(record) for record in records]

    @application.get("/api/sources/{source_id}", response_model=SourceDetailRead)
    async def get_source(source_id: int) -> SourceDetailRead:
        return _read_source_detail(active_database, source_id)

    @application.put("/api/sources/{source_id}", response_model=SourceDetailRead)
    async def update_source(source_id: int, source: SourceUpdate) -> SourceDetailRead:
        title = _clean_source_title(source.title)
        authors = _clean_authors(source.authors)
        publication_year = _clean_publication_year(source.publication_year)
        venue = _clean_optional_text(source.venue, label="Venues", maximum_length=500)
        doi = _clean_optional_text(source.doi, label="DOIs", maximum_length=255)
        url = _clean_url(source.url)
        abstract = _clean_optional_text(
            source.abstract,
            label="Abstracts",
            maximum_length=100_000,
        )
        language = _clean_optional_text(source.language, label="Languages", maximum_length=35)
        tags = _clean_organization_names(source.tags, singular="Tag", plural="tags")
        collections = _clean_organization_names(
            source.collections,
            singular="Collection",
            plural="collections",
        )

        with active_database.session() as session:
            record = session.scalar(
                select(SourceRecord)
                .where(SourceRecord.id == source_id)
                .options(
                    selectinload(SourceRecord.tags),
                    selectinload(SourceRecord.collections),
                )
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Source not found")
            with session.no_autoflush:
                resolved_tags = _resolve_tags(session, tags)
                resolved_collections = _resolve_collections(session, collections)
                record.source_type = source.source_type.value
                record.title = title
                record.authors = authors
                record.publication_year = publication_year
                record.venue = venue
                record.doi = doi
                record.url = url
                record.abstract = abstract
                record.language = language
                record.reading_status = source.reading_status.value
                record.tags = resolved_tags
                record.collections = resolved_collections
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A source with this DOI already exists.",
                ) from error

        return _read_source_detail(active_database, source_id)

    @application.post(
        "/api/sources",
        response_model=SourceRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_source(source: SourceCreate) -> SourceRead:
        title = _clean_source_title(source.title)
        doi = _clean_optional_text(source.doi, label="DOIs", maximum_length=255)

        with active_database.session() as session:
            record = SourceRecord(source_type=source.source_type.value, title=title, doi=doi)
            session.add(record)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A source with this DOI already exists.",
                ) from error
            session.refresh(record)
            return _source_read(record)

    @application.post(
        "/api/imports",
        response_model=ImportedDocumentRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def import_document(
        source_type: Annotated[SourceType, Form()],
        title: Annotated[str, Form()],
        document: Annotated[UploadFile, File()],
    ) -> ImportedDocumentRead:
        clean_title = _clean_source_title(title)
        original_filename = _clean_original_filename(document.filename)
        if document.size is not None and document.size > MAX_DOCUMENT_BYTES:
            raise _oversized_document_error()

        content = await document.read(MAX_DOCUMENT_BYTES + 1)
        if len(content) > MAX_DOCUMENT_BYTES:
            raise _oversized_document_error()

        try:
            ingested = ingest_document_bytes(
                active_database,
                source_type=source_type,
                title=clean_title,
                data=content,
                original_filename=original_filename,
                media_type=document.content_type,
            )
        except DuplicateAttachmentError as error:
            duplicate = _read_attachment(active_database, error.attachment_id)
            if duplicate is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "duplicate",
                        "message": "This document is already in the library.",
                        "attachment_id": error.attachment_id,
                    },
                ) from error
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "duplicate",
                    "message": "This document is already in the library.",
                    "source_id": duplicate.source_id,
                    "attachment_id": duplicate.id,
                },
            ) from error

        source = _read_source_detail(active_database, ingested.source_id)
        attachment = next(
            attachment
            for attachment in source.attachments
            if attachment.id == ingested.attachment_id
        )
        return ImportedDocumentRead(
            source=source,
            attachment=attachment,
        )

    @application.post(
        "/api/attachments/{attachment_id}/convert",
        response_model=AttachmentRead,
    )
    def convert_saved_attachment(attachment_id: int) -> AttachmentRead:
        try:
            converted = convert_attachment(active_database, attachment_id)
        except DocumentConversionFailure:
            failed = _read_attachment(active_database, attachment_id)
            if failed is None:
                raise HTTPException(status_code=404, detail="Attachment not found") from None
            return failed
        except AttachmentNotFoundError as error:
            raise HTTPException(status_code=404, detail="Attachment not found") from error
        except ManagedFileConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "managed_file_conflict",
                    "message": "The saved original is missing or has changed.",
                },
            ) from error
        return _attachment_read(converted)

    @application.get(
        "/api/attachments/{attachment_id}/extracted-text",
        response_model=ExtractedTextRead,
    )
    def get_extracted_text(attachment_id: int) -> ExtractedTextRead:
        try:
            markdown = read_attachment_markdown(active_database, attachment_id)
        except AttachmentNotFoundError as error:
            raise HTTPException(status_code=404, detail="Attachment not found") from error
        except AttachmentNotConvertedError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "not_converted",
                    "message": "This attachment has no extracted text yet.",
                },
            ) from error
        except ManagedFileConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "managed_file_conflict",
                    "message": "The extracted text is missing or has changed.",
                },
            ) from error
        return ExtractedTextRead(attachment_id=attachment_id, markdown=markdown)

    @application.delete(
        "/api/attachments/{attachment_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_failed_attachment(attachment_id: int) -> None:
        try:
            remove_failed_attachment(active_database, attachment_id)
        except AttachmentNotFoundError as error:
            raise HTTPException(status_code=404, detail="Attachment not found") from error
        except AttachmentRemovalNotAllowedError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "attachment_not_removable",
                    "message": "Only a document with a failed extraction can be removed.",
                },
            ) from error
        except AttachmentRemovalDatabaseError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "attachment_database_removal_failed",
                    "message": (
                        "The failed document could not be removed; its saved files were restored."
                    ),
                },
            ) from error
        except ManagedFileConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "managed_file_conflict",
                    "message": "The saved document paths are unsafe to remove.",
                },
            ) from error
        except ManagedFileRecoveryError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "attachment_removal_recovery_failed",
                    "message": "Removal stopped, but the saved files could not all be restored.",
                },
            ) from error
        except ManagedFileCleanupError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "attachment_cleanup_incomplete",
                    "message": (
                        "The document was removed, but temporary file cleanup did not finish."
                    ),
                },
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "attachment_removal_failed",
                    "message": (
                        "The failed document could not be removed; its saved files were restored."
                    ),
                },
            ) from error

    return application


def _clean_source_title(title: str) -> str:
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(status_code=422, detail="A source title is required")
    if len(clean_title) > 500:
        raise HTTPException(status_code=422, detail="Source titles are limited to 500 characters")
    return clean_title


def _clean_authors(authors: list[str]) -> list[str]:
    if len(authors) > 100:
        raise HTTPException(status_code=422, detail="Sources are limited to 100 authors")
    cleaned: list[str] = []
    for author in authors:
        clean_author = author.strip()
        if not clean_author:
            raise HTTPException(status_code=422, detail="Author names cannot be empty")
        if len(clean_author) > 500:
            raise HTTPException(
                status_code=422, detail="Author names are limited to 500 characters"
            )
        cleaned.append(clean_author)
    return cleaned


def _clean_organization_names(
    names: list[str],
    *,
    singular: str,
    plural: str,
) -> list[tuple[str, str]]:
    if len(names) > 50:
        raise HTTPException(
            status_code=422,
            detail=f"Sources are limited to 50 {plural}",
        )

    cleaned: dict[str, str] = {}
    for name in names:
        clean_name = " ".join(name.split())
        if not clean_name:
            raise HTTPException(status_code=422, detail=f"{singular} names cannot be empty")
        if len(clean_name) > 100:
            raise HTTPException(
                status_code=422,
                detail=f"{singular} names are limited to 100 characters",
            )
        normalized_name = clean_name.casefold()
        cleaned.setdefault(normalized_name, clean_name)
    return [(display_name, normalized_name) for normalized_name, display_name in cleaned.items()]


def _resolve_tags(session: Session, names: list[tuple[str, str]]) -> list[TagRecord]:
    normalized_names = [normalized for _, normalized in names]
    existing = {
        record.normalized_name: record
        for record in session.scalars(
            select(TagRecord).where(TagRecord.normalized_name.in_(normalized_names))
        )
    }
    return [
        existing.get(normalized) or TagRecord(name=name, normalized_name=normalized)
        for name, normalized in names
    ]


def _resolve_collections(
    session: Session,
    names: list[tuple[str, str]],
) -> list[CollectionRecord]:
    normalized_names = [normalized for _, normalized in names]
    existing = {
        record.normalized_name: record
        for record in session.scalars(
            select(CollectionRecord).where(CollectionRecord.normalized_name.in_(normalized_names))
        )
    }
    return [
        existing.get(normalized) or CollectionRecord(name=name, normalized_name=normalized)
        for name, normalized in names
    ]


def _clean_publication_year(publication_year: int | None) -> int | None:
    if publication_year is not None and not 1 <= publication_year <= 9999:
        raise HTTPException(status_code=422, detail="Publication years must be between 1 and 9999")
    return publication_year


def _clean_optional_text(
    value: str | None,
    *,
    label: str,
    maximum_length: int,
) -> str | None:
    cleaned = (value or "").strip() or None
    if cleaned is not None and len(cleaned) > maximum_length:
        raise HTTPException(
            status_code=422,
            detail=f"{label} are limited to {maximum_length} characters",
        )
    return cleaned


def _clean_url(value: str | None) -> str | None:
    cleaned = _clean_optional_text(value, label="URLs", maximum_length=2048)
    if cleaned is None:
        return None
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="URLs must use http or https")
    return cleaned


def _clean_original_filename(filename: str | None) -> str:
    clean_filename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not clean_filename:
        return "document"
    if len(clean_filename) > 1024:
        raise HTTPException(status_code=422, detail="Document filenames are too long")
    return clean_filename


def _oversized_document_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail={
            "code": ConversionStatus.OVERSIZED.value,
            "message": "Documents are limited to 50 MB.",
            "maximum_byte_size": MAX_DOCUMENT_BYTES,
        },
    )


def _read_source_detail(database: Database, source_id: int) -> SourceDetailRead:
    with database.session() as session:
        record = session.scalar(
            select(SourceRecord)
            .where(SourceRecord.id == source_id)
            .options(
                selectinload(SourceRecord.attachments),
                selectinload(SourceRecord.tags),
                selectinload(SourceRecord.collections),
            )
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Source not found")
        source = _source_read(record)
        return SourceDetailRead(
            **source.model_dump(),
            attachments=[
                _attachment_read(attachment)
                for attachment in sorted(record.attachments, key=lambda item: item.id)
            ],
        )


def _source_read(record: SourceRecord) -> SourceRead:
    return SourceRead(
        id=record.id,
        source_type=SourceType(record.source_type),
        title=record.title,
        authors=list(record.authors),
        publication_year=record.publication_year,
        venue=record.venue,
        doi=record.doi,
        url=record.url,
        abstract=record.abstract,
        language=record.language,
        reading_status=ReadingStatus(record.reading_status),
        tags=[tag.name for tag in sorted(record.tags, key=lambda item: item.normalized_name)],
        collections=[
            collection.name
            for collection in sorted(
                record.collections,
                key=lambda item: item.normalized_name,
            )
        ],
        created_at=record.created_at,
    )


def _read_attachment(database: Database, attachment_id: int) -> AttachmentRead | None:
    with database.session() as session:
        record = session.get(AttachmentRecord, attachment_id)
        return _attachment_read(record) if record is not None else None


def _attachment_read(record: AttachmentRecord) -> AttachmentRead:
    return AttachmentRead(
        id=record.id,
        source_id=record.source_id,
        original_filename=record.original_filename,
        media_type=record.media_type,
        byte_size=record.byte_size,
        detected_format=record.detected_format,
        conversion_status=ConversionStatus(record.conversion_status),
        conversion_message=record.conversion_message,
        conversion_diagnostics=record.conversion_diagnostics,
        has_extracted_text=(
            record.conversion_status == ConversionStatus.SUCCEEDED.value
            and record.extracted_path is not None
        ),
        can_remove=can_remove_attachment(record),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


app = create_app()
