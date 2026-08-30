from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session

from litrev.infrastructure.storage import LibraryPaths

INITIAL_REVISION = "20260830_0001"
_LEGACY_TABLE_COLUMNS = {
    "sources": {"id", "title", "doi", "created_at"},
    "notes": {"id", "source_id", "body", "locator", "created_at"},
}


class Base(DeclarativeBase):
    pass


class IncompatibleLegacySchemaError(RuntimeError):
    pass


class Database:
    def __init__(
        self,
        engine: Engine,
        path: Path | None = None,
        library_paths: LibraryPaths | None = None,
    ) -> None:
        self.engine = engine
        self.path = path
        self.library_paths = library_paths

    @classmethod
    def from_path(cls, path: Path) -> Database:
        return cls(_create_sqlite_engine(f"sqlite:///{path}"), path)

    @classmethod
    def from_library(cls, paths: LibraryPaths) -> Database:
        return cls(_create_sqlite_engine(f"sqlite:///{paths.database}"), paths.database, paths)

    @classmethod
    def in_memory(cls) -> Database:
        return cls(_create_sqlite_engine("sqlite:///:memory:"))

    def migrate(self) -> None:
        if self.library_paths is not None:
            self.library_paths.ensure_exists()
        elif self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

        configuration = _migration_config()
        with self.engine.begin() as connection:
            configuration.attributes["connection"] = connection
            if _is_unversioned_legacy_database(connection):
                command.stamp(configuration, INITIAL_REVISION)
            command.upgrade(configuration, "head")

    def session(self) -> Session:
        return Session(self.engine)


def _create_sqlite_engine(url: str) -> Engine:
    engine = create_engine(url)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()


def _migration_config() -> Config:
    configuration = Config()
    migrations = Path(__file__).with_name("migrations")
    configuration.set_main_option("script_location", str(migrations))
    return configuration


def _is_unversioned_legacy_database(connection: Connection) -> bool:
    schema = inspect(connection)
    table_names = set(schema.get_table_names())
    if "alembic_version" in table_names:
        return False

    legacy_tables = table_names.intersection(_LEGACY_TABLE_COLUMNS)
    if not legacy_tables:
        return False
    if legacy_tables != set(_LEGACY_TABLE_COLUMNS):
        raise IncompatibleLegacySchemaError(
            "The existing Litrev database has only part of the legacy schema; restore a backup "
            "before trying the upgrade again."
        )

    for table_name, expected_columns in _LEGACY_TABLE_COLUMNS.items():
        actual_columns = {column["name"] for column in schema.get_columns(table_name)}
        if actual_columns != expected_columns:
            raise IncompatibleLegacySchemaError(
                f"The existing {table_name!r} table does not match Litrev's legacy schema; "
                "restore a backup before trying the upgrade again."
            )

    source_unique_columns = {
        tuple(constraint["column_names"]) for constraint in schema.get_unique_constraints("sources")
    }
    note_index_columns = {tuple(index["column_names"]) for index in schema.get_indexes("notes")}
    note_foreign_keys = {
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
        )
        for foreign_key in schema.get_foreign_keys("notes")
    }
    if ("doi",) not in source_unique_columns:
        raise IncompatibleLegacySchemaError(
            "The legacy sources table is missing its DOI constraint."
        )
    if ("source_id",) not in note_index_columns:
        raise IncompatibleLegacySchemaError("The legacy notes table is missing its source index.")
    if (("source_id",), "sources", ("id",)) not in note_foreign_keys:
        raise IncompatibleLegacySchemaError(
            "The legacy notes table is missing its source relationship."
        )

    return True
