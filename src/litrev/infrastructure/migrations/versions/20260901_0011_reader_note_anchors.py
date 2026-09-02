"""Add structured Reader anchors to shared notes.

Revision ID: 20260901_0011
Revises: 20260901_0010
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0011"
down_revision: str | None = "20260901_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "notes"
_ATTACHMENT_INDEX = "ix_notes_attachment_id"
_HIGHLIGHT_INDEX = "ix_notes_highlight_id"
_ATTACHMENT_FOREIGN_KEY = "fk_notes_attachment_id_attachments"
_HIGHLIGHT_FOREIGN_KEY = "fk_notes_highlight_id_highlights"
_EXPECTED_READER_FOREIGN_KEYS = {
    ("attachment_id", "attachments", "id", "RESTRICT"),
    ("highlight_id", "highlights", "id", "SET NULL"),
}


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"]: column for column in sa.inspect(connection).get_columns(_TABLE_NAME)}

    if "attachment_id" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE notes ADD COLUMN attachment_id INTEGER "
            "REFERENCES attachments(id) ON DELETE RESTRICT"
        )
    if "page_number" not in columns:
        op.add_column(
            _TABLE_NAME,
            sa.Column("page_number", sa.Integer(), nullable=True),
        )
    if "highlight_id" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE notes ADD COLUMN highlight_id INTEGER "
            "REFERENCES highlights(id) ON DELETE SET NULL"
        )

    _validate_reader_note_column_shapes(connection)
    _repair_missing_reader_note_foreign_keys(connection)
    _validate_reader_note_foreign_keys(connection)
    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in sa.inspect(connection).get_indexes(_TABLE_NAME)
    }
    expected_indexes = {
        _ATTACHMENT_INDEX: ("attachment_id",),
        _HIGHLIGHT_INDEX: ("highlight_id",),
    }
    for index_name, expected_columns in expected_indexes.items():
        actual_columns = indexes.get(index_name)
        if actual_columns is None:
            op.create_index(index_name, _TABLE_NAME, list(expected_columns), unique=False)
        elif actual_columns != expected_columns:
            raise _incompatible_note_table_error()


def _validate_reader_note_column_shapes(connection: sa.Connection) -> None:
    schema = sa.inspect(connection)
    columns = {column["name"]: column for column in schema.get_columns(_TABLE_NAME)}
    expected_types: dict[str, sa.types.TypeEngine[object]] = {
        "attachment_id": sa.Integer(),
        "page_number": sa.Integer(),
        "highlight_id": sa.Integer(),
    }
    for name, expected_type in expected_types.items():
        column = columns.get(name)
        if column is None:
            raise _incompatible_note_table_error()
        actual_type = " ".join(column["type"].compile(dialect=connection.dialect).upper().split())
        wanted_type = " ".join(expected_type.compile(dialect=connection.dialect).upper().split())
        if actual_type != wanted_type or not column["nullable"] or column["default"] is not None:
            raise _incompatible_note_table_error()


def _repair_missing_reader_note_foreign_keys(connection: sa.Connection) -> None:
    reader_foreign_keys = _reader_note_foreign_keys(connection)
    if reader_foreign_keys == _EXPECTED_READER_FOREIGN_KEYS:
        return
    if not reader_foreign_keys <= _EXPECTED_READER_FOREIGN_KEYS:
        raise _incompatible_note_table_error()

    # SQLite can persist added columns before Alembic stamps the revision if a reloading service
    # interrupts the migration. Recreate only this exact recoverable shape so the rows survive and
    # the missing foreign keys become enforceable.
    with op.batch_alter_table(_TABLE_NAME, recreate="always") as batch:
        if not any(foreign_key[0] == "attachment_id" for foreign_key in reader_foreign_keys):
            batch.create_foreign_key(
                _ATTACHMENT_FOREIGN_KEY,
                "attachments",
                ["attachment_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        if not any(foreign_key[0] == "highlight_id" for foreign_key in reader_foreign_keys):
            batch.create_foreign_key(
                _HIGHLIGHT_FOREIGN_KEY,
                "highlights",
                ["highlight_id"],
                ["id"],
                ondelete="SET NULL",
            )


def _validate_reader_note_foreign_keys(connection: sa.Connection) -> None:
    if _reader_note_foreign_keys(connection) != _EXPECTED_READER_FOREIGN_KEYS:
        raise _incompatible_note_table_error()


def _reader_note_foreign_keys(connection: sa.Connection) -> set[tuple[str, str, str, str]]:
    return {
        (row["from"], row["table"], row["to"], row["on_delete"])
        for row in connection.exec_driver_sql("PRAGMA foreign_key_list(notes)").mappings()
        if row["from"] in {"attachment_id", "highlight_id"}
    }


def _incompatible_note_table_error() -> RuntimeError:
    return RuntimeError(
        f"Cannot resume migration {revision}: the existing {_TABLE_NAME!r} table does not match "
        "the expected structured Reader-note schema."
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because dropping Reader note anchors would lose "
        "research locators. Restore a compatible backup instead."
    )
