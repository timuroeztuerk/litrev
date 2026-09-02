"""Generalize metadata provenance identifiers without losing DOI history.

Revision ID: 20260901_0009
Revises: 20260831_0008
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0009"
down_revision: str | None = "20260831_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "source_metadata_lookups"
_SOURCE_INDEX = "ix_source_metadata_lookups_source_id"


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"]: column for column in sa.inspect(connection).get_columns(_TABLE_NAME)}

    if "identifier_type" not in columns:
        op.add_column(
            _TABLE_NAME,
            sa.Column(
                "identifier_type",
                sa.String(length=50),
                nullable=False,
                server_default="doi",
            ),
        )
    if "requested_doi" in columns:
        op.alter_column(
            _TABLE_NAME,
            "requested_doi",
            existing_type=sa.String(length=255),
            new_column_name="requested_identifier",
        )
    if "retrieved_doi" in columns:
        op.alter_column(
            _TABLE_NAME,
            "retrieved_doi",
            existing_type=sa.String(length=255),
            new_column_name="retrieved_identifier",
        )

    columns = _validate_generic_lookup_table(connection, allow_identifier_default=True)
    identifier_types = set(
        connection.execute(sa.text(f"SELECT DISTINCT identifier_type FROM {_TABLE_NAME}")).scalars()
    )
    if identifier_types - {"doi"}:
        raise _incompatible_table_error()

    if columns["identifier_type"]["default"] is not None:
        with op.batch_alter_table(_TABLE_NAME) as batch:
            batch.alter_column(
                "identifier_type",
                existing_type=sa.String(length=50),
                nullable=False,
                server_default=None,
            )
    _validate_generic_lookup_table(connection, allow_identifier_default=False)


def _validate_generic_lookup_table(
    connection: sa.Connection,
    *,
    allow_identifier_default: bool,
) -> dict[str, dict[str, object]]:
    expected_columns: dict[str, tuple[sa.types.TypeEngine[object], bool]] = {
        "id": (sa.Integer(), False),
        "source_id": (sa.Integer(), False),
        "provider": (sa.String(length=50), False),
        "provider_url": (sa.String(length=2048), False),
        "identifier_type": (sa.String(length=50), False),
        "requested_identifier": (sa.String(length=255), False),
        "retrieved_identifier": (sa.String(length=255), False),
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
        default = columns[name]["default"]
        valid_identifier_default = (
            name == "identifier_type"
            and allow_identifier_default
            and default is not None
            and str(default).strip("'\"() ").casefold() == "doi"
        )
        if (
            actual_type != wanted_type
            or columns[name]["nullable"] is not nullable
            or (default is not None and not valid_identifier_default)
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
    indexes = {
        index["name"]: tuple(index["column_names"]) for index in schema.get_indexes(_TABLE_NAME)
    }
    if indexes.get(_SOURCE_INDEX) != ("source_id",):
        raise _incompatible_table_error()
    return columns


def _incompatible_table_error() -> RuntimeError:
    return RuntimeError(
        f"Cannot resume migration {revision}: the existing {_TABLE_NAME!r} table does not match "
        "the expected generic metadata provenance schema."
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because reverting generic provenance would lose "
        "ISBN lookup history. Restore a compatible backup instead."
    )
