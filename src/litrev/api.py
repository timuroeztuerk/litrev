from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool

from litrev.diagnostics import run_checks
from litrev.domain.documents import ConversionStatus
from litrev.domain.sources import ReadingStatus, SourceType
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import (
    AttachmentRecord,
    CollectionRecord,
    SourceCitationKeyRecord,
    SourceIdentifierRecord,
    SourceMetadataLookupRecord,
    SourceRecord,
    TagRecord,
)
from litrev.infrastructure.storage import (
    LibraryPaths,
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
    CROSSREF_PROVIDER,
    DoiMetadata,
    DoiMetadataMalformedError,
    DoiMetadataMismatchError,
    DoiMetadataNotFoundError,
    DoiMetadataRateLimitedError,
    DoiMetadataUnavailableError,
    crossref_record_url,
    lookup_crossref_metadata,
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


DoiMetadataField = Literal[
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

_DOI_METADATA_FIELDS: tuple[DoiMetadataField, ...] = (
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


class DoiMetadataProvenanceRead(BaseModel):
    lookup_id: int
    provider: str
    provider_url: str
    requested_doi: str
    retrieved_doi: str
    retrieved_at: datetime
    applied_fields: list[DoiMetadataField]
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
    metadata_provenance: list[DoiMetadataProvenanceRead]


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


class DoiMetadataProposalRead(BaseModel):
    source_type: SourceType | None
    title: str | None
    authors: list[str] | None
    publication_year: int | None
    venue: str | None
    url: str | None
    abstract: str | None
    language: str | None
    identifiers: list[SourceIdentifierRead] | None


class DoiMetadataLookupRead(BaseModel):
    id: int
    provider: str
    provider_url: str
    requested_doi: str
    retrieved_doi: str
    retrieved_at: datetime
    proposal: DoiMetadataProposalRead
    available_fields: list[DoiMetadataField]
    conflicting_fields: list[DoiMetadataField]


class DoiMetadataApply(BaseModel):
    fields: list[DoiMetadataField]


class ExtractedTextRead(BaseModel):
    attachment_id: int
    markdown: str


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
    doi_metadata_provider: Callable[[str], DoiMetadata] = lookup_crossref_metadata,
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
        "/api/sources/{source_id}/doi-metadata-lookups",
        response_model=DoiMetadataLookupRead,
    )
    async def lookup_source_doi_metadata(source_id: int) -> DoiMetadataLookupRead:
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
            requested_doi = record.doi

        try:
            metadata = await run_in_threadpool(doi_metadata_provider, requested_doi)
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

        proposal = _doi_metadata_proposal(metadata)
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
                provider=CROSSREF_PROVIDER,
                provider_url=crossref_record_url(metadata.doi),
                requested_doi=normalize_imported_doi(requested_doi),
                retrieved_doi=metadata.doi,
                reviewed_metadata=reviewed_metadata,
                proposed_metadata=proposal.model_dump(mode="json"),
            )
            session.add(lookup)
            session.commit()
            session.refresh(lookup)
            return _doi_metadata_lookup_read(lookup, reviewed_metadata, proposal)

    @application.post(
        "/api/sources/{source_id}/doi-metadata-lookups/{lookup_id}/apply",
        response_model=SourceDetailRead,
    )
    async def apply_source_doi_metadata(
        source_id: int,
        lookup_id: int,
        selection: DoiMetadataApply,
    ) -> SourceDetailRead:
        selected_fields = [field for field in _DOI_METADATA_FIELDS if field in selection.fields]
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

            proposal = _stored_doi_metadata_proposal(lookup.proposed_metadata)
            available_fields = _available_doi_metadata_fields(proposal)
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

            _apply_doi_metadata_fields(record, proposal, selected_fields)
            lookup.applied_fields = selected_fields
            lookup.applied_at = datetime.now(UTC)
            session.commit()

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


def _doi_metadata_proposal(metadata: DoiMetadata) -> DoiMetadataProposalRead:
    return DoiMetadataProposalRead(
        source_type=metadata.source_type,
        title=metadata.title,
        authors=metadata.authors,
        publication_year=metadata.publication_year,
        venue=metadata.venue,
        url=metadata.url,
        abstract=metadata.abstract,
        language=metadata.language,
        identifiers=(
            [
                SourceIdentifierRead(
                    identifier_type=identifier.identifier_type,
                    value=identifier.value,
                )
                for identifier in metadata.identifiers
            ]
            if metadata.identifiers is not None
            else None
        ),
    )


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


def _stored_doi_metadata_proposal(payload: dict[str, object]) -> DoiMetadataProposalRead:
    try:
        return DoiMetadataProposalRead.model_validate(payload)
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "invalid_metadata_provenance",
                "message": "The saved DOI metadata review is invalid; nothing was changed.",
            },
        ) from error


def _available_doi_metadata_fields(
    proposal: DoiMetadataProposalRead,
) -> list[DoiMetadataField]:
    values = proposal.model_dump(mode="json")
    return [field for field in _DOI_METADATA_FIELDS if values[field] is not None]


def _doi_metadata_lookup_read(
    lookup: SourceMetadataLookupRecord,
    reviewed_metadata: dict[str, object],
    proposal: DoiMetadataProposalRead,
) -> DoiMetadataLookupRead:
    proposed_metadata = proposal.model_dump(mode="json")
    available_fields = _available_doi_metadata_fields(proposal)
    conflicting_fields = [
        field
        for field in available_fields
        if field != "identifiers"
        and _metadata_value_present(reviewed_metadata.get(field))
        and reviewed_metadata.get(field) != proposed_metadata.get(field)
    ]
    return DoiMetadataLookupRead(
        id=lookup.id,
        provider=lookup.provider,
        provider_url=lookup.provider_url,
        requested_doi=lookup.requested_doi,
        retrieved_doi=lookup.retrieved_doi,
        retrieved_at=lookup.retrieved_at,
        proposal=proposal,
        available_fields=available_fields,
        conflicting_fields=conflicting_fields,
    )


def _metadata_value_present(value: object) -> bool:
    return value is not None and value != "" and value != []


def _apply_doi_metadata_fields(
    record: SourceRecord,
    proposal: DoiMetadataProposalRead,
    fields: list[DoiMetadataField],
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
                DoiMetadataProvenanceRead(
                    lookup_id=lookup.id,
                    provider=lookup.provider,
                    provider_url=lookup.provider_url,
                    requested_doi=lookup.requested_doi,
                    retrieved_doi=lookup.retrieved_doi,
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


app = create_app()
