from pathlib import Path

import pytest
from alembic import command
from alembic.operations import Operations
from sqlalchemy import Column, inspect, text
from sqlalchemy.exc import IntegrityError

from litrev.infrastructure.database import (
    Database,
    IncompatibleLegacySchemaError,
    _migration_config,
)
from litrev.infrastructure.models import AttachmentRecord, NoteRecord, SourceRecord
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

    assert revision == "20260830_0004"


def test_interrupted_conversion_result_migration_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database.from_path(tmp_path / "interrupted.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260830_0003")

    original_add_column = Operations.add_column

    def interrupt_after_first_conversion_column(
        operations: Operations,
        table_name: str,
        column: Column[object],
        *,
        schema: str | None = None,
        if_not_exists: bool | None = None,
        inline_references: bool | None = None,
        inline_primary_key: bool | None = None,
    ) -> None:
        original_add_column(
            operations,
            table_name,
            column,
            schema=schema,
            if_not_exists=if_not_exists,
            inline_references=inline_references,
            inline_primary_key=inline_primary_key,
        )
        if table_name == "attachments" and column.name == "extracted_path":
            raise RuntimeError("simulated migration interruption")

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(Operations, "add_column", interrupt_after_first_conversion_column)
        with pytest.raises(RuntimeError, match="simulated migration interruption"):
            database.migrate()

    columns_after_interruption = {
        column["name"] for column in inspect(database.engine).get_columns("attachments")
    }
    assert "extracted_path" in columns_after_interruption
    assert "conversion_message" not in columns_after_interruption
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260830_0003"

    database.migrate()

    columns_after_retry = {
        column["name"] for column in inspect(database.engine).get_columns("attachments")
    }
    assert {
        "extracted_path",
        "conversion_message",
        "conversion_diagnostics",
    } <= columns_after_retry
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260830_0004"


def test_conversion_result_migration_rejects_an_incompatible_partial_schema(
    tmp_path: Path,
) -> None:
    database = Database.from_path(tmp_path / "incompatible-interruption.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260830_0003")
        connection.exec_driver_sql(
            "ALTER TABLE attachments ADD COLUMN extracted_path TEXT NOT NULL DEFAULT ''"
        )

    with pytest.raises(RuntimeError, match="does not match the expected conversion-results schema"):
        database.migrate()

    columns = {column["name"] for column in inspect(database.engine).get_columns("attachments")}
    assert "conversion_message" not in columns
    assert "conversion_diagnostics" not in columns
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260830_0003"


def test_legacy_database_is_adopted_without_losing_records(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "legacy.sqlite3")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE sources (
                id INTEGER NOT NULL,
                title VARCHAR(500) NOT NULL,
                doi VARCHAR(255),
                created_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                UNIQUE (doi)
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE notes (
                id INTEGER NOT NULL PRIMARY KEY,
                source_id INTEGER NOT NULL,
                body TEXT NOT NULL,
                locator VARCHAR(100),
                created_at DATETIME NOT NULL,
                FOREIGN KEY(source_id) REFERENCES sources (id)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX ix_notes_source_id ON notes (source_id)")
        connection.execute(
            text(
                """
                INSERT INTO sources (id, title, doi, created_at)
                VALUES (1, 'Existing paper', '10.1234/existing', '2026-08-30 00:00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO notes (source_id, body, locator, created_at)
                VALUES (1, 'Existing note', 'p. 4', '2026-08-30 00:00:00')
                """
            )
        )

    database.migrate()

    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.title == "Existing paper"
        assert saved.source_type == "other"
        assert saved.doi == "10.1234/existing"
        assert saved.notes[0].body == "Existing note"
        session.add(SourceRecord(title="Duplicate DOI", doi="10.1234/existing"))
        with pytest.raises(IntegrityError):
            session.commit()
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert revision == "20260830_0004"
    assert AttachmentRecord.__tablename__ in inspect(database.engine).get_table_names()


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
        source = SourceRecord(
            source_type="book",
            title="A useful book",
            doi="10.1234/example",
        )
        source.notes.append(NoteRecord(body="Important finding", locator="p. 7"))
        session.add(source)
        session.commit()

    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.source_type == "book"
        assert saved.title == "A useful book"
        assert saved.notes[0].locator == "p. 7"


def test_orphan_note_is_rejected() -> None:
    database = Database.in_memory()
    database.migrate()

    with database.session() as session:
        session.add(NoteRecord(source_id=999, body="No source exists for this note"))

        with pytest.raises(IntegrityError):
            session.commit()
