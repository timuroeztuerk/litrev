from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, local

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from litrev.infrastructure.database import Database
from litrev.infrastructure.models import AttachmentRecord, SourceRecord
from litrev.infrastructure.storage import LibraryPaths
from litrev.services.documents import DuplicateAttachmentError, store_attachment_bytes


def create_source(database: Database, title: str = "A useful paper") -> int:
    with database.session() as session:
        source = SourceRecord(title=title)
        session.add(source)
        session.commit()
        return source.id


def test_attachment_and_managed_file_persist_across_restart(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    source_id = create_source(database)
    content = b"paper,year\nA useful paper,2026\n"

    stored = store_attachment_bytes(
        database,
        source_id=source_id,
        data=content,
        original_filename="papers.csv",
        media_type="text/csv",
        detected_format="csv",
    )

    managed_file = paths.root / stored.managed_path
    assert managed_file.read_bytes() == content
    assert stored.managed_path == f"attachments/{stored.checksum[:2]}/{stored.checksum}"
    assert stored.byte_size == len(content)
    assert stored.conversion_status == "pending"
    assert stored.created_at is not None
    assert stored.updated_at is not None

    database.engine.dispose()
    reopened_database = Database.from_library(paths)
    reopened_database.migrate()
    with reopened_database.session() as session:
        reopened = session.scalar(select(AttachmentRecord))
        assert reopened is not None
        assert reopened.original_filename == "papers.csv"
        assert reopened.media_type == "text/csv"
        assert reopened.detected_format == "csv"
        assert reopened.source.title == "A useful paper"
        assert (paths.root / reopened.managed_path).read_bytes() == content


def test_source_can_own_multiple_distinct_attachments(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    source_id = create_source(database)

    store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"main document",
        original_filename="paper.pdf",
        media_type="application/pdf",
        detected_format="pdf",
    )
    store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"supplementary data",
        original_filename="supplement.csv",
        media_type="text/csv",
        detected_format="csv",
    )

    with database.session() as session:
        source = session.get(SourceRecord, source_id)
        assert source is not None
        assert {attachment.original_filename for attachment in source.attachments} == {
            "paper.pdf",
            "supplement.csv",
        }


def test_duplicate_bytes_are_reported_without_a_second_attachment(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    first_source_id = create_source(database, "First paper")
    second_source_id = create_source(database, "Second paper")
    content = b"identical document bytes"
    first = store_attachment_bytes(
        database,
        source_id=first_source_id,
        data=content,
        original_filename="first.pdf",
        media_type="application/pdf",
        detected_format="pdf",
    )

    with pytest.raises(DuplicateAttachmentError) as caught:
        store_attachment_bytes(
            database,
            source_id=second_source_id,
            data=content,
            original_filename="renamed.pdf",
            media_type="application/pdf",
            detected_format="pdf",
        )

    assert caught.value.attachment_id == first.id
    with database.session() as session:
        attachment_count = session.scalar(select(func.count()).select_from(AttachmentRecord))
        assert attachment_count == 1
    assert [path for path in paths.attachments.rglob("*") if path.is_file()] == [
        paths.root / first.managed_path
    ]


def test_concurrent_duplicate_bytes_are_reported_as_a_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    source_id = create_source(database)
    content = b"concurrently imported document bytes"
    original_session_factory = database.session
    loser_checked = Event()
    winner_finished = Event()
    thread_state = local()

    def synchronized_session_factory():
        session = original_session_factory()
        original_scalar = session.scalar
        duplicate_checked = False

        def synchronized_scalar(statement, *args, **kwargs):
            nonlocal duplicate_checked
            if duplicate_checked:
                return original_scalar(statement, *args, **kwargs)
            duplicate_checked = True
            if thread_state.role == "winner":
                assert loser_checked.wait(timeout=5)
            value = original_scalar(statement, *args, **kwargs)
            if thread_state.role == "loser":
                assert value is None
                session.rollback()
                loser_checked.set()
                assert winner_finished.wait(timeout=5)
            return value

        session.scalar = synchronized_scalar
        return session

    monkeypatch.setattr(database, "session", synchronized_session_factory)

    def store(role: str) -> AttachmentRecord:
        thread_state.role = role
        try:
            return store_attachment_bytes(
                database,
                source_id=source_id,
                data=content,
                original_filename=f"{role}.pdf",
                media_type="application/pdf",
                detected_format="pdf",
            )
        finally:
            if role == "winner":
                winner_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        losing_import = executor.submit(store, "loser")
        winning_import = executor.submit(store, "winner")

        winner = winning_import.result(timeout=10)
        with pytest.raises(DuplicateAttachmentError) as caught:
            losing_import.result(timeout=10)

    assert caught.value.attachment_id == winner.id
    with original_session_factory() as session:
        attachment_count = session.scalar(select(func.count()).select_from(AttachmentRecord))
        assert attachment_count == 1


def test_unrelated_integrity_failure_is_not_reported_as_a_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    source_id = create_source(database)
    original_session_factory = database.session
    unrelated_error = IntegrityError(
        "simulated insert",
        {},
        RuntimeError("simulated unrelated constraint failure"),
    )

    def failing_session_factory():
        session = original_session_factory()

        def fail_flush() -> None:
            raise unrelated_error

        session.flush = fail_flush
        return session

    monkeypatch.setattr(database, "session", failing_session_factory)

    with pytest.raises(IntegrityError) as caught:
        store_attachment_bytes(
            database,
            source_id=source_id,
            data=b"new document bytes",
            original_filename="paper.pdf",
        )

    assert caught.value is unrelated_error


def test_managed_file_failure_rolls_back_the_attachment_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    source_id = create_source(database)

    def fail_to_install(_source: Path, _destination: Path) -> None:
        raise OSError("simulated storage failure")

    monkeypatch.setattr("litrev.infrastructure.storage.os.replace", fail_to_install)

    with pytest.raises(OSError, match="simulated storage failure"):
        store_attachment_bytes(
            database,
            source_id=source_id,
            data=b"document bytes",
            original_filename="paper.pdf",
            media_type="application/pdf",
            detected_format="pdf",
        )

    with database.session() as session:
        attachment_count = session.scalar(select(func.count()).select_from(AttachmentRecord))
        assert attachment_count == 0
    assert not [path for path in paths.attachments.rglob("*") if path.is_file()]
    assert not list(paths.temporary_imports.iterdir())


def test_database_failure_leaves_a_reusable_file_without_a_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    source_id = create_source(database)
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
        store_attachment_bytes(
            database,
            source_id=source_id,
            data=content,
            original_filename="paper.pdf",
            media_type="application/pdf",
            detected_format="pdf",
        )

    monkeypatch.setattr(database, "session", original_session_factory)
    managed_files = [path for path in paths.attachments.rglob("*") if path.is_file()]
    assert len(managed_files) == 1
    with database.session() as session:
        attachment_count = session.scalar(select(func.count()).select_from(AttachmentRecord))
        assert attachment_count == 0

    retried = store_attachment_bytes(
        database,
        source_id=source_id,
        data=content,
        original_filename="paper.pdf",
        media_type="application/pdf",
        detected_format="pdf",
    )

    assert paths.root / retried.managed_path == managed_files[0]
    with database.session() as session:
        attachment_count = session.scalar(select(func.count()).select_from(AttachmentRecord))
        assert attachment_count == 1
