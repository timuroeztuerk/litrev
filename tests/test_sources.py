import os
from pathlib import Path

import pytest
from sqlalchemy import func, select

from litrev.domain.documents import ConversionStatus
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import (
    AttachmentRecord,
    CollectionRecord,
    NoteRecord,
    SourceRecord,
    TagRecord,
    source_collections,
    source_tags,
)
from litrev.infrastructure.storage import (
    LibraryPaths,
    ManagedExtractionStore,
    ManagedFileCleanupError,
    ManagedFileConflictError,
)
from litrev.services.documents import store_attachment_bytes
from litrev.services.sources import (
    SourceNotFoundError,
    SourceRemovalDatabaseError,
    remove_source,
)


def create_library(tmp_path: Path) -> tuple[LibraryPaths, Database]:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    database.migrate()
    return paths, database


def create_source(database: Database, title: str = "Source to delete") -> int:
    with database.session() as session:
        source = SourceRecord(title=title)
        session.add(source)
        session.commit()
        return source.id


def test_source_removal_deletes_owned_rows_and_files_but_keeps_reusable_organization(
    tmp_path: Path,
) -> None:
    paths, database = create_library(tmp_path)
    with database.session() as session:
        methods = TagRecord(name="Methods", normalized_name="methods")
        thesis = CollectionRecord(name="Thesis", normalized_name="thesis")
        source = SourceRecord(
            title="Source to delete",
            tags=[methods],
            collections=[thesis],
            notes=[NoteRecord(body="Recoverable note", locator="p. 4")],
        )
        retained = SourceRecord(title="Retained source", tags=[methods])
        session.add_all([source, retained])
        session.commit()
        source_id = source.id
        retained_id = retained.id
        note_id = source.notes[0].id

    first = store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"first original",
        original_filename="first.pdf",
    )
    second = store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"second original",
        original_filename="second.pdf",
    )
    extracted_path = ManagedExtractionStore(paths).put("# Extracted", first.checksum)
    unrecorded_extracted_path = ManagedExtractionStore(paths).put(
        "# Left by an interrupted conversion",
        second.checksum,
    )
    with database.session() as session:
        converted = session.get(AttachmentRecord, first.id)
        assert converted is not None
        converted.conversion_status = ConversionStatus.SUCCEEDED.value
        converted.extracted_path = extracted_path
        session.commit()

    remove_source(database, source_id)

    with database.session() as session:
        assert session.get(SourceRecord, source_id) is None
        assert session.get(NoteRecord, note_id) is None
        assert session.get(AttachmentRecord, first.id) is None
        assert session.get(AttachmentRecord, second.id) is None
        assert session.scalar(select(func.count()).select_from(source_tags)) == 1
        assert session.scalar(select(func.count()).select_from(source_collections)) == 0
        assert session.scalar(select(TagRecord.name)) == "Methods"
        assert session.scalar(select(CollectionRecord.name)) == "Thesis"
        retained = session.get(SourceRecord, retained_id)
        assert retained is not None
        assert [tag.name for tag in retained.tags] == ["Methods"]
    assert not (paths.root / first.managed_path).exists()
    assert not (paths.root / second.managed_path).exists()
    assert not (paths.root / extracted_path).exists()
    assert not (paths.root / unrecorded_extracted_path).exists()
    assert not list(paths.temporary_imports.iterdir())


def test_source_removal_validates_every_managed_path_before_staging_files(
    tmp_path: Path,
) -> None:
    paths, database = create_library(tmp_path)
    source_id = create_source(database)
    first = store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"first original",
        original_filename="first.pdf",
    )
    second = store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"second original",
        original_filename="second.pdf",
    )
    with database.session() as session:
        unsafe = session.get(AttachmentRecord, second.id)
        assert unsafe is not None
        unsafe.managed_path = "attachments/not-the-checksum"
        session.commit()

    with pytest.raises(ManagedFileConflictError, match="does not match checksum"):
        remove_source(database, source_id)

    with database.session() as session:
        assert session.get(SourceRecord, source_id) is not None
        assert session.get(AttachmentRecord, first.id) is not None
        assert session.get(AttachmentRecord, second.id) is not None
    assert (paths.root / first.managed_path).read_bytes() == b"first original"
    assert (paths.root / second.managed_path).read_bytes() == b"second original"
    assert not list(paths.temporary_imports.iterdir())


def test_source_removal_staging_failure_restores_prior_files_and_keeps_database_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, database = create_library(tmp_path)
    source_id = create_source(database)
    first = store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"first original",
        original_filename="first.pdf",
    )
    second = store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"second original",
        original_filename="second.pdf",
    )
    second_file = paths.root / second.managed_path
    original_replace = os.replace

    def fail_second_move(source: Path, destination: Path) -> None:
        if Path(source) == second_file:
            raise OSError("simulated staging failure")
        original_replace(source, destination)

    monkeypatch.setattr("litrev.infrastructure.storage.os.replace", fail_second_move)

    with pytest.raises(OSError, match="simulated staging failure"):
        remove_source(database, source_id)

    with database.session() as session:
        assert session.get(SourceRecord, source_id) is not None
        assert session.get(AttachmentRecord, first.id) is not None
        assert session.get(AttachmentRecord, second.id) is not None
    assert (paths.root / first.managed_path).read_bytes() == b"first original"
    assert second_file.read_bytes() == b"second original"
    assert not list(paths.temporary_imports.iterdir())


def test_source_removal_database_failure_restores_all_files_and_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, database = create_library(tmp_path)
    source_id = create_source(database)
    with database.session() as session:
        source = session.get(SourceRecord, source_id)
        assert source is not None
        source.notes.append(NoteRecord(body="Must survive rollback"))
        source.tags.append(TagRecord(name="Evidence", normalized_name="evidence"))
        session.commit()
        note_id = source.notes[0].id
    attachment = store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"original",
        original_filename="paper.pdf",
    )
    extracted_path = ManagedExtractionStore(paths).put("# Extracted", attachment.checksum)
    with database.session() as session:
        stored = session.get(AttachmentRecord, attachment.id)
        assert stored is not None
        stored.extracted_path = extracted_path
        session.commit()
    original_session_factory = database.session

    def failing_session_factory():
        session = original_session_factory()

        def fail_commit() -> None:
            raise RuntimeError("simulated database failure")

        session.commit = fail_commit
        return session

    monkeypatch.setattr(database, "session", failing_session_factory)

    with pytest.raises(SourceRemovalDatabaseError) as caught:
        remove_source(database, source_id)

    assert isinstance(caught.value.__cause__, RuntimeError)
    monkeypatch.setattr(database, "session", original_session_factory)
    with database.session() as session:
        source = session.get(SourceRecord, source_id)
        assert source is not None
        assert session.get(AttachmentRecord, attachment.id) is not None
        assert session.get(NoteRecord, note_id) is not None
        assert [tag.name for tag in source.tags] == ["Evidence"]
    assert (paths.root / attachment.managed_path).read_bytes() == b"original"
    assert (paths.root / extracted_path).read_text(encoding="utf-8") == "# Extracted"
    assert not list(paths.temporary_imports.iterdir())


def test_source_removal_cleanup_failure_keeps_artifacts_staged_after_database_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, database = create_library(tmp_path)
    source_id = create_source(database)
    attachment = store_attachment_bytes(
        database,
        source_id=source_id,
        data=b"original",
        original_filename="paper.pdf",
    )
    original_unlink = Path.unlink

    def fail_staged_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path.name == "original" and path.parent.name.startswith("removal-"):
            raise OSError("simulated cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_staged_cleanup)

    with pytest.raises(ManagedFileCleanupError):
        remove_source(database, source_id)

    with database.session() as session:
        assert session.get(SourceRecord, source_id) is None
        assert session.get(AttachmentRecord, attachment.id) is None
    assert not (paths.root / attachment.managed_path).exists()
    assert [path for path in paths.temporary_imports.rglob("*") if path.is_file()]


def test_removing_an_unknown_source_reports_it(tmp_path: Path) -> None:
    _paths, database = create_library(tmp_path)

    with pytest.raises(SourceNotFoundError):
        remove_source(database, 999)
