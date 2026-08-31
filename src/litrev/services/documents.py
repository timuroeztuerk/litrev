from __future__ import annotations

import hashlib
from dataclasses import dataclass

import anydoc
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from litrev.domain.documents import REMOVABLE_CONVERSION_STATUSES, ConversionStatus
from litrev.domain.sources import SourceType
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import AttachmentRecord, SourceRecord
from litrev.infrastructure.storage import (
    LibraryPaths,
    ManagedAttachmentStore,
    ManagedExtractionStore,
    ManagedFileConflictError,
    stage_managed_attachment_removal,
)

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class ConvertedDocument:
    filename: str
    format: str
    markdown: str


@dataclass(frozen=True)
class IngestedDocument:
    source_id: int
    attachment_id: int


class DocumentConversionFailure(Exception):
    def __init__(
        self,
        code: ConversionStatus,
        message: str,
        *,
        pages: tuple[int, ...] = (),
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code.value
        self.pages = pages
        self.diagnostics = dict(diagnostics or {})
        if pages:
            self.diagnostics["pages"] = list(pages)


class AttachmentSourceNotFoundError(Exception):
    pass


class AttachmentNotFoundError(Exception):
    pass


class AttachmentNotConvertedError(Exception):
    pass


class AttachmentRemovalNotAllowedError(Exception):
    pass


class AttachmentRemovalDatabaseError(Exception):
    pass


class DuplicateAttachmentError(Exception):
    def __init__(self, attachment_id: int) -> None:
        super().__init__(f"These bytes are already stored as attachment {attachment_id}.")
        self.attachment_id = attachment_id


def ingest_document_bytes(
    database: Database,
    *,
    source_type: SourceType,
    title: str,
    data: bytes,
    original_filename: str,
    media_type: str | None = None,
) -> IngestedDocument:
    paths = database.library_paths
    if paths is None:
        raise ValueError("Document ingestion requires a library-backed database")

    clean_title = title.strip()
    if not clean_title:
        raise ValueError("A source title is required")
    if len(clean_title) > 500:
        raise ValueError("Source titles are limited to 500 characters")

    detected_format = anydoc.format_from_bytes(data) or anydoc.format_from_path(original_filename)
    with database.session() as session:
        source = SourceRecord(source_type=source_type.value, title=clean_title)
        session.add(source)
        try:
            attachment = _store_attachment_record(
                session,
                paths=paths,
                source=source,
                data=data,
                original_filename=original_filename,
                media_type=media_type,
                detected_format=detected_format,
            )
            session.commit()
        except IntegrityError as error:
            session.rollback()
            duplicate = _find_duplicate_attachment(session, hashlib.sha256(data).hexdigest())
            if duplicate is not None:
                raise DuplicateAttachmentError(duplicate.id) from error
            raise
        except Exception:
            session.rollback()
            raise

        return IngestedDocument(source_id=source.id, attachment_id=attachment.id)


def store_attachment_bytes(
    database: Database,
    *,
    source_id: int,
    data: bytes,
    original_filename: str,
    media_type: str | None = None,
    detected_format: str | None = None,
) -> AttachmentRecord:
    paths = database.library_paths
    if paths is None:
        raise ValueError("Managed attachment storage requires a library-backed database")

    with database.session() as session:
        source = session.get(SourceRecord, source_id)
        if source is None:
            raise AttachmentSourceNotFoundError(f"Source {source_id} does not exist.")
        try:
            record = _store_attachment_record(
                session,
                paths=paths,
                source=source,
                data=data,
                original_filename=original_filename,
                media_type=media_type,
                detected_format=detected_format,
            )
            session.commit()
        except IntegrityError as error:
            session.rollback()
            duplicate = _find_duplicate_attachment(session, hashlib.sha256(data).hexdigest())
            if duplicate is not None:
                raise DuplicateAttachmentError(duplicate.id) from error
            raise
        except Exception:
            session.rollback()
            raise

        session.refresh(record)
        session.expunge(record)
        return record


def _store_attachment_record(
    session: Session,
    *,
    paths: LibraryPaths,
    source: SourceRecord,
    data: bytes,
    original_filename: str,
    media_type: str | None,
    detected_format: str | None,
) -> AttachmentRecord:
    checksum = hashlib.sha256(data).hexdigest()
    duplicate = _find_duplicate_attachment(session, checksum)
    if duplicate is not None:
        raise DuplicateAttachmentError(duplicate.id)

    store = ManagedAttachmentStore(paths)
    record = AttachmentRecord(
        source=source,
        original_filename=original_filename,
        managed_path=store.relative_path_for(checksum),
        media_type=media_type,
        byte_size=len(data),
        checksum=checksum,
        detected_format=detected_format,
        conversion_status=ConversionStatus.PENDING.value,
    )
    session.add(record)
    session.flush()
    # The file must exist before the row becomes visible outside this transaction.
    store.put(data, checksum)
    return record


def _find_duplicate_attachment(session: Session, checksum: str) -> AttachmentRecord | None:
    return session.scalar(select(AttachmentRecord).where(AttachmentRecord.checksum == checksum))


def can_remove_attachment(record: AttachmentRecord) -> bool:
    try:
        status = ConversionStatus(record.conversion_status)
    except ValueError:
        return False
    return status in REMOVABLE_CONVERSION_STATUSES


def remove_failed_attachment(database: Database, attachment_id: int) -> None:
    paths = database.library_paths
    if paths is None:
        raise ValueError("Managed attachment removal requires a library-backed database")

    with database.session() as session:
        record = session.get(AttachmentRecord, attachment_id)
        if record is None:
            raise AttachmentNotFoundError(f"Attachment {attachment_id} does not exist.")
        if not can_remove_attachment(record):
            raise AttachmentRemovalNotAllowedError(
                "Only an attachment with a failed extraction can be removed."
            )

        staged = stage_managed_attachment_removal(
            paths,
            checksum=record.checksum,
            managed_path=record.managed_path,
            extracted_path=record.extracted_path,
        )
        try:
            session.delete(record)
            session.commit()
        except Exception as error:
            rollback_error: Exception | None = None
            try:
                session.rollback()
            except Exception as caught:
                rollback_error = caught
            staged.restore()
            raise AttachmentRemovalDatabaseError(
                "The attachment record could not be removed; its managed artifacts were restored."
            ) from (rollback_error or error)

        staged.discard()


def convert_attachment(database: Database, attachment_id: int) -> AttachmentRecord:
    paths = database.library_paths
    if paths is None:
        raise ValueError("Managed attachment conversion requires a library-backed database")

    attachment_store = ManagedAttachmentStore(paths)
    extraction_store = ManagedExtractionStore(paths)
    with database.session() as session:
        record = session.get(AttachmentRecord, attachment_id)
        if record is None:
            raise AttachmentNotFoundError(f"Attachment {attachment_id} does not exist.")

        attachment_path = attachment_store.file_for(record.checksum, record.managed_path)
        if attachment_path.stat().st_size != record.byte_size:
            raise ManagedFileConflictError(
                f"Managed attachment {record.managed_path!r} does not match its recorded size."
            )

        try:
            if record.byte_size == 0:
                raise DocumentConversionFailure(
                    ConversionStatus.EMPTY,
                    "The document is empty.",
                )
            if record.byte_size > MAX_DOCUMENT_BYTES:
                raise DocumentConversionFailure(
                    ConversionStatus.OVERSIZED,
                    "Documents are limited to 50 MB.",
                    diagnostics={
                        "byte_size": record.byte_size,
                        "maximum_byte_size": MAX_DOCUMENT_BYTES,
                    },
                )

            content = attachment_store.read(record.checksum, record.managed_path)
            converted = convert_document_bytes(content, record.original_filename)
        except DocumentConversionFailure as error:
            record.conversion_status = error.code
            record.conversion_message = str(error)
            record.conversion_diagnostics = error.diagnostics
            session.commit()
            raise

        extracted_path = extraction_store.put(converted.markdown, record.checksum)
        record.detected_format = converted.format
        record.extracted_path = extracted_path
        record.conversion_status = ConversionStatus.SUCCEEDED.value
        record.conversion_message = None
        record.conversion_diagnostics = None
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise

        session.refresh(record)
        session.expunge(record)
        return record


def read_attachment_markdown(database: Database, attachment_id: int) -> str:
    paths = database.library_paths
    if paths is None:
        raise ValueError("Managed attachment reading requires a library-backed database")

    with database.session() as session:
        record = session.get(AttachmentRecord, attachment_id)
        if record is None:
            raise AttachmentNotFoundError(f"Attachment {attachment_id} does not exist.")
        if (
            record.conversion_status != ConversionStatus.SUCCEEDED.value
            or record.extracted_path is None
        ):
            raise AttachmentNotConvertedError(
                f"Attachment {attachment_id} has no successful conversion."
            )
        checksum = record.checksum
        extracted_path = record.extracted_path

    return ManagedExtractionStore(paths).read(checksum, extracted_path)


def convert_document_bytes(data: bytes, filename: str) -> ConvertedDocument:
    """Convert a local document to Markdown using Anydoc's Rust engine."""
    detected_format = anydoc.format_from_bytes(data) or anydoc.format_from_path(filename)

    try:
        markdown = anydoc.to_markdown_bytes(data, detected_format)
    except anydoc.NeedsOcrError as error:
        raise DocumentConversionFailure(
            ConversionStatus.NEEDS_OCR,
            "This document contains scanned pages that need OCR.",
            pages=tuple(error.pages),
            diagnostics={"page_count": error.page_count},
        ) from error
    except anydoc.EncryptedError as error:
        raise DocumentConversionFailure(
            ConversionStatus.ENCRYPTED, "The document is encrypted."
        ) from error
    except anydoc.UnsupportedError as error:
        raise DocumentConversionFailure(
            ConversionStatus.UNSUPPORTED, "Anydoc does not support this document."
        ) from error
    except anydoc.MalformedError as error:
        raise DocumentConversionFailure(
            ConversionStatus.MALFORMED,
            "The document structure could not be read.",
            diagnostics=_present_diagnostics(part=error.part),
        ) from error
    except anydoc.ResourceLimitError as error:
        raise DocumentConversionFailure(
            ConversionStatus.RESOURCE_LIMIT,
            "The document exceeded Anydoc's safety limits.",
            diagnostics=_present_diagnostics(limit=error.limit),
        ) from error
    except anydoc.MissingPartError as error:
        raise DocumentConversionFailure(
            ConversionStatus.MISSING_PART,
            "A required part of the document is missing.",
            diagnostics=_present_diagnostics(part=error.part),
        ) from error

    return ConvertedDocument(
        filename=filename,
        format=detected_format or "unknown",
        markdown=markdown,
    )


def _present_diagnostics(**values: object) -> dict[str, object]:
    return {name: value for name, value in values.items() if value is not None}
