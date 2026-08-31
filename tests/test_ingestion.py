from pathlib import Path

import pytest
from sqlalchemy import func, select

from litrev.domain.sources import SourceType
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import AttachmentRecord, SourceRecord
from litrev.infrastructure.storage import LibraryPaths
from litrev.services.documents import DuplicateAttachmentError, ingest_document_bytes


def test_ingestion_saves_the_source_and_original_together(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    content = b"paper,year\nA useful paper,2026\n"

    ingested = ingest_document_bytes(
        database,
        source_type=SourceType.PAPER,
        title="  A useful paper  ",
        data=content,
        original_filename="papers.csv",
        media_type="text/csv",
    )

    with database.session() as session:
        source = session.get(SourceRecord, ingested.source_id)
        attachment = session.get(AttachmentRecord, ingested.attachment_id)
        assert source is not None
        assert source.title == "A useful paper"
        assert source.source_type == SourceType.PAPER.value
        assert attachment is not None
        assert attachment.source_id == source.id
        assert attachment.detected_format == "csv"
        assert attachment.conversion_status == "pending"
        assert (paths.root / attachment.managed_path).read_bytes() == content


def test_duplicate_ingestion_does_not_leave_an_empty_source(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    content = b"identical document bytes"
    first = ingest_document_bytes(
        database,
        source_type=SourceType.PAPER,
        title="First paper",
        data=content,
        original_filename="first.pdf",
    )

    with pytest.raises(DuplicateAttachmentError) as caught:
        ingest_document_bytes(
            database,
            source_type=SourceType.PAPER,
            title="Duplicate paper",
            data=content,
            original_filename="renamed.pdf",
        )

    assert caught.value.attachment_id == first.attachment_id
    with database.session() as session:
        source_count = session.scalar(select(func.count()).select_from(SourceRecord))
        attachment_count = session.scalar(select(func.count()).select_from(AttachmentRecord))
        assert source_count == 1
        assert attachment_count == 1


def test_storage_failure_rolls_back_the_new_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()

    def fail_to_install(_source: Path, _destination: Path) -> None:
        raise OSError("simulated storage failure")

    monkeypatch.setattr("litrev.infrastructure.storage.os.replace", fail_to_install)

    with pytest.raises(OSError, match="simulated storage failure"):
        ingest_document_bytes(
            database,
            source_type=SourceType.PAPER,
            title="A useful paper",
            data=b"document bytes",
            original_filename="paper.pdf",
        )

    with database.session() as session:
        source_count = session.scalar(select(func.count()).select_from(SourceRecord))
        attachment_count = session.scalar(select(func.count()).select_from(AttachmentRecord))
        assert source_count == 0
        assert attachment_count == 0
    assert not [path for path in paths.attachments.rglob("*") if path.is_file()]
    assert not list(paths.temporary_imports.iterdir())


def test_database_failure_leaves_no_source_and_the_original_can_be_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    content = b"document bytes"
    original_session_factory = database.session

    def failing_session_factory():
        session = original_session_factory()

        def fail_commit() -> None:
            raise RuntimeError("simulated database failure")

        session.commit = fail_commit
        return session

    monkeypatch.setattr(database, "session", failing_session_factory)

    with pytest.raises(RuntimeError, match="simulated database failure"):
        ingest_document_bytes(
            database,
            source_type=SourceType.PAPER,
            title="A useful paper",
            data=content,
            original_filename="paper.pdf",
        )

    monkeypatch.setattr(database, "session", original_session_factory)
    managed_files = [path for path in paths.attachments.rglob("*") if path.is_file()]
    assert len(managed_files) == 1
    with database.session() as session:
        source_count = session.scalar(select(func.count()).select_from(SourceRecord))
        attachment_count = session.scalar(select(func.count()).select_from(AttachmentRecord))
        assert source_count == 0
        assert attachment_count == 0

    retried = ingest_document_bytes(
        database,
        source_type=SourceType.PAPER,
        title="A useful paper",
        data=content,
        original_filename="paper.pdf",
    )

    with database.session() as session:
        attachment = session.get(AttachmentRecord, retried.attachment_id)
        assert attachment is not None
        assert paths.root / attachment.managed_path == managed_files[0]
