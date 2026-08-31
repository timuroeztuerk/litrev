from pathlib import Path

import pytest
from sqlalchemy import select

from litrev.domain.documents import ConversionStatus
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import AttachmentRecord, SourceRecord
from litrev.infrastructure.storage import LibraryPaths, ManagedFileConflictError
from litrev.services import documents
from litrev.services.documents import (
    AttachmentNotConvertedError,
    DocumentConversionFailure,
    convert_attachment,
    read_attachment_markdown,
    store_attachment_bytes,
)

CSV_CONTENT = b"paper,year\nA useful paper,2026\n"


def create_stored_attachment(
    tmp_path: Path,
    *,
    content: bytes = CSV_CONTENT,
    filename: str = "papers.csv",
) -> tuple[LibraryPaths, Database, AttachmentRecord]:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    with database.session() as session:
        source = SourceRecord(title="A useful paper")
        session.add(source)
        session.commit()
        source_id = source.id

    attachment = store_attachment_bytes(
        database,
        source_id=source_id,
        data=content,
        original_filename=filename,
        media_type="text/csv",
    )
    return paths, database, attachment


def test_markdown_and_conversion_state_persist_across_restart(tmp_path: Path) -> None:
    paths, database, attachment = create_stored_attachment(tmp_path)

    converted = convert_attachment(database, attachment.id)

    assert converted.conversion_status == ConversionStatus.SUCCEEDED.value
    assert converted.detected_format == "csv"
    assert converted.extracted_path == (
        f"extracted/{converted.checksum[:2]}/{converted.checksum}.md"
    )
    assert converted.conversion_message is None
    assert converted.conversion_diagnostics is None

    database.engine.dispose()
    reopened_database = Database.from_library(paths)
    reopened_database.migrate()

    assert "A useful paper" in read_attachment_markdown(reopened_database, attachment.id)
    with reopened_database.session() as session:
        reopened = session.get(AttachmentRecord, attachment.id)
        assert reopened is not None
        assert reopened.conversion_status == ConversionStatus.SUCCEEDED.value
        assert reopened.extracted_path == converted.extracted_path


@pytest.mark.parametrize(
    "failure_status",
    [
        ConversionStatus.NEEDS_OCR,
        ConversionStatus.ENCRYPTED,
        ConversionStatus.UNSUPPORTED,
        ConversionStatus.MALFORMED,
        ConversionStatus.RESOURCE_LIMIT,
        ConversionStatus.MISSING_PART,
    ],
)
def test_supported_conversion_failure_remains_visible_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_status: ConversionStatus,
) -> None:
    paths, database, attachment = create_stored_attachment(tmp_path)

    def fail_conversion(_data: bytes, _filename: str) -> documents.ConvertedDocument:
        raise DocumentConversionFailure(
            failure_status,
            "Conversion failed safely.",
            diagnostics={"detail": "test diagnostic"},
        )

    monkeypatch.setattr(documents, "convert_document_bytes", fail_conversion)

    with pytest.raises(DocumentConversionFailure):
        convert_attachment(database, attachment.id)

    database.engine.dispose()
    reopened_database = Database.from_library(paths)
    reopened_database.migrate()
    with reopened_database.session() as session:
        failed = session.get(AttachmentRecord, attachment.id)
        assert failed is not None
        assert failed.conversion_status == failure_status.value
        assert failed.conversion_message == "Conversion failed safely."
        assert failed.conversion_diagnostics == {"detail": "test diagnostic"}
        assert failed.extracted_path is None
    assert (paths.root / attachment.managed_path).read_bytes() == CSV_CONTENT
    with pytest.raises(AttachmentNotConvertedError):
        read_attachment_markdown(reopened_database, attachment.id)


def test_empty_attachment_records_a_specific_failure(tmp_path: Path) -> None:
    _paths, database, attachment = create_stored_attachment(tmp_path, content=b"")

    with pytest.raises(DocumentConversionFailure) as caught:
        convert_attachment(database, attachment.id)

    assert caught.value.code == ConversionStatus.EMPTY.value
    with database.session() as session:
        failed = session.get(AttachmentRecord, attachment.id)
        assert failed is not None
        assert failed.conversion_status == ConversionStatus.EMPTY.value
        assert failed.conversion_message == "The document is empty."


def test_oversized_attachment_records_sizes_without_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paths, database, attachment = create_stored_attachment(tmp_path, content=b"12345")
    monkeypatch.setattr(documents, "MAX_DOCUMENT_BYTES", 4)

    with pytest.raises(DocumentConversionFailure) as caught:
        convert_attachment(database, attachment.id)

    assert caught.value.code == ConversionStatus.OVERSIZED.value
    with database.session() as session:
        failed = session.get(AttachmentRecord, attachment.id)
        assert failed is not None
        assert failed.conversion_status == ConversionStatus.OVERSIZED.value
        assert failed.conversion_diagnostics == {
            "byte_size": 5,
            "maximum_byte_size": 4,
        }


def test_failed_conversion_can_be_retried_successfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, database, attachment = create_stored_attachment(tmp_path)
    converter = documents.convert_document_bytes

    def needs_ocr(_data: bytes, _filename: str) -> documents.ConvertedDocument:
        raise DocumentConversionFailure(
            ConversionStatus.NEEDS_OCR,
            "OCR is required.",
            pages=(2,),
            diagnostics={"page_count": 3},
        )

    monkeypatch.setattr(documents, "convert_document_bytes", needs_ocr)
    with pytest.raises(DocumentConversionFailure):
        convert_attachment(database, attachment.id)

    monkeypatch.setattr(documents, "convert_document_bytes", converter)
    retried = convert_attachment(database, attachment.id)

    assert retried.conversion_status == ConversionStatus.SUCCEEDED.value
    assert retried.conversion_message is None
    assert retried.conversion_diagnostics is None
    assert "A useful paper" in read_attachment_markdown(database, attachment.id)
    assert (paths.root / attachment.managed_path).read_bytes() == CSV_CONTENT


def test_extracted_file_failure_does_not_publish_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, database, attachment = create_stored_attachment(tmp_path)

    def fail_to_install(_source: Path, _destination: Path) -> None:
        raise OSError("simulated extraction storage failure")

    monkeypatch.setattr("litrev.infrastructure.storage.os.replace", fail_to_install)

    with pytest.raises(OSError, match="simulated extraction storage failure"):
        convert_attachment(database, attachment.id)

    with database.session() as session:
        pending = session.scalar(select(AttachmentRecord))
        assert pending is not None
        assert pending.conversion_status == ConversionStatus.PENDING.value
        assert pending.extracted_path is None
    assert not [path for path in paths.extracted.rglob("*") if path.is_file()]
    assert not list(paths.temporary_imports.iterdir())
    assert (paths.root / attachment.managed_path).read_bytes() == CSV_CONTENT


def test_database_failure_leaves_reusable_extracted_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, database, attachment = create_stored_attachment(tmp_path)
    original_session_factory = database.session

    def failing_session_factory():
        session = original_session_factory()

        def fail_commit() -> None:
            raise RuntimeError("simulated database failure")

        session.commit = fail_commit
        return session

    monkeypatch.setattr(database, "session", failing_session_factory)

    with pytest.raises(RuntimeError, match="simulated database failure"):
        convert_attachment(database, attachment.id)

    monkeypatch.setattr(database, "session", original_session_factory)
    extracted_files = [path for path in paths.extracted.rglob("*") if path.is_file()]
    assert len(extracted_files) == 1
    with database.session() as session:
        pending = session.get(AttachmentRecord, attachment.id)
        assert pending is not None
        assert pending.conversion_status == ConversionStatus.PENDING.value
        assert pending.extracted_path is None

    retried = convert_attachment(database, attachment.id)

    assert retried.extracted_path is not None
    assert paths.root / retried.extracted_path == extracted_files[0]
    assert "A useful paper" in read_attachment_markdown(database, attachment.id)


def test_missing_managed_original_does_not_change_conversion_state(tmp_path: Path) -> None:
    paths, database, attachment = create_stored_attachment(tmp_path)
    (paths.root / attachment.managed_path).unlink()

    with pytest.raises(ManagedFileConflictError, match="missing"):
        convert_attachment(database, attachment.id)

    with database.session() as session:
        pending = session.get(AttachmentRecord, attachment.id)
        assert pending is not None
        assert pending.conversion_status == ConversionStatus.PENDING.value
        assert pending.conversion_message is None
        assert pending.conversion_diagnostics is None
        assert pending.extracted_path is None
