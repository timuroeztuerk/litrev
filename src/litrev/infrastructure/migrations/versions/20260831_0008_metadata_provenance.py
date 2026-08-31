"""Preserve DOI metadata lookup reviews and provenance.

Revision ID: 20260831_0008
Revises: 20260831_0007
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0008"
down_revision: str | None = "20260831_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "source_metadata_lookups"
_SOURCE_INDEX = "ix_source_metadata_lookups_source_id"


def upgrade() -> None:
    connection = op.get_bind()
    schema = sa.inspect(connection)
    if _TABLE_NAME in schema.get_table_names():
        _validate_lookup_table(connection)
    else:
        op.create_table(
            _TABLE_NAME,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("provider_url", sa.String(length=2048), nullable=False),
            sa.Column("requested_doi", sa.String(length=255), nullable=False),
            sa.Column("retrieved_doi", sa.String(length=255), nullable=False),
            sa.Column("reviewed_metadata", sa.JSON(), nullable=False),
            sa.Column("proposed_metadata", sa.JSON(), nullable=False),
            sa.Column("retrieved_at", sa.DateTime(), nullable=False),
            sa.Column("applied_fields", sa.JSON(), nullable=True),
            sa.Column("applied_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in sa.inspect(connection).get_indexes(_TABLE_NAME)
    }
    existing_index = indexes.get(_SOURCE_INDEX)
    if existing_index is None:
        op.create_index(_SOURCE_INDEX, _TABLE_NAME, ["source_id"], unique=False)
    elif existing_index != ("source_id",):
        raise _incompatible_table_error()


def _validate_lookup_table(connection: sa.Connection) -> None:
    expected_columns: dict[str, tuple[sa.types.TypeEngine[object], bool]] = {
        "id": (sa.Integer(), False),
        "source_id": (sa.Integer(), False),
        "provider": (sa.String(length=50), False),
        "provider_url": (sa.String(length=2048), False),
        "requested_doi": (sa.String(length=255), False),
        "retrieved_doi": (sa.String(length=255), False),
        "reviewed_metadata": (sa.JSON(), False),
        "proposed_metadata": (sa.JSON(), False),
        "retrieved_at": (sa.DateTime(), False),
        "applied_fields": (sa.JSON(), True),
        "applied_at": (sa.DateTime(), True),
    }
    schema = sa.inspect(connection)
    columns = {column["name"]: column for column in schema.get_columns(_TABLE_NAME)}
    if set(columns) != set(expected_columns):
        raise _incompatible_table_error()

    for name, (expected_type, nullable) in expected_columns.items():
        actual_type = " ".join(
            columns[name]["type"].compile(dialect=connection.dialect).upper().split()
        )
        wanted_type = " ".join(expected_type.compile(dialect=connection.dialect).upper().split())
        if (
            actual_type != wanted_type
            or columns[name]["nullable"] is not nullable
            or columns[name]["default"] is not None
        ):
            raise _incompatible_table_error()

    if schema.get_pk_constraint(_TABLE_NAME)["constrained_columns"] != ["id"]:
        raise _incompatible_table_error()
    foreign_keys = {
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
            foreign_key["options"].get("ondelete"),
        )
        for foreign_key in schema.get_foreign_keys(_TABLE_NAME)
    }
    if foreign_keys != {(("source_id",), "sources", ("id",), "CASCADE")}:
        raise _incompatible_table_error()


def _incompatible_table_error() -> RuntimeError:
    return RuntimeError(
        f"Cannot resume migration {revision}: the existing {_TABLE_NAME!r} table does not match "
        "the expected DOI metadata provenance schema."
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because dropping DOI lookup provenance would lose "
        "research metadata. Restore a compatible backup instead."
    )
