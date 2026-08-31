from pathlib import Path

import pytest
from alembic import command
from alembic.operations import Operations
from sqlalchemy import Column, delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from litrev.domain.sources import ReadingStatus
from litrev.infrastructure.database import (
    Database,
    IncompatibleLegacySchemaError,
    _migration_config,
)
from litrev.infrastructure.models import (
    AttachmentRecord,
    CollectionRecord,
    NoteRecord,
    SourceCitationKeyRecord,
    SourceIdentifierRecord,
    SourceMetadataLookupRecord,
    SourceRecord,
    TagRecord,
)
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

    assert revision == "20260831_0008"


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
    assert revision == "20260831_0008"


def test_interrupted_source_metadata_migration_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database.from_path(tmp_path / "interrupted-metadata.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260830_0004")
        connection.exec_driver_sql(
            """
            INSERT INTO sources (title, doi, created_at, source_type)
            VALUES ('Existing source', NULL, '2026-08-30 00:00:00', 'paper')
            """
        )

    original_add_column = Operations.add_column

    def interrupt_after_authors_column(
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
        if table_name == "sources" and column.name == "authors":
            raise RuntimeError("simulated metadata migration interruption")

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(Operations, "add_column", interrupt_after_authors_column)
        with pytest.raises(RuntimeError, match="simulated metadata migration interruption"):
            database.migrate()

    columns_after_interruption = {
        column["name"] for column in inspect(database.engine).get_columns("sources")
    }
    assert "authors" in columns_after_interruption
    assert "publication_year" not in columns_after_interruption
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260830_0004"

    database.migrate()

    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.authors == []
        assert saved.reading_status == ReadingStatus.UNREAD.value
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260831_0008"


def test_interrupted_source_organization_migration_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database.from_path(tmp_path / "interrupted-organization.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260831_0005")

    original_create_table = Operations.create_table

    def interrupt_after_tags_table(
        operations: Operations,
        table_name: str,
        *columns: object,
        **kwargs: object,
    ) -> object:
        table = original_create_table(operations, table_name, *columns, **kwargs)
        if table_name == "tags":
            raise RuntimeError("simulated organization migration interruption")
        return table

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(Operations, "create_table", interrupt_after_tags_table)
        with pytest.raises(RuntimeError, match="simulated organization migration interruption"):
            database.migrate()

    assert "tags" in inspect(database.engine).get_table_names()
    assert "collections" not in inspect(database.engine).get_table_names()
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260831_0005"

    database.migrate()

    assert {
        "tags",
        "collections",
        "source_tags",
        "source_collections",
    } <= set(inspect(database.engine).get_table_names())
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260831_0008"


def test_source_identifier_migration_preserves_existing_sources(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "source-identifiers.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260831_0006")
        connection.exec_driver_sql(
            """
            INSERT INTO sources (title, created_at, source_type, authors, reading_status)
            VALUES ('Existing source', '2026-08-31 00:00:00', 'paper', '[]', 'unread')
            """
        )

    database.migrate()

    schema = inspect(database.engine)
    assert {"source_identifiers", "source_citation_keys"} <= set(schema.get_table_names())
    identifier_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in schema.get_unique_constraints("source_identifiers")
    }
    assert ("source_id", "identifier_type", "normalized_value") in identifier_unique_columns
    citation_key_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in schema.get_unique_constraints("source_citation_keys")
    }
    assert ("source_id", "bibliography_format", "value") in citation_key_unique_columns
    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.title == "Existing source"
        assert saved.identifiers == []
        assert saved.citation_keys == []


def test_interrupted_source_identifier_migration_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database.from_path(tmp_path / "interrupted-identifiers.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260831_0006")

    original_create_table = Operations.create_table

    def interrupt_after_identifier_table(
        operations: Operations,
        table_name: str,
        *columns: object,
        **kwargs: object,
    ) -> object:
        table = original_create_table(operations, table_name, *columns, **kwargs)
        if table_name == "source_identifiers":
            raise RuntimeError("simulated source-identifier migration interruption")
        return table

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(Operations, "create_table", interrupt_after_identifier_table)
        with pytest.raises(
            RuntimeError,
            match="simulated source-identifier migration interruption",
        ):
            database.migrate()

    assert "source_identifiers" in inspect(database.engine).get_table_names()
    assert "source_citation_keys" not in inspect(database.engine).get_table_names()
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260831_0006"

    database.migrate()

    assert {"source_identifiers", "source_citation_keys"} <= set(
        inspect(database.engine).get_table_names()
    )
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260831_0008"


def test_metadata_provenance_migration_preserves_existing_sources(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "metadata-provenance.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260831_0007")
        connection.exec_driver_sql(
            """
            INSERT INTO sources (title, doi, created_at, source_type, authors, reading_status)
            VALUES (
                'Existing source',
                '10.1234/existing',
                '2026-08-31 00:00:00',
                'paper',
                '[]',
                'unread'
            )
            """
        )

    database.migrate()

    schema = inspect(database.engine)
    assert "source_metadata_lookups" in schema.get_table_names()
    assert {column["name"] for column in schema.get_columns("source_metadata_lookups")} == {
        "id",
        "source_id",
        "provider",
        "provider_url",
        "requested_doi",
        "retrieved_doi",
        "reviewed_metadata",
        "proposed_metadata",
        "retrieved_at",
        "applied_fields",
        "applied_at",
    }
    assert ("source_id",) in {
        tuple(index["column_names"]) for index in schema.get_indexes("source_metadata_lookups")
    }
    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.title == "Existing source"
        assert saved.metadata_lookups == []


def test_interrupted_metadata_provenance_migration_can_be_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.from_path(tmp_path / "interrupted-metadata-provenance.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260831_0007")

    original_create_index = Operations.create_index

    def interrupt_before_source_index(
        operations: Operations,
        index_name: str,
        table_name: str,
        columns: list[str],
        **kwargs: object,
    ) -> None:
        if index_name == "ix_source_metadata_lookups_source_id":
            raise RuntimeError("simulated metadata-provenance migration interruption")
        original_create_index(operations, index_name, table_name, columns, **kwargs)

    with monkeypatch.context() as migration_patch:
        migration_patch.setattr(Operations, "create_index", interrupt_before_source_index)
        with pytest.raises(
            RuntimeError,
            match="simulated metadata-provenance migration interruption",
        ):
            database.migrate()

    assert "source_metadata_lookups" in inspect(database.engine).get_table_names()
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260831_0007"

    database.migrate()

    assert ("source_id",) in {
        tuple(index["column_names"])
        for index in inspect(database.engine).get_indexes("source_metadata_lookups")
    }
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260831_0008"


def test_source_organization_migration_rejects_an_incompatible_partial_schema(
    tmp_path: Path,
) -> None:
    database = Database.from_path(tmp_path / "incompatible-organization.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260831_0005")
        connection.exec_driver_sql(
            "CREATE TABLE tags (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL)"
        )

    with pytest.raises(RuntimeError, match="expected source-organization schema"):
        database.migrate()

    assert "collections" not in inspect(database.engine).get_table_names()
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260831_0005"


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
        assert saved.authors == []
        assert saved.publication_year is None
        assert saved.reading_status == ReadingStatus.UNREAD.value
        assert saved.notes[0].body == "Existing note"
        session.add(SourceRecord(title="Duplicate DOI", doi="10.1234/existing"))
        with pytest.raises(IntegrityError):
            session.commit()
    with database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert revision == "20260831_0008"
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
            authors=["Jane Researcher", "Research Collective"],
            publication_year=2024,
            venue="Evidence Press",
            doi="10.1234/example",
            url="https://example.org/book",
            abstract="A concise abstract.",
            language="en",
            reading_status=ReadingStatus.READING.value,
        )
        source.notes.append(NoteRecord(body="Important finding", locator="p. 7"))
        session.add(source)
        session.commit()

    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.source_type == "book"
        assert saved.title == "A useful book"
        assert saved.authors == ["Jane Researcher", "Research Collective"]
        assert saved.publication_year == 2024
        assert saved.venue == "Evidence Press"
        assert saved.url == "https://example.org/book"
        assert saved.abstract == "A concise abstract."
        assert saved.language == "en"
        assert saved.reading_status == ReadingStatus.READING.value
        assert saved.notes[0].locator == "p. 7"


def test_source_identifiers_and_citation_keys_are_source_owned() -> None:
    database = Database.in_memory()
    database.migrate()

    with database.session() as session:
        source = SourceRecord(
            title="Identified source",
            identifiers=[
                SourceIdentifierRecord(
                    identifier_type="pmid",
                    value="12345",
                    normalized_value="12345",
                )
            ],
            citation_keys=[
                SourceCitationKeyRecord(bibliography_format="bibtex", value="stable-key")
            ],
        )
        session.add(source)
        session.commit()
        source_id = source.id

    with database.session() as session:
        saved = session.get(SourceRecord, source_id)
        assert saved is not None
        assert [(item.identifier_type, item.value) for item in saved.identifiers] == [
            ("pmid", "12345")
        ]
        assert [(item.bibliography_format, item.value) for item in saved.citation_keys] == [
            ("bibtex", "stable-key")
        ]
        session.execute(delete(SourceRecord).where(SourceRecord.id == source_id))
        session.commit()

    with database.session() as session:
        assert session.scalar(select(SourceIdentifierRecord)) is None
        assert session.scalar(select(SourceCitationKeyRecord)) is None


def test_metadata_lookup_provenance_is_source_owned() -> None:
    database = Database.in_memory()
    database.migrate()

    with database.session() as session:
        source = SourceRecord(
            title="Looked-up source",
            doi="10.1234/example",
            metadata_lookups=[
                SourceMetadataLookupRecord(
                    provider="Crossref",
                    provider_url="https://api.crossref.org/works/10.1234%2Fexample",
                    requested_doi="10.1234/example",
                    retrieved_doi="10.1234/example",
                    reviewed_metadata={"title": "Looked-up source"},
                    proposed_metadata={"title": "Provider title"},
                    applied_fields=["title"],
                    applied_at=None,
                )
            ],
        )
        session.add(source)
        session.commit()
        source_id = source.id

    with database.session() as session:
        saved = session.get(SourceRecord, source_id)
        assert saved is not None
        assert saved.metadata_lookups[0].provider == "Crossref"
        session.execute(delete(SourceRecord).where(SourceRecord.id == source_id))
        session.commit()

    with database.session() as session:
        assert session.scalar(select(SourceMetadataLookupRecord)) is None


def test_tags_and_collections_are_reusable_persistent_source_relationships(tmp_path: Path) -> None:
    database_path = tmp_path / "organized-library.sqlite3"
    database = Database.from_path(database_path)
    database.migrate()

    with database.session() as session:
        methods = TagRecord(name="Methods", normalized_name="methods")
        thesis = CollectionRecord(name="Thesis", normalized_name="thesis")
        first = SourceRecord(title="First source", tags=[methods], collections=[thesis])
        second = SourceRecord(title="Second source", tags=[methods])
        session.add_all([first, second])
        session.commit()
        first_id = first.id

    database.engine.dispose()
    database = Database.from_path(database_path)
    database.migrate()

    with database.session() as session:
        saved = list(session.scalars(select(SourceRecord).order_by(SourceRecord.title)))
        assert [tag.name for tag in saved[0].tags] == ["Methods"]
        assert [collection.name for collection in saved[0].collections] == ["Thesis"]
        assert saved[0].tags[0].id == saved[1].tags[0].id

    with database.session() as session:
        session.execute(delete(SourceRecord).where(SourceRecord.id == first_id))
        session.commit()

    with database.session() as session:
        assert session.scalar(select(TagRecord.name)) == "Methods"
        assert session.scalar(select(CollectionRecord.name)) == "Thesis"
        remaining = session.scalar(select(SourceRecord))
        assert remaining is not None
        assert [tag.name for tag in remaining.tags] == ["Methods"]


def test_orphan_note_is_rejected() -> None:
    database = Database.in_memory()
    database.migrate()

    with database.session() as session:
        session.add(NoteRecord(source_id=999, body="No source exists for this note"))

        with pytest.raises(IntegrityError):
            session.commit()
