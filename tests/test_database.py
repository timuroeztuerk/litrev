from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from litrev.infrastructure.database import (
    Base,
    Database,
    IncompatibleLegacySchemaError,
)
from litrev.infrastructure.models import NoteRecord, SourceRecord
from litrev.infrastructure.storage import DATA_DIRECTORY_ENV, LibraryPaths


def test_library_layout_is_created_under_an_explicit_root(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)

    assert not paths.root.exists()

    database.migrate()

    assert database.path == paths.database
    assert paths.database.is_file()
    assert paths.attachments.is_dir()
    assert paths.extracted.is_dir()
    assert paths.thumbnails.is_dir()
    assert paths.temporary_imports.is_dir()


def test_configured_data_directory_does_not_consult_the_user_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_root = tmp_path / "isolated-library"
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(configured_root))

    def fail_if_called(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("The user data directory must not be used in this test")

    monkeypatch.setattr("litrev.infrastructure.storage.user_data_path", fail_if_called)

    paths = LibraryPaths.default()
    Database.from_library(paths).migrate()

    assert paths.root == configured_root.resolve()
    assert paths.database.is_file()


def test_migration_records_the_current_revision() -> None:
    database = Database.in_memory()
    database.migrate()
    database.migrate()

    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert revision == "20260830_0001"


def test_legacy_database_is_adopted_without_losing_records(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "legacy.sqlite3")
    Base.metadata.create_all(database.engine)

    with database.session() as session:
        source = SourceRecord(title="Existing paper", doi="10.1234/existing")
        source.notes.append(NoteRecord(body="Existing note", locator="p. 4"))
        session.add(source)
        session.commit()

    database.migrate()

    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.title == "Existing paper"
        assert saved.notes[0].body == "Existing note"
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert revision == "20260830_0001"


def test_partial_legacy_schema_is_rejected_without_stamping(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "partial.sqlite3")
    SourceRecord.__table__.create(database.engine)

    with pytest.raises(IncompatibleLegacySchemaError, match="only part"):
        database.migrate()

    assert "alembic_version" not in inspect(database.engine).get_table_names()


def test_source_and_linked_note_are_persisted() -> None:
    database = Database.in_memory()
    database.migrate()

    with database.session() as session:
        source = SourceRecord(title="A useful paper", doi="10.1234/example")
        source.notes.append(NoteRecord(body="Important finding", locator="p. 7"))
        session.add(source)
        session.commit()

    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.title == "A useful paper"
        assert saved.notes[0].locator == "p. 7"


def test_orphan_note_is_rejected() -> None:
    database = Database.in_memory()
    database.migrate()

    with database.session() as session:
        session.add(NoteRecord(source_id=999, body="No source exists for this note"))

        with pytest.raises(IntegrityError):
            session.commit()
