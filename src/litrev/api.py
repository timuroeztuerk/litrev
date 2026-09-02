from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool

from litrev.diagnostics import run_checks
from litrev.domain.documents import ConversionStatus
from litrev.domain.isbn import (
    EmptyIsbnError,
    IsbnChecksumError,
    IsbnIdentity,
    IsbnValidationError,
    MalformedIsbnError,
    UnsupportedIsbnPrefixError,
    isbn_identity,
)
from litrev.domain.sources import ReadingStatus, SourceType
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import (
    AttachmentRecord,
    CollectionRecord,
    HighlightRecord,
    NoteRecord,
    SourceCitationKeyRecord,
    SourceIdentifierRecord,
    SourceMetadataLookupRecord,
    SourceRecord,
    TagRecord,
)
from litrev.infrastructure.storage import (
    LibraryPaths,
    ManagedAttachmentStore,
    ManagedFileCleanupError,
    ManagedFileConflictError,
    ManagedFileRecoveryError,
)
from litrev.services.bibliographies import (
    MAX_BIBLIOGRAPHY_BYTES,
    BibliographyCitationKey,
    BibliographyEntryLimitError,
    BibliographyExportSource,
    BibliographyFormat,
    BibliographyIdentifier,
    BibliographySerializationError,
    BibliographySourceDraft,
    EmptyBibliographyError,
    EmptyBibliographyExportError,
    MalformedBibliographyError,
    UnsupportedBibliographyFormatError,
    doi_key,
    normalize_imported_doi,
    parse_bibliography,
    serialize_bibliography,
)
from litrev.services.documents import (
    MAX_DOCUMENT_BYTES,
    AttachmentNotConvertedError,
    AttachmentNotFoundError,
    AttachmentRemovalBlockedByHighlightsError,
    AttachmentRemovalBlockedByNotesError,
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
from litrev.services.doi_metadata import (
    DoiMetadataMalformedError,
    DoiMetadataMismatchError,
    DoiMetadataNotFoundError,
    DoiMetadataRateLimitedError,
    DoiMetadataUnavailableError,
    InvalidDoiError,
    lookup_crossref_metadata,
    normalize_doi_for_lookup,
)
from litrev.services.metadata import RetrievedMetadata
from litrev.services.notes import (
    MAX_HIGHLIGHT_RECTANGLES,
    MAX_HIGHLIGHT_TEXT_LENGTH,
    MAX_NOTE_BODY_LENGTH,
    NewHighlightDraft,
    ReaderNoteAttachmentNotFoundError,
    ReaderNoteDatabaseError,
    ReaderNoteNotFoundError,
    ReaderNoteNotPdfError,
    ReaderNoteRelationshipError,
    ReaderNoteValidationError,
    create_reader_note,
    update_reader_note,
)
from litrev.services.open_library import (
    OpenLibraryMetadataAmbiguousError,
    OpenLibraryMetadataMalformedError,
    OpenLibraryMetadataMismatchError,
    OpenLibraryMetadataNotFoundError,
    OpenLibraryMetadataRateLimitedError,
    OpenLibraryMetadataUnavailableError,
    lookup_open_library_metadata,
)
from litrev.services.sources import (
    SourceNotFoundError,
    SourceRemovalDatabaseError,
    remove_source,
)


class SourceCreate(BaseModel):
    source_type: SourceType = SourceType.OTHER
    title: str
    doi: str | None = None


class SourceIdentifierRead(BaseModel):
    identifier_type: str
    value: str


class SourceCitationKeyRead(BaseModel):
    bibliography_format: BibliographyFormat
    value: str


MetadataField = Literal[
    "source_type",
    "title",
    "authors",
    "publication_year",
    "venue",
    "url",
    "abstract",
    "language",
    "identifiers",
]

_METADATA_FIELDS: tuple[MetadataField, ...] = (
    "source_type",
    "title",
    "authors",
    "publication_year",
    "venue",
    "url",
    "abstract",
    "language",
    "identifiers",
)


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
    identifiers: list[SourceIdentifierRead]


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
    identifiers: list[SourceIdentifierRead]
    citation_keys: list[SourceCitationKeyRead]
    created_at: datetime


class MetadataProvenanceRead(BaseModel):
    lookup_id: int
    provider: str
    provider_url: str
    identifier_type: Literal["doi", "isbn"]
    requested_identifier: str
    retrieved_identifier: str
    retrieved_at: datetime
    applied_fields: list[MetadataField]
    applied_at: datetime


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
    metadata_provenance: list[MetadataProvenanceRead]


class ImportedDocumentRead(BaseModel):
    source: SourceRead
    attachment: AttachmentRead


class BibliographyImportSkippedRead(BaseModel):
    entry_id: str
    title: str
    doi: str
    reason: Literal["existing_doi", "duplicate_doi_in_file"]


class BibliographyImportRead(BaseModel):
    bibliography_format: BibliographyFormat
    total_entries: int
    imported: list[SourceRead]
    skipped: list[BibliographyImportSkippedRead]


class MetadataProposalRead(BaseModel):
    source_type: SourceType | None
    title: str | None
    authors: list[str] | None
    publication_year: int | None
    venue: str | None
    url: str | None
    abstract: str | None
    language: str | None
    identifiers: list[SourceIdentifierRead] | None


class MetadataLookupRead(BaseModel):
    id: int
    provider: str
    provider_url: str
    identifier_type: Literal["doi", "isbn"]
    requested_identifier: str
    retrieved_identifier: str
    retrieved_at: datetime
    proposal: MetadataProposalRead
    available_fields: list[MetadataField]
    conflicting_fields: list[MetadataField]


class DoiMetadataPreviewCreate(BaseModel):
    doi: str


class ExistingDoiSourceRead(BaseModel):
    id: int
    source_type: SourceType
    title: str
    doi: str


class ExistingDoiMetadataPreviewRead(BaseModel):
    kind: Literal["existing_source"]
    normalized_doi: str
    existing_source: ExistingDoiSourceRead


class ProviderDoiMetadataPreviewRead(BaseModel):
    kind: Literal["proposal"]
    normalized_doi: str
    provider: str
    provider_url: str
    retrieved_doi: str
    retrieved_at: datetime
    proposal_fingerprint: str
    proposal: MetadataProposalRead
    available_fields: list[MetadataField]


class DoiSourceCreate(BaseModel):
    doi: str
    proposal_fingerprint: str
    fields: list[MetadataField]


class IsbnMetadataPreviewCreate(BaseModel):
    isbn: str
    lookup_if_local_match: bool = False


class ExistingIsbnSourceRead(BaseModel):
    id: int
    source_type: SourceType
    title: str
    isbn_values: list[str]


class ExistingIsbnMetadataPreviewRead(BaseModel):
    kind: Literal["existing_sources"]
    input_isbn: str
    normalized_isbn: str
    canonical_isbn13: str
    existing_sources: list[ExistingIsbnSourceRead]


class ProviderIsbnMetadataPreviewRead(BaseModel):
    kind: Literal["proposal"]
    input_isbn: str
    normalized_isbn: str
    canonical_isbn13: str
    provider: str
    provider_url: str
    retrieved_isbn: str
    retrieved_at: datetime
    proposal_fingerprint: str
    proposal: MetadataProposalRead
    available_fields: list[MetadataField]


class IsbnSourceCreate(BaseModel):
    isbn: str
    proposal_fingerprint: str
    fields: list[MetadataField]


class MetadataApply(BaseModel):
    fields: list[MetadataField]


class IsbnMetadataLookupCreate(BaseModel):
    isbn: str


class ExtractedTextRead(BaseModel):
    attachment_id: int
    markdown: str


class HighlightRectangle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> HighlightRectangle:
        values = (self.x, self.y, self.width, self.height)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Highlight rectangle coordinates must be finite.")
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("Highlight rectangles must stay within the page.")
        return self


class HighlightCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    selected_text: str = Field(min_length=1, max_length=MAX_HIGHLIGHT_TEXT_LENGTH)
    rectangles: list[HighlightRectangle] = Field(
        min_length=1,
        max_length=MAX_HIGHLIGHT_RECTANGLES,
    )

    @field_validator("selected_text")
    @classmethod
    def selected_text_must_be_usable(cls, selected_text: str) -> str:
        if not selected_text.strip():
            raise ValueError("Selected text cannot be blank.")
        return selected_text


class HighlightRead(BaseModel):
    id: int
    attachment_id: int
    source_id: int
    page_number: int
    selected_text: str
    rectangles: list[HighlightRectangle]
    created_at: datetime


class ReaderNoteHighlightCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_text: str = Field(min_length=1, max_length=MAX_HIGHLIGHT_TEXT_LENGTH)
    rectangles: list[HighlightRectangle] = Field(
        min_length=1,
        max_length=MAX_HIGHLIGHT_RECTANGLES,
    )

    @field_validator("selected_text")
    @classmethod
    def selected_text_must_be_usable(cls, selected_text: str) -> str:
        if not selected_text.strip():
            raise ValueError("Selected text cannot be blank.")
        return selected_text


class ReaderNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=MAX_NOTE_BODY_LENGTH)
    highlight_id: int | None = Field(default=None, ge=1)
    new_highlight: ReaderNoteHighlightCreate | None = None

    @field_validator("body")
    @classmethod
    def body_must_be_usable(cls, body: str) -> str:
        if not body.strip():
            raise ValueError("Reader notes cannot be empty.")
        return body

    @model_validator(mode="after")
    def validate_highlight_choice(self) -> ReaderNoteCreate:
        if self.highlight_id is not None and self.new_highlight is not None:
            raise ValueError("Choose either a saved highlight or a new highlight, not both.")
        return self


class ReaderNoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=MAX_NOTE_BODY_LENGTH)

    @field_validator("body")
    @classmethod
    def body_must_be_usable(cls, body: str) -> str:
        if not body.strip():
            raise ValueError("Reader notes cannot be empty.")
        return body


class ReaderNoteRead(BaseModel):
    id: int
    source_id: int
    source_title: str
    attachment_id: int
    original_filename: str
    page_number: int
    body: str
    highlight: HighlightRead | None
    attachment_availability: Literal["available", "missing_or_changed", "storage_unavailable"]
    created_at: datetime


class ReaderDocumentRead(BaseModel):
    attachment_id: int
    source_id: int
    source_title: str
    original_filename: str
    byte_size: int
    attachment_availability: Literal["available", "missing_or_changed", "storage_unavailable"]
    reader_notes: list[ReaderNoteRead]


_BIBLIOGRAPHY_EXPORT_RESPONSES = {
    BibliographyFormat.BIBTEX: (
        "litrev-library.bib",
        "application/x-bibtex; charset=utf-8",
    ),
    BibliographyFormat.RIS: (
        "litrev-library.ris",
        "application/x-research-info-systems; charset=utf-8",
    ),
    BibliographyFormat.CSL_JSON: (
        "litrev-library.json",
        "application/vnd.citationstyles.csl+json; charset=utf-8",
    ),
}


def create_app(
    database: Database | None = None,
    *,
    doi_metadata_provider: Callable[[str], RetrievedMetadata] = lookup_crossref_metadata,
    isbn_metadata_provider: Callable[[str], RetrievedMetadata] = lookup_open_library_metadata,
) -> FastAPI:
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
        expose_headers=["Accept-Ranges", "Content-Length", "Content-Range"],
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
                    selectinload(SourceRecord.identifiers),
                    selectinload(SourceRecord.citation_keys),
                )
                .order_by(SourceRecord.title)
            )
            return [_source_read(record) for record in records]

    @application.get("/api/sources/{source_id}", response_model=SourceDetailRead)
    async def get_source(source_id: int) -> SourceDetailRead:
        return _read_source_detail(active_database, source_id)

    @application.get("/api/reader/documents", response_model=list[ReaderDocumentRead])
    async def list_reader_documents() -> list[ReaderDocumentRead]:
        with active_database.session() as session:
            records = session.scalars(
                select(AttachmentRecord)
                .join(AttachmentRecord.source)
                .options(
                    selectinload(AttachmentRecord.source),
                    selectinload(AttachmentRecord.notes).selectinload(NoteRecord.highlight),
                )
                .where(AttachmentRecord.detected_format == "pdf")
                .order_by(SourceRecord.title, AttachmentRecord.id)
            )
            return [_reader_document_read(active_database, record) for record in records]

    @application.get(
        "/api/attachments/{attachment_id}/highlights",
        response_model=list[HighlightRead],
    )
    async def list_highlights(attachment_id: int) -> list[HighlightRead]:
        with active_database.session() as session:
            attachment = _require_pdf_attachment(session, attachment_id)
            records = session.scalars(
                select(HighlightRecord)
                .where(HighlightRecord.attachment_id == attachment_id)
                .order_by(HighlightRecord.page_number, HighlightRecord.id)
            )
            return [_highlight_read(record, source_id=attachment.source_id) for record in records]

    @application.post(
        "/api/attachments/{attachment_id}/highlights",
        response_model=HighlightRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_highlight(
        attachment_id: int,
        highlight: HighlightCreate,
    ) -> HighlightRead:
        with active_database.session() as session:
            attachment = _require_pdf_attachment(session, attachment_id)
            record = HighlightRecord(
                attachment=attachment,
                page_number=highlight.page_number,
                selected_text=highlight.selected_text,
                rectangles=[rectangle.model_dump() for rectangle in highlight.rectangles],
            )
            session.add(record)
            try:
                session.commit()
            except Exception as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "code": "highlight_creation_failed",
                        "message": "The highlight could not be saved; no highlight was added.",
                    },
                ) from error
            session.refresh(record)
            return _highlight_read(record, source_id=attachment.source_id)

    @application.delete(
        "/api/highlights/{highlight_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_highlight(highlight_id: int) -> None:
        with active_database.session() as session:
            record = session.get(HighlightRecord, highlight_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Highlight not found")
            session.delete(record)
            try:
                session.commit()
            except Exception as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "code": "highlight_deletion_failed",
                        "message": "The highlight could not be deleted and remains saved.",
                    },
                ) from error

    @application.get(
        "/api/attachments/{attachment_id}/notes",
        response_model=list[ReaderNoteRead],
    )
    async def list_reader_notes(attachment_id: int) -> list[ReaderNoteRead]:
        with active_database.session() as session:
            attachment = _require_pdf_attachment(session, attachment_id)
            availability = _attachment_availability(active_database, attachment)
            records = session.scalars(
                select(NoteRecord)
                .where(NoteRecord.attachment_id == attachment_id)
                .options(
                    selectinload(NoteRecord.source),
                    selectinload(NoteRecord.attachment),
                    selectinload(NoteRecord.highlight),
                )
                .order_by(NoteRecord.page_number, NoteRecord.id)
            )
            return [
                _reader_note_read(
                    active_database,
                    record,
                    attachment_availability=availability,
                )
                for record in records
            ]

    @application.post(
        "/api/attachments/{attachment_id}/notes",
        response_model=ReaderNoteRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_note(attachment_id: int, note: ReaderNoteCreate) -> ReaderNoteRead:
        new_highlight = (
            NewHighlightDraft(
                selected_text=note.new_highlight.selected_text,
                rectangles=tuple(
                    rectangle.model_dump() for rectangle in note.new_highlight.rectangles
                ),
            )
            if note.new_highlight is not None
            else None
        )
        try:
            note_id = create_reader_note(
                active_database,
                attachment_id=attachment_id,
                page_number=note.page_number,
                body=note.body,
                highlight_id=note.highlight_id,
                new_highlight=new_highlight,
            )
        except (
            ReaderNoteAttachmentNotFoundError,
            ReaderNoteDatabaseError,
            ReaderNoteNotPdfError,
            ReaderNoteRelationshipError,
            ReaderNoteValidationError,
        ) as error:
            raise _reader_note_http_error(error) from error
        return _read_reader_note(active_database, note_id)

    @application.put(
        "/api/notes/{note_id}",
        response_model=ReaderNoteRead,
    )
    async def update_note(note_id: int, note: ReaderNoteUpdate) -> ReaderNoteRead:
        try:
            updated_note_id = update_reader_note(
                active_database,
                note_id=note_id,
                body=note.body,
            )
        except (
            ReaderNoteDatabaseError,
            ReaderNoteNotFoundError,
            ReaderNoteRelationshipError,
            ReaderNoteValidationError,
        ) as error:
            raise _reader_note_http_error(error) from error
        return _read_reader_note(active_database, updated_note_id)

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
        identifiers = _clean_identifiers(source.identifiers)

        with active_database.session() as session:
            record = session.scalar(
                select(SourceRecord)
                .where(SourceRecord.id == source_id)
                .options(
                    selectinload(SourceRecord.tags),
                    selectinload(SourceRecord.collections),
                    selectinload(SourceRecord.identifiers),
                    selectinload(SourceRecord.citation_keys),
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
                record.identifiers = _reconcile_identifier_records(record.identifiers, identifiers)
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
        "/api/doi-metadata-previews",
        response_model=ExistingDoiMetadataPreviewRead | ProviderDoiMetadataPreviewRead,
    )
    async def preview_doi_metadata(
        preview: DoiMetadataPreviewCreate,
    ) -> ExistingDoiMetadataPreviewRead | ProviderDoiMetadataPreviewRead:
        normalized_doi = _normalize_doi_for_api(preview.doi)

        with active_database.session() as session:
            existing_source = _find_source_by_doi(session, normalized_doi)
            if existing_source is not None:
                return ExistingDoiMetadataPreviewRead(
                    kind="existing_source",
                    normalized_doi=normalized_doi,
                    existing_source=_existing_doi_source_read(existing_source),
                )

        metadata = await _retrieve_doi_metadata(doi_metadata_provider, normalized_doi)
        return _provider_doi_metadata_preview(normalized_doi, metadata)

    @application.post(
        "/api/sources/from-doi",
        response_model=SourceDetailRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_source_from_doi(creation: DoiSourceCreate) -> SourceDetailRead:
        normalized_doi = _normalize_doi_for_api(creation.doi)
        selected_fields = [field for field in _METADATA_FIELDS if field in creation.fields]
        if "title" not in selected_fields:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "doi_metadata_title_required",
                    "message": "Keep the provider title selected to add this source.",
                },
            )

        with active_database.session() as session:
            existing_source = _find_source_by_doi(session, normalized_doi)
            if existing_source is not None:
                raise _doi_source_exists_error(existing_source)

        metadata = await _retrieve_doi_metadata(doi_metadata_provider, normalized_doi)
        current_preview = _provider_doi_metadata_preview(normalized_doi, metadata)
        if creation.proposal_fingerprint != current_preview.proposal_fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "doi_metadata_changed",
                    "message": (
                        "Crossref metadata changed since this review. Review the updated "
                        "proposal before adding the source."
                    ),
                    "preview": current_preview.model_dump(mode="json"),
                },
            )

        proposal = current_preview.proposal
        if proposal.title is None or not proposal.title.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "doi_metadata_missing_title",
                    "message": "Crossref has no usable title for this DOI, so it cannot be added.",
                },
            )
        unavailable_fields = [
            field for field in selected_fields if field not in current_preview.available_fields
        ]
        if unavailable_fields:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "unavailable_metadata_fields",
                    "message": "The selected DOI metadata is not available from Crossref.",
                    "fields": unavailable_fields,
                },
            )

        record = SourceRecord(
            source_type=SourceType.OTHER.value,
            title=_clean_source_title(proposal.title),
            doi=normalized_doi,
        )
        _apply_metadata_fields(record, proposal, selected_fields)
        record.metadata_lookups.append(
            SourceMetadataLookupRecord(
                provider=current_preview.provider,
                provider_url=current_preview.provider_url,
                identifier_type="doi",
                requested_identifier=normalized_doi,
                retrieved_identifier=current_preview.retrieved_doi,
                reviewed_metadata={},
                proposed_metadata=proposal.model_dump(mode="json"),
                retrieved_at=current_preview.retrieved_at,
                applied_fields=selected_fields,
                applied_at=datetime.now(UTC),
            )
        )

        with active_database.session() as session:
            existing_source = _find_source_by_doi(session, normalized_doi)
            if existing_source is not None:
                raise _doi_source_exists_error(existing_source)
            session.add(record)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                existing_source = _find_source_by_doi(session, normalized_doi)
                if existing_source is not None:
                    raise _doi_source_exists_error(existing_source) from error
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "doi_source_creation_conflict",
                        "message": (
                            "The source conflicted with another library change; nothing was saved."
                        ),
                    },
                ) from error
            except Exception as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "code": "doi_source_creation_failed",
                        "message": (
                            "The source could not be saved; no source or provenance was added."
                        ),
                    },
                ) from error
            source_id = record.id

        return _read_source_detail(active_database, source_id)

    @application.post(
        "/api/isbn-metadata-previews",
        response_model=ExistingIsbnMetadataPreviewRead | ProviderIsbnMetadataPreviewRead,
    )
    async def preview_isbn_metadata(
        preview: IsbnMetadataPreviewCreate,
    ) -> ExistingIsbnMetadataPreviewRead | ProviderIsbnMetadataPreviewRead:
        identity = _isbn_identity_for_api(preview.isbn)

        with active_database.session() as session:
            existing_sources = _find_sources_by_isbn(session, identity.canonical_isbn13)
            if existing_sources and not preview.lookup_if_local_match:
                return ExistingIsbnMetadataPreviewRead(
                    kind="existing_sources",
                    input_isbn=preview.isbn,
                    normalized_isbn=identity.normalized_isbn,
                    canonical_isbn13=identity.canonical_isbn13,
                    existing_sources=[
                        _existing_isbn_source_read(record) for record in existing_sources
                    ],
                )

        metadata = await _retrieve_isbn_metadata(
            isbn_metadata_provider,
            identity.canonical_isbn13,
        )
        return _provider_isbn_metadata_preview(preview.isbn, identity, metadata)

    @application.post(
        "/api/sources/from-isbn",
        response_model=SourceDetailRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_source_from_isbn(creation: IsbnSourceCreate) -> SourceDetailRead:
        identity = _isbn_identity_for_api(creation.isbn)
        selected_fields = [field for field in _METADATA_FIELDS if field in creation.fields]
        if "title" not in selected_fields:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "isbn_metadata_title_required",
                    "message": "Keep the catalog title selected to add this book.",
                },
            )

        metadata = await _retrieve_isbn_metadata(
            isbn_metadata_provider,
            identity.canonical_isbn13,
        )
        current_preview = _provider_isbn_metadata_preview(creation.isbn, identity, metadata)
        if creation.proposal_fingerprint != current_preview.proposal_fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "isbn_metadata_changed",
                    "message": (
                        "Open Library metadata changed since this review. Review the updated "
                        "proposal before adding the book."
                    ),
                    "preview": current_preview.model_dump(mode="json"),
                },
            )

        proposal = current_preview.proposal
        if proposal.title is None or not proposal.title.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "isbn_metadata_missing_title",
                    "message": (
                        "Open Library has no usable title for this edition, so it cannot be added."
                    ),
                },
            )
        unavailable_fields = [
            field for field in selected_fields if field not in current_preview.available_fields
        ]
        if unavailable_fields:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "unavailable_metadata_fields",
                    "message": "The selected book metadata is not available from Open Library.",
                    "fields": unavailable_fields,
                },
            )

        required_isbn = _clean_identifiers(
            [
                SourceIdentifierRead(
                    identifier_type="isbn",
                    value=creation.isbn.strip(),
                )
            ]
        )
        record = SourceRecord(
            source_type=SourceType.BOOK.value,
            title=_clean_source_title(proposal.title),
            identifiers=required_isbn,
        )
        _apply_metadata_fields(record, proposal, selected_fields)
        record.metadata_lookups.append(
            SourceMetadataLookupRecord(
                provider=current_preview.provider,
                provider_url=current_preview.provider_url,
                identifier_type="isbn",
                requested_identifier=identity.canonical_isbn13,
                retrieved_identifier=current_preview.retrieved_isbn,
                reviewed_metadata={},
                proposed_metadata=proposal.model_dump(mode="json"),
                retrieved_at=current_preview.retrieved_at,
                applied_fields=selected_fields,
                applied_at=datetime.now(UTC),
            )
        )

        with active_database.session() as session:
            session.add(record)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "isbn_source_creation_conflict",
                        "message": (
                            "The book conflicted with another library change; nothing was saved."
                        ),
                    },
                ) from error
            except Exception as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "code": "isbn_source_creation_failed",
                        "message": (
                            "The book could not be saved; no source or provenance was added."
                        ),
                    },
                ) from error
            source_id = record.id

        return _read_source_detail(active_database, source_id)

    @application.post(
        "/api/sources/{source_id}/doi-metadata-lookups",
        response_model=MetadataLookupRead,
    )
    async def lookup_source_doi_metadata(source_id: int) -> MetadataLookupRead:
        with active_database.session() as session:
            record = session.get(SourceRecord, source_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Source not found")
            if record.doi is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "missing_doi",
                        "message": "Add a DOI to this source before looking up metadata.",
                    },
                )
            requested_doi = _normalize_doi_for_api(record.doi)

        metadata = await _retrieve_doi_metadata(doi_metadata_provider, requested_doi)

        proposal = _metadata_proposal(metadata)
        with active_database.session() as session:
            record = session.scalar(
                select(SourceRecord)
                .where(SourceRecord.id == source_id)
                .options(selectinload(SourceRecord.identifiers))
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Source not found")
            if record.doi is None or doi_key(record.doi) != doi_key(requested_doi):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "source_doi_changed",
                        "message": (
                            "The source DOI changed during lookup. Review the source and try again."
                        ),
                    },
                )
            reviewed_metadata = _source_metadata_snapshot(record)
            lookup = SourceMetadataLookupRecord(
                source=record,
                provider=metadata.provider,
                provider_url=metadata.provider_url,
                identifier_type="doi",
                requested_identifier=normalize_imported_doi(requested_doi),
                retrieved_identifier=metadata.retrieved_identifier,
                reviewed_metadata=reviewed_metadata,
                proposed_metadata=proposal.model_dump(mode="json"),
            )
            session.add(lookup)
            session.commit()
            session.refresh(lookup)
            return _metadata_lookup_read(lookup, reviewed_metadata, proposal)

    @application.post(
        "/api/sources/{source_id}/doi-metadata-lookups/{lookup_id}/apply",
        response_model=SourceDetailRead,
    )
    async def apply_source_doi_metadata(
        source_id: int,
        lookup_id: int,
        selection: MetadataApply,
    ) -> SourceDetailRead:
        selected_fields = [field for field in _METADATA_FIELDS if field in selection.fields]
        if not selected_fields:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "no_metadata_fields_selected",
                    "message": "Choose at least one metadata field to apply.",
                },
            )

        with active_database.session() as session:
            record = session.scalar(
                select(SourceRecord)
                .where(SourceRecord.id == source_id)
                .options(selectinload(SourceRecord.identifiers))
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Source not found")
            lookup = session.scalar(
                select(SourceMetadataLookupRecord).where(
                    SourceMetadataLookupRecord.id == lookup_id,
                    SourceMetadataLookupRecord.source_id == source_id,
                )
            )
            if lookup is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "doi_metadata_lookup_not_found",
                        "message": "This DOI metadata review no longer exists.",
                    },
                )
            if lookup.applied_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "doi_metadata_already_applied",
                        "message": "This DOI metadata review has already been applied.",
                    },
                )
            if (
                record.doi is None
                or lookup.identifier_type != "doi"
                or doi_key(record.doi) != doi_key(lookup.requested_identifier)
                or doi_key(record.doi) != doi_key(lookup.retrieved_identifier)
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "source_doi_changed",
                        "message": (
                            "The source DOI changed after this review. Look up the DOI again "
                            "before applying metadata."
                        ),
                    },
                )

            proposal = _stored_metadata_proposal(lookup.proposed_metadata)
            available_fields = _available_metadata_fields(proposal)
            unavailable_fields = [
                field for field in selected_fields if field not in available_fields
            ]
            if unavailable_fields:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "unavailable_metadata_fields",
                        "message": "The selected DOI metadata is not available from Crossref.",
                        "fields": unavailable_fields,
                    },
                )

            current_metadata = _source_metadata_snapshot(record)
            changed_fields = [
                field
                for field in selected_fields
                if current_metadata.get(field) != lookup.reviewed_metadata.get(field)
            ]
            if changed_fields:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "source_metadata_changed",
                        "message": (
                            "The source changed after this review. Look up the DOI again before "
                            "applying metadata."
                        ),
                        "fields": changed_fields,
                    },
                )

            _apply_metadata_fields(record, proposal, selected_fields)
            lookup.applied_fields = selected_fields
            lookup.applied_at = datetime.now(UTC)
            session.commit()

        return _read_source_detail(active_database, source_id)

    @application.post(
        "/api/sources/{source_id}/isbn-metadata-lookups",
        response_model=MetadataLookupRead,
    )
    async def lookup_source_isbn_metadata(
        source_id: int,
        selection: IsbnMetadataLookupCreate,
    ) -> MetadataLookupRead:
        identity = _isbn_identity_for_api(selection.isbn)
        with active_database.session() as session:
            record = session.scalar(
                select(SourceRecord)
                .where(SourceRecord.id == source_id)
                .options(selectinload(SourceRecord.identifiers))
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Source not found")
            _require_saved_source_isbn(record, identity.canonical_isbn13)

        metadata = await _retrieve_isbn_metadata(
            isbn_metadata_provider,
            identity.canonical_isbn13,
        )
        proposal = _metadata_proposal(metadata)

        with active_database.session() as session:
            record = session.scalar(
                select(SourceRecord)
                .where(SourceRecord.id == source_id)
                .options(selectinload(SourceRecord.identifiers))
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Source not found")
            _require_saved_source_isbn(
                record,
                identity.canonical_isbn13,
                changed_during_lookup=True,
            )
            reviewed_metadata = _source_metadata_snapshot(record)
            lookup = SourceMetadataLookupRecord(
                source=record,
                provider=metadata.provider,
                provider_url=metadata.provider_url,
                identifier_type="isbn",
                requested_identifier=identity.canonical_isbn13,
                retrieved_identifier=isbn_identity(metadata.retrieved_identifier).canonical_isbn13,
                reviewed_metadata=reviewed_metadata,
                proposed_metadata=proposal.model_dump(mode="json"),
            )
            session.add(lookup)
            try:
                session.commit()
            except Exception as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "code": "isbn_metadata_lookup_save_failed",
                        "message": (
                            "The catalog review could not be saved; the source was not changed."
                        ),
                    },
                ) from error
            session.refresh(lookup)
            return _metadata_lookup_read(lookup, reviewed_metadata, proposal)

    @application.post(
        "/api/sources/{source_id}/isbn-metadata-lookups/{lookup_id}/apply",
        response_model=SourceDetailRead,
    )
    async def apply_source_isbn_metadata(
        source_id: int,
        lookup_id: int,
        selection: MetadataApply,
    ) -> SourceDetailRead:
        selected_fields = [field for field in _METADATA_FIELDS if field in selection.fields]
        if not selected_fields:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "no_metadata_fields_selected",
                    "message": "Choose at least one metadata field to apply.",
                },
            )

        with active_database.session() as session:
            record, lookup = _isbn_lookup_records(session, source_id, lookup_id)
            canonical_isbn13 = _validate_saved_isbn_lookup(record, lookup)
            stored_proposal = _stored_metadata_proposal(lookup.proposed_metadata)
            stored_fingerprint = _metadata_proposal_fingerprint(
                identifier_type="isbn",
                requested_identifier=canonical_isbn13,
                retrieved_identifier=canonical_isbn13,
                provider=lookup.provider,
                provider_url=lookup.provider_url,
                proposal=stored_proposal,
            )

        metadata = await _retrieve_isbn_metadata(isbn_metadata_provider, canonical_isbn13)
        fresh_proposal = _metadata_proposal(metadata)
        fresh_retrieved_isbn = isbn_identity(metadata.retrieved_identifier).canonical_isbn13
        fresh_fingerprint = _metadata_proposal_fingerprint(
            identifier_type="isbn",
            requested_identifier=canonical_isbn13,
            retrieved_identifier=fresh_retrieved_isbn,
            provider=metadata.provider,
            provider_url=metadata.provider_url,
            proposal=fresh_proposal,
        )

        with active_database.session() as session:
            record, lookup = _isbn_lookup_records(session, source_id, lookup_id)
            _validate_saved_isbn_lookup(record, lookup, canonical_isbn13=canonical_isbn13)
            current_metadata = _source_metadata_snapshot(record)
            changed_fields = [
                field
                for field in selected_fields
                if current_metadata.get(field) != lookup.reviewed_metadata.get(field)
            ]
            if changed_fields:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "source_metadata_changed",
                        "message": (
                            "The source changed after this review. Look up the ISBN again before "
                            "applying metadata."
                        ),
                        "fields": changed_fields,
                    },
                )

            if fresh_fingerprint != stored_fingerprint:
                refreshed_lookup = SourceMetadataLookupRecord(
                    source=record,
                    provider=metadata.provider,
                    provider_url=metadata.provider_url,
                    identifier_type="isbn",
                    requested_identifier=canonical_isbn13,
                    retrieved_identifier=fresh_retrieved_isbn,
                    reviewed_metadata=current_metadata,
                    proposed_metadata=fresh_proposal.model_dump(mode="json"),
                )
                session.add(refreshed_lookup)
                try:
                    session.commit()
                except Exception as error:
                    session.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail={
                            "code": "isbn_metadata_lookup_save_failed",
                            "message": (
                                "The updated catalog review could not be saved; the source was "
                                "not changed."
                            ),
                        },
                    ) from error
                session.refresh(refreshed_lookup)
                refreshed_review = _metadata_lookup_read(
                    refreshed_lookup,
                    current_metadata,
                    fresh_proposal,
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "isbn_metadata_changed",
                        "message": (
                            "Open Library metadata changed since this review. Review the updated "
                            "proposal before applying it."
                        ),
                        "lookup": refreshed_review.model_dump(mode="json"),
                    },
                )

            available_fields = _available_metadata_fields(fresh_proposal)
            unavailable_fields = [
                field for field in selected_fields if field not in available_fields
            ]
            if unavailable_fields:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "unavailable_metadata_fields",
                        "message": (
                            "The selected book metadata is not available from Open Library."
                        ),
                        "fields": unavailable_fields,
                    },
                )

            _apply_metadata_fields(record, fresh_proposal, selected_fields)
            lookup.applied_fields = selected_fields
            lookup.applied_at = datetime.now(UTC)
            try:
                session.commit()
            except Exception as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "code": "isbn_metadata_apply_failed",
                        "message": (
                            "The catalog metadata could not be applied; the source was not changed."
                        ),
                    },
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

    @application.delete(
        "/api/sources/{source_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_source(source_id: int) -> None:
        try:
            remove_source(active_database, source_id)
        except SourceNotFoundError as error:
            raise HTTPException(status_code=404, detail="Source not found") from error
        except SourceRemovalDatabaseError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "source_database_removal_failed",
                    "message": "The source could not be removed; its saved files were restored.",
                },
            ) from error
        except ManagedFileConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "source_managed_file_conflict",
                    "message": "The source has saved file paths that are unsafe to remove.",
                },
            ) from error
        except ManagedFileRecoveryError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "source_removal_recovery_failed",
                    "message": "Removal stopped, but the saved files could not all be restored.",
                },
            ) from error
        except ManagedFileCleanupError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "source_cleanup_incomplete",
                    "message": "The source was removed, but temporary file cleanup did not finish.",
                },
            ) from error
        except OSError as error:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "source_removal_failed",
                    "message": "The source could not be removed; its saved files were restored.",
                },
            ) from error

    @application.get("/api/bibliography-exports/{bibliography_format}")
    async def export_bibliography(bibliography_format: str) -> Response:
        try:
            export_format = BibliographyFormat(bibliography_format)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail={
                    "code": "unsupported_bibliography_export",
                    "message": "Choose BibTeX, RIS, or CSL JSON for the export.",
                },
            ) from error

        with active_database.session() as session:
            records = list(
                session.scalars(
                    select(SourceRecord).options(
                        selectinload(SourceRecord.identifiers),
                        selectinload(SourceRecord.citation_keys),
                    )
                )
            )
            sources = [_bibliography_export_source(record) for record in records]

        try:
            content = serialize_bibliography(sources, export_format)
        except EmptyBibliographyExportError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "empty_library", "message": str(error)},
            ) from error
        except BibliographySerializationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "bibliography_export_failed",
                    "message": str(error),
                },
            ) from error

        filename, media_type = _BIBLIOGRAPHY_EXPORT_RESPONSES[export_format]
        return Response(
            content=content.encode("utf-8"),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @application.post(
        "/api/bibliography-imports",
        response_model=BibliographyImportRead,
    )
    async def import_bibliography(
        bibliography: Annotated[UploadFile, File()],
    ) -> BibliographyImportRead:
        filename = _clean_bibliography_filename(bibliography.filename)
        if bibliography.size is not None and bibliography.size > MAX_BIBLIOGRAPHY_BYTES:
            raise _oversized_bibliography_error()

        content = await bibliography.read(MAX_BIBLIOGRAPHY_BYTES + 1)
        if len(content) > MAX_BIBLIOGRAPHY_BYTES:
            raise _oversized_bibliography_error()

        try:
            parsed = parse_bibliography(content, filename)
        except UnsupportedBibliographyFormatError as error:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail={"code": "unsupported_bibliography", "message": str(error)},
            ) from error
        except EmptyBibliographyError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "empty_bibliography", "message": str(error)},
            ) from error
        except BibliographyEntryLimitError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "bibliography_entry_limit", "message": str(error)},
            ) from error
        except MalformedBibliographyError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "malformed_bibliography", "message": str(error)},
            ) from error

        records = [
            _bibliography_source_record(source, parsed.bibliography_format)
            for source in parsed.sources
        ]
        skipped: list[BibliographyImportSkippedRead] = []
        imported_records: list[SourceRecord] = []
        with active_database.session() as session:
            existing_dois = {
                doi_key(doi)
                for doi in session.scalars(
                    select(SourceRecord.doi).where(SourceRecord.doi.is_not(None))
                )
                if doi is not None
            }
            seen_dois: set[str] = set()
            for source, record in zip(parsed.sources, records, strict=True):
                normalized_doi = doi_key(record.doi) if record.doi is not None else None
                if normalized_doi is not None and (
                    normalized_doi in existing_dois or normalized_doi in seen_dois
                ):
                    skipped.append(
                        BibliographyImportSkippedRead(
                            entry_id=source.entry_id,
                            title=record.title,
                            doi=record.doi,
                            reason=(
                                "existing_doi"
                                if normalized_doi in existing_dois
                                else "duplicate_doi_in_file"
                            ),
                        )
                    )
                    continue
                if normalized_doi is not None:
                    seen_dois.add(normalized_doi)
                session.add(record)
                imported_records.append(record)

            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "bibliography_conflict",
                        "message": "The bibliography changed during import; no sources were saved.",
                    },
                ) from error

            imported = []
            for record in imported_records:
                session.refresh(record)
                imported.append(_source_read(record))

        return BibliographyImportRead(
            bibliography_format=parsed.bibliography_format,
            total_entries=len(parsed.sources),
            imported=imported,
            skipped=skipped,
        )

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
        "/api/attachments/{attachment_id}/content",
        response_class=FileResponse,
    )
    def get_pdf_content(attachment_id: int) -> FileResponse:
        with active_database.session() as session:
            record = session.get(AttachmentRecord, attachment_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Attachment not found")
            if record.detected_format != "pdf":
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail={
                        "code": "not_pdf",
                        "message": "Only PDF attachments can be opened in Reader.",
                    },
                )
            checksum = record.checksum
            managed_path = record.managed_path
            filename = record.original_filename

        if active_database.library_paths is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "library_storage_unavailable",
                    "message": "The managed library storage is not available.",
                },
            )

        try:
            attachment_path = ManagedAttachmentStore(
                active_database.library_paths
            ).verified_file_for(checksum, managed_path)
        except ManagedFileConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "managed_file_conflict",
                    "message": "The saved PDF is missing or has changed.",
                },
            ) from error

        return FileResponse(
            attachment_path,
            media_type="application/pdf",
            filename=filename,
            content_disposition_type="inline",
            headers={"Cache-Control": "no-store"},
        )

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
        except AttachmentRemovalBlockedByHighlightsError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "attachment_has_highlights",
                    "message": "Remove the saved highlights before removing this attachment.",
                },
            ) from error
        except AttachmentRemovalBlockedByNotesError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "attachment_has_reader_notes",
                    "message": str(error),
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


def _normalize_doi_for_api(doi: str) -> str:
    try:
        return normalize_doi_for_lookup(doi)
    except InvalidDoiError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_doi", "message": str(error)},
        ) from error


def _find_source_by_doi(session: Session, doi: str) -> SourceRecord | None:
    requested_key = doi_key(doi)
    return next(
        (
            record
            for record in session.scalars(
                select(SourceRecord).where(SourceRecord.doi.is_not(None)).order_by(SourceRecord.id)
            )
            if record.doi is not None and doi_key(record.doi) == requested_key
        ),
        None,
    )


def _existing_doi_source_read(record: SourceRecord) -> ExistingDoiSourceRead:
    assert record.doi is not None
    return ExistingDoiSourceRead(
        id=record.id,
        source_type=SourceType(record.source_type),
        title=record.title,
        doi=record.doi,
    )


def _doi_source_exists_error(record: SourceRecord) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "doi_already_exists",
            "message": "A source with this DOI already exists.",
            "existing_source": _existing_doi_source_read(record).model_dump(mode="json"),
        },
    )


def _isbn_identity_for_api(isbn: str) -> IsbnIdentity:
    try:
        return isbn_identity(isbn)
    except EmptyIsbnError as error:
        raise _isbn_validation_http_error("empty_isbn", error) from error
    except MalformedIsbnError as error:
        raise _isbn_validation_http_error("malformed_isbn", error) from error
    except UnsupportedIsbnPrefixError as error:
        raise _isbn_validation_http_error("unsupported_isbn_prefix", error) from error
    except IsbnChecksumError as error:
        raise _isbn_validation_http_error("isbn_checksum", error) from error


def _isbn_validation_http_error(code: str, error: IsbnValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": str(error)},
    )


def _find_sources_by_isbn(session: Session, canonical_isbn13: str) -> list[SourceRecord]:
    matches: list[SourceRecord] = []
    records = session.scalars(
        select(SourceRecord)
        .join(SourceRecord.identifiers)
        .where(SourceIdentifierRecord.identifier_type == "isbn")
        .options(selectinload(SourceRecord.identifiers))
        .order_by(SourceRecord.id)
    ).unique()
    for record in records:
        for identifier in record.identifiers:
            if identifier.identifier_type != "isbn":
                continue
            try:
                existing_identity = isbn_identity(identifier.value)
            except IsbnValidationError:
                continue
            if existing_identity.canonical_isbn13 == canonical_isbn13:
                matches.append(record)
                break
    return matches


def _existing_isbn_source_read(record: SourceRecord) -> ExistingIsbnSourceRead:
    return ExistingIsbnSourceRead(
        id=record.id,
        source_type=SourceType(record.source_type),
        title=record.title,
        isbn_values=[
            identifier.value
            for identifier in record.identifiers
            if identifier.identifier_type == "isbn"
        ],
    )


def _valid_source_isbn_identities(record: SourceRecord) -> list[IsbnIdentity]:
    identities: list[IsbnIdentity] = []
    for identifier in record.identifiers:
        if identifier.identifier_type != "isbn":
            continue
        try:
            identities.append(isbn_identity(identifier.value))
        except IsbnValidationError:
            continue
    return identities


def _require_saved_source_isbn(
    record: SourceRecord,
    canonical_isbn13: str,
    *,
    changed_during_lookup: bool = False,
) -> None:
    identities = _valid_source_isbn_identities(record)
    if not identities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "missing_isbn",
                "message": "Add and save a valid ISBN before looking up catalog metadata.",
            },
        )
    if any(identity.canonical_isbn13 == canonical_isbn13 for identity in identities):
        return
    if changed_during_lookup:
        detail = {
            "code": "source_isbn_changed",
            "message": (
                "The selected ISBN changed during lookup. Review the source and try again."
            ),
        }
    else:
        detail = {
            "code": "isbn_not_saved",
            "message": "Choose an ISBN that is currently saved on this source.",
        }
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _isbn_lookup_records(
    session: Session,
    source_id: int,
    lookup_id: int,
) -> tuple[SourceRecord, SourceMetadataLookupRecord]:
    record = session.scalar(
        select(SourceRecord)
        .where(SourceRecord.id == source_id)
        .options(selectinload(SourceRecord.identifiers))
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Source not found")
    lookup = session.scalar(
        select(SourceMetadataLookupRecord).where(
            SourceMetadataLookupRecord.id == lookup_id,
            SourceMetadataLookupRecord.source_id == source_id,
            SourceMetadataLookupRecord.identifier_type == "isbn",
        )
    )
    if lookup is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "isbn_metadata_lookup_not_found",
                "message": "This ISBN metadata review no longer exists.",
            },
        )
    if lookup.applied_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "isbn_metadata_already_applied",
                "message": "This ISBN metadata review has already been applied.",
            },
        )
    return record, lookup


def _validate_saved_isbn_lookup(
    record: SourceRecord,
    lookup: SourceMetadataLookupRecord,
    *,
    canonical_isbn13: str | None = None,
) -> str:
    try:
        requested = isbn_identity(lookup.requested_identifier).canonical_isbn13
        retrieved = isbn_identity(lookup.retrieved_identifier).canonical_isbn13
    except IsbnValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "invalid_metadata_provenance",
                "message": "The saved ISBN metadata review is invalid; nothing was changed.",
            },
        ) from error
    if requested != retrieved or (canonical_isbn13 is not None and requested != canonical_isbn13):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "invalid_metadata_provenance",
                "message": "The saved ISBN metadata review is invalid; nothing was changed.",
            },
        )
    if not any(
        identity.canonical_isbn13 == requested for identity in _valid_source_isbn_identities(record)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "source_isbn_changed",
                "message": (
                    "The source ISBN changed after this review. Look up the ISBN again before "
                    "applying metadata."
                ),
            },
        )
    return requested


async def _retrieve_doi_metadata(
    provider: Callable[[str], RetrievedMetadata],
    doi: str,
) -> RetrievedMetadata:
    try:
        return await run_in_threadpool(provider, doi)
    except InvalidDoiError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_doi", "message": str(error)},
        ) from error
    except DoiMetadataNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "doi_metadata_not_found", "message": str(error)},
        ) from error
    except DoiMetadataRateLimitedError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "doi_metadata_rate_limited", "message": str(error)},
        ) from error
    except DoiMetadataUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "doi_metadata_unavailable", "message": str(error)},
        ) from error
    except (DoiMetadataMalformedError, DoiMetadataMismatchError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "invalid_doi_metadata", "message": str(error)},
        ) from error


async def _retrieve_isbn_metadata(
    provider: Callable[[str], RetrievedMetadata],
    canonical_isbn13: str,
) -> RetrievedMetadata:
    try:
        metadata = await run_in_threadpool(provider, canonical_isbn13)
        if (
            metadata.identifier_type != "isbn"
            or isbn_identity(metadata.retrieved_identifier).canonical_isbn13 != canonical_isbn13
        ):
            raise OpenLibraryMetadataMismatchError(
                "Open Library returned catalog metadata for a different ISBN."
            )
        return metadata
    except OpenLibraryMetadataNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "isbn_metadata_not_found", "message": str(error)},
        ) from error
    except OpenLibraryMetadataAmbiguousError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "isbn_metadata_ambiguous", "message": str(error)},
        ) from error
    except OpenLibraryMetadataRateLimitedError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "isbn_metadata_rate_limited", "message": str(error)},
        ) from error
    except OpenLibraryMetadataUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "isbn_metadata_unavailable", "message": str(error)},
        ) from error
    except (
        IsbnValidationError,
        OpenLibraryMetadataMalformedError,
        OpenLibraryMetadataMismatchError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "invalid_isbn_metadata", "message": str(error)},
        ) from error


def _metadata_proposal(metadata: RetrievedMetadata) -> MetadataProposalRead:
    proposal = metadata.proposal
    return MetadataProposalRead(
        source_type=proposal.source_type,
        title=proposal.title,
        authors=proposal.authors,
        publication_year=proposal.publication_year,
        venue=proposal.venue,
        url=proposal.url,
        abstract=proposal.abstract,
        language=proposal.language,
        identifiers=(
            [
                SourceIdentifierRead(
                    identifier_type=identifier.identifier_type,
                    value=identifier.value,
                )
                for identifier in proposal.identifiers
            ]
            if proposal.identifiers is not None
            else None
        ),
    )


def _provider_doi_metadata_preview(
    requested_doi: str,
    metadata: RetrievedMetadata,
) -> ProviderDoiMetadataPreviewRead:
    proposal = _metadata_proposal(metadata)
    return ProviderDoiMetadataPreviewRead(
        kind="proposal",
        normalized_doi=requested_doi,
        provider=metadata.provider,
        provider_url=metadata.provider_url,
        retrieved_doi=metadata.retrieved_identifier,
        retrieved_at=datetime.now(UTC),
        proposal_fingerprint=_metadata_proposal_fingerprint(
            identifier_type="doi",
            requested_identifier=doi_key(requested_doi),
            retrieved_identifier=doi_key(metadata.retrieved_identifier),
            provider=metadata.provider,
            provider_url=metadata.provider_url,
            proposal=proposal,
        ),
        proposal=proposal,
        available_fields=_available_metadata_fields(proposal),
    )


def _provider_isbn_metadata_preview(
    input_isbn: str,
    identity: IsbnIdentity,
    metadata: RetrievedMetadata,
) -> ProviderIsbnMetadataPreviewRead:
    proposal = _metadata_proposal(metadata)
    retrieved_isbn = isbn_identity(metadata.retrieved_identifier).canonical_isbn13
    return ProviderIsbnMetadataPreviewRead(
        kind="proposal",
        input_isbn=input_isbn,
        normalized_isbn=identity.normalized_isbn,
        canonical_isbn13=identity.canonical_isbn13,
        provider=metadata.provider,
        provider_url=metadata.provider_url,
        retrieved_isbn=retrieved_isbn,
        retrieved_at=datetime.now(UTC),
        proposal_fingerprint=_metadata_proposal_fingerprint(
            identifier_type="isbn",
            requested_identifier=identity.canonical_isbn13,
            retrieved_identifier=retrieved_isbn,
            provider=metadata.provider,
            provider_url=metadata.provider_url,
            proposal=proposal,
        ),
        proposal=proposal,
        available_fields=_available_metadata_fields(proposal),
    )


def _metadata_proposal_fingerprint(
    *,
    identifier_type: Literal["doi", "isbn"],
    requested_identifier: str,
    retrieved_identifier: str,
    provider: str,
    provider_url: str,
    proposal: MetadataProposalRead,
) -> str:
    payload = {
        "provider": provider,
        "provider_url": provider_url,
        "identifier_type": identifier_type,
        "requested_identifier": requested_identifier,
        "retrieved_identifier": retrieved_identifier,
        "proposal": proposal.model_dump(mode="json"),
    }
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical_payload).hexdigest()


def _source_metadata_snapshot(record: SourceRecord) -> dict[str, object]:
    return {
        "source_type": record.source_type,
        "title": record.title,
        "authors": list(record.authors),
        "publication_year": record.publication_year,
        "venue": record.venue,
        "url": record.url,
        "abstract": record.abstract,
        "language": record.language,
        "identifiers": [
            {
                "identifier_type": identifier.identifier_type,
                "value": identifier.value,
            }
            for identifier in sorted(
                record.identifiers,
                key=lambda identifier: (
                    identifier.identifier_type,
                    identifier.normalized_value,
                ),
            )
        ],
    }


def _stored_metadata_proposal(payload: dict[str, object]) -> MetadataProposalRead:
    try:
        return MetadataProposalRead.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "invalid_metadata_provenance",
                "message": "The saved metadata review is invalid; nothing was changed.",
            },
        ) from error


def _available_metadata_fields(
    proposal: MetadataProposalRead,
) -> list[MetadataField]:
    values = proposal.model_dump(mode="json")
    return [field for field in _METADATA_FIELDS if values[field] is not None]


def _metadata_lookup_read(
    lookup: SourceMetadataLookupRecord,
    reviewed_metadata: dict[str, object],
    proposal: MetadataProposalRead,
) -> MetadataLookupRead:
    proposed_metadata = proposal.model_dump(mode="json")
    available_fields = _available_metadata_fields(proposal)
    conflicting_fields = [
        field
        for field in available_fields
        if field != "identifiers"
        and _metadata_value_present(reviewed_metadata.get(field))
        and reviewed_metadata.get(field) != proposed_metadata.get(field)
    ]
    return MetadataLookupRead(
        id=lookup.id,
        provider=lookup.provider,
        provider_url=lookup.provider_url,
        identifier_type=lookup.identifier_type,
        requested_identifier=lookup.requested_identifier,
        retrieved_identifier=lookup.retrieved_identifier,
        retrieved_at=lookup.retrieved_at,
        proposal=proposal,
        available_fields=available_fields,
        conflicting_fields=conflicting_fields,
    )


def _metadata_value_present(value: object) -> bool:
    return value is not None and value != "" and value != []


def _apply_metadata_fields(
    record: SourceRecord,
    proposal: MetadataProposalRead,
    fields: list[MetadataField],
) -> None:
    if "source_type" in fields:
        assert proposal.source_type is not None
        record.source_type = proposal.source_type.value
    if "title" in fields:
        assert proposal.title is not None
        record.title = _clean_source_title(proposal.title)
    if "authors" in fields:
        assert proposal.authors is not None
        record.authors = _clean_authors(proposal.authors)
    if "publication_year" in fields:
        assert proposal.publication_year is not None
        record.publication_year = _clean_publication_year(proposal.publication_year)
    if "venue" in fields:
        assert proposal.venue is not None
        record.venue = _clean_optional_text(proposal.venue, label="Venues", maximum_length=500)
    if "url" in fields:
        assert proposal.url is not None
        record.url = _clean_url(proposal.url)
    if "abstract" in fields:
        assert proposal.abstract is not None
        record.abstract = _clean_optional_text(
            proposal.abstract,
            label="Abstracts",
            maximum_length=100_000,
        )
    if "language" in fields:
        assert proposal.language is not None
        record.language = _clean_optional_text(
            proposal.language,
            label="Languages",
            maximum_length=35,
        )
    if "identifiers" in fields:
        assert proposal.identifiers is not None
        cleaned_identifiers = _clean_identifiers(
            [
                SourceIdentifierRead(
                    identifier_type=identifier.identifier_type,
                    value=identifier.value,
                )
                for identifier in record.identifiers
            ]
            + proposal.identifiers
        )
        record.identifiers = _reconcile_identifier_records(
            record.identifiers,
            cleaned_identifiers,
        )


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


def _clean_identifiers(
    identifiers: list[SourceIdentifierRead],
) -> list[SourceIdentifierRecord]:
    if len(identifiers) > 50:
        raise HTTPException(status_code=422, detail="Sources are limited to 50 identifiers")

    cleaned: dict[tuple[str, str], str] = {}
    for identifier in identifiers:
        identifier_type = identifier.identifier_type.strip().casefold()
        if not identifier_type:
            raise HTTPException(status_code=422, detail="Identifier types cannot be empty")
        if identifier_type == "doi":
            raise HTTPException(
                status_code=422,
                detail="Use the DOI field for DOI identifiers",
            )
        if len(identifier_type) > 50:
            raise HTTPException(
                status_code=422,
                detail="Identifier types are limited to 50 characters",
            )
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", identifier_type) is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Identifier types may contain letters, numbers, periods, hyphens, "
                    "and underscores"
                ),
            )

        value = identifier.value.strip()
        if not value:
            raise HTTPException(status_code=422, detail="Identifier values cannot be empty")
        if len(value) > 500:
            raise HTTPException(
                status_code=422,
                detail="Identifier values are limited to 500 characters",
            )
        normalized_value = value.casefold()
        if identifier_type == "isbn":
            try:
                normalized_value = isbn_identity(value).canonical_isbn13
            except IsbnValidationError:
                normalized_value = value.casefold()
        cleaned.setdefault((identifier_type, normalized_value), value)

    return [
        SourceIdentifierRecord(
            identifier_type=identifier_type,
            value=value,
            normalized_value=normalized_value,
        )
        for (identifier_type, normalized_value), value in cleaned.items()
    ]


def _reconcile_identifier_records(
    existing: list[SourceIdentifierRecord],
    cleaned: list[SourceIdentifierRecord],
) -> list[SourceIdentifierRecord]:
    existing_by_key = {
        (identifier.identifier_type, identifier.normalized_value): identifier
        for identifier in existing
    }
    return [
        existing_by_key.get(
            (identifier.identifier_type, identifier.normalized_value),
            identifier,
        )
        for identifier in cleaned
    ]


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


def _clean_bibliography_filename(filename: str | None) -> str:
    clean_filename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not clean_filename:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "missing_bibliography_filename",
                "message": "The bibliography file must have a filename.",
            },
        )
    if len(clean_filename) > 1024:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "bibliography_filename_too_long",
                "message": "The bibliography filename is too long.",
            },
        )
    return clean_filename


def _bibliography_source_record(
    source: BibliographySourceDraft,
    bibliography_format: BibliographyFormat,
) -> SourceRecord:
    try:
        title = _clean_source_title(source.title)
        authors = _clean_authors(source.authors)
        publication_year = _clean_publication_year(source.publication_year)
        venue = _clean_optional_text(source.venue, label="Venues", maximum_length=500)
        doi = _clean_optional_text(
            normalize_imported_doi(source.doi) if source.doi is not None else None,
            label="DOIs",
            maximum_length=255,
        )
        url = _clean_url(source.url)
        abstract = _clean_optional_text(
            source.abstract,
            label="Abstracts",
            maximum_length=100_000,
        )
        language = _clean_optional_text(source.language, label="Languages", maximum_length=35)
        identifiers = _clean_identifiers(
            [
                SourceIdentifierRead(
                    identifier_type=identifier.identifier_type,
                    value=identifier.value,
                )
                for identifier in source.identifiers
            ]
        )
        citation_key = _clean_optional_text(
            source.citation_key,
            label="Citation keys",
            maximum_length=500,
        )
    except HTTPException as error:
        if error.status_code != status.HTTP_422_UNPROCESSABLE_CONTENT:
            raise
        message = error.detail if isinstance(error.detail, str) else "Invalid source metadata."
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_bibliography_entry",
                "message": f'Source "{source.entry_id}" is invalid: {message}',
            },
        ) from error

    return SourceRecord(
        source_type=source.source_type.value,
        title=title,
        authors=authors,
        publication_year=publication_year,
        venue=venue,
        doi=doi,
        url=url,
        abstract=abstract,
        language=language,
        reading_status=ReadingStatus.UNREAD.value,
        identifiers=identifiers,
        citation_keys=(
            [
                SourceCitationKeyRecord(
                    bibliography_format=bibliography_format.value,
                    value=citation_key,
                )
            ]
            if citation_key is not None
            else []
        ),
    )


def _bibliography_export_source(record: SourceRecord) -> BibliographyExportSource:
    return BibliographyExportSource(
        source_id=record.id,
        source_type=SourceType(record.source_type),
        title=record.title,
        authors=list(record.authors),
        publication_year=record.publication_year,
        venue=record.venue,
        doi=record.doi,
        url=record.url,
        abstract=record.abstract,
        language=record.language,
        identifiers=[
            BibliographyIdentifier(
                identifier_type=identifier.identifier_type,
                value=identifier.value,
            )
            for identifier in record.identifiers
        ],
        citation_keys=[
            BibliographyCitationKey(
                bibliography_format=BibliographyFormat(citation_key.bibliography_format),
                value=citation_key.value,
            )
            for citation_key in record.citation_keys
        ],
    )


def _oversized_bibliography_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail={
            "code": "oversized_bibliography",
            "message": "Bibliography files are limited to 5 MB.",
            "maximum_byte_size": MAX_BIBLIOGRAPHY_BYTES,
        },
    )


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
                selectinload(SourceRecord.identifiers),
                selectinload(SourceRecord.citation_keys),
                selectinload(SourceRecord.metadata_lookups),
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
            metadata_provenance=[
                MetadataProvenanceRead(
                    lookup_id=lookup.id,
                    provider=lookup.provider,
                    provider_url=lookup.provider_url,
                    identifier_type=lookup.identifier_type,
                    requested_identifier=lookup.requested_identifier,
                    retrieved_identifier=lookup.retrieved_identifier,
                    retrieved_at=lookup.retrieved_at,
                    applied_fields=lookup.applied_fields,
                    applied_at=lookup.applied_at,
                )
                for lookup in sorted(record.metadata_lookups, key=lambda item: item.id)
                if lookup.applied_fields is not None and lookup.applied_at is not None
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
        identifiers=[
            SourceIdentifierRead(
                identifier_type=identifier.identifier_type,
                value=identifier.value,
            )
            for identifier in sorted(
                record.identifiers,
                key=lambda item: (item.identifier_type, item.normalized_value),
            )
        ],
        citation_keys=[
            SourceCitationKeyRead(
                bibliography_format=BibliographyFormat(citation_key.bibliography_format),
                value=citation_key.value,
            )
            for citation_key in sorted(
                record.citation_keys,
                key=lambda item: (item.bibliography_format, item.value),
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


def _reader_document_read(database: Database, record: AttachmentRecord) -> ReaderDocumentRead:
    availability = _attachment_availability(database, record)
    return ReaderDocumentRead(
        attachment_id=record.id,
        source_id=record.source_id,
        source_title=record.source.title,
        original_filename=record.original_filename,
        byte_size=record.byte_size,
        attachment_availability=availability,
        reader_notes=[
            _reader_note_read(database, note, attachment_availability=availability)
            for note in sorted(record.notes, key=lambda item: (item.page_number or 0, item.id))
        ],
    )


def _require_pdf_attachment(session: Session, attachment_id: int) -> AttachmentRecord:
    record = session.get(AttachmentRecord, attachment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if record.detected_format != "pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "not_pdf",
                "message": "Only PDF attachments can have Reader annotations.",
            },
        )
    return record


def _highlight_read(record: HighlightRecord, *, source_id: int) -> HighlightRead:
    return HighlightRead(
        id=record.id,
        attachment_id=record.attachment_id,
        source_id=source_id,
        page_number=record.page_number,
        selected_text=record.selected_text,
        rectangles=[
            HighlightRectangle.model_validate(rectangle) for rectangle in record.rectangles
        ],
        created_at=record.created_at,
    )


def _read_reader_note(database: Database, note_id: int) -> ReaderNoteRead:
    with database.session() as session:
        record = session.scalar(
            select(NoteRecord)
            .where(NoteRecord.id == note_id)
            .options(
                selectinload(NoteRecord.source),
                selectinload(NoteRecord.attachment),
                selectinload(NoteRecord.highlight),
            )
        )
        if record is None or record.attachment is None or record.page_number is None:
            raise HTTPException(status_code=404, detail="Reader note not found")
        return _reader_note_read(database, record)


def _reader_note_read(
    database: Database,
    record: NoteRecord,
    *,
    attachment_availability: Literal["available", "missing_or_changed", "storage_unavailable"]
    | None = None,
) -> ReaderNoteRead:
    attachment = record.attachment
    if attachment is None or record.page_number is None:
        raise ValueError("Reader notes require a structured attachment and page locator.")
    availability = attachment_availability or _attachment_availability(database, attachment)
    return ReaderNoteRead(
        id=record.id,
        source_id=record.source_id,
        source_title=record.source.title,
        attachment_id=attachment.id,
        original_filename=attachment.original_filename,
        page_number=record.page_number,
        body=record.body,
        highlight=(
            _highlight_read(record.highlight, source_id=record.source_id)
            if record.highlight is not None
            else None
        ),
        attachment_availability=availability,
        created_at=record.created_at,
    )


def _attachment_availability(
    database: Database,
    record: AttachmentRecord,
) -> Literal["available", "missing_or_changed", "storage_unavailable"]:
    if database.library_paths is None:
        return "storage_unavailable"
    try:
        ManagedAttachmentStore(database.library_paths).verified_file_for(
            record.checksum,
            record.managed_path,
        )
    except ManagedFileConflictError:
        return "missing_or_changed"
    return "available"


def _reader_note_http_error(error: Exception) -> HTTPException:
    if isinstance(error, (ReaderNoteAttachmentNotFoundError, ReaderNoteNotFoundError)):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ReaderNoteNotPdfError):
        return HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "reader_note_not_pdf", "message": str(error)},
        )
    if isinstance(error, ReaderNoteValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "reader_note_validation", "message": str(error)},
        )
    if isinstance(error, ReaderNoteRelationshipError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "reader_note_relationship_changed", "message": str(error)},
        )
    if isinstance(error, ReaderNoteDatabaseError):
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "reader_note_write_failed", "message": str(error)},
        )
    raise TypeError(f"Unsupported Reader note error: {type(error).__name__}")


app = create_app()
