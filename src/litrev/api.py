from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from litrev.diagnostics import run_checks
from litrev.domain.documents import ConversionStatus
from litrev.domain.sources import SourceType
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import AttachmentRecord, SourceRecord
from litrev.infrastructure.storage import LibraryPaths, ManagedFileConflictError
from litrev.services.documents import (
    MAX_DOCUMENT_BYTES,
    AttachmentNotConvertedError,
    AttachmentNotFoundError,
    DocumentConversionFailure,
    DuplicateAttachmentError,
    convert_attachment,
    ingest_document_bytes,
    read_attachment_markdown,
)


class SourceCreate(BaseModel):
    source_type: SourceType = SourceType.OTHER
    title: str
    doi: str | None = None


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_type: SourceType
    title: str
    doi: str | None
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
    async def list_sources() -> list[SourceRecord]:
        with active_database.session() as session:
            return list(session.scalars(select(SourceRecord).order_by(SourceRecord.title)))

    @application.get("/api/sources/{source_id}", response_model=SourceDetailRead)
    async def get_source(source_id: int) -> SourceDetailRead:
        return _read_source_detail(active_database, source_id)

    @application.post(
        "/api/sources",
        response_model=SourceRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_source(source: SourceCreate) -> SourceRecord:
        title = _clean_source_title(source.title)
        doi = (source.doi or "").strip() or None

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
            session.expunge(record)
            return record

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
            source=SourceRead(
                id=source.id,
                source_type=source.source_type,
                title=source.title,
                doi=source.doi,
                created_at=source.created_at,
            ),
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

    return application


def _clean_source_title(title: str) -> str:
    clean_title = title.strip()
    if not clean_title:
        raise HTTPException(status_code=422, detail="A source title is required")
    if len(clean_title) > 500:
        raise HTTPException(status_code=422, detail="Source titles are limited to 500 characters")
    return clean_title


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
            .options(selectinload(SourceRecord.attachments))
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Source not found")
        return SourceDetailRead(
            id=record.id,
            source_type=SourceType(record.source_type),
            title=record.title,
            doi=record.doi,
            created_at=record.created_at,
            attachments=[
                _attachment_read(attachment)
                for attachment in sorted(record.attachments, key=lambda item: item.id)
            ],
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
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


app = create_app()
