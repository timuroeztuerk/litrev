"""Preserve source identifiers and imported citation keys.

Revision ID: 20260831_0007
Revises: 20260831_0006
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0007"
down_revision: str | None = "20260831_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    existing_tables = set(sa.inspect(connection).get_table_names())

    if "source_identifiers" in existing_tables:
        _validate_source_owned_table(
            connection,
            "source_identifiers",
            {
                "id": sa.Integer(),
                "source_id": sa.Integer(),
                "identifier_type": sa.String(length=50),
                "value": sa.String(length=500),
                "normalized_value": sa.String(length=500),
            },
            ("source_id", "identifier_type", "normalized_value"),
        )
    else:
        op.create_table(
            "source_identifiers",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("identifier_type", sa.String(length=50), nullable=False),
            sa.Column("value", sa.String(length=500), nullable=False),
            sa.Column("normalized_value", sa.String(length=500), nullable=False),
            sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_id",
                "identifier_type",
                "normalized_value",
                name="uq_source_identifiers_source_type_value",
            ),
        )

    if "source_citation_keys" in existing_tables:
        _validate_source_owned_table(
            connection,
            "source_citation_keys",
            {
                "id": sa.Integer(),
                "source_id": sa.Integer(),
                "bibliography_format": sa.String(length=20),
                "value": sa.String(length=500),
            },
            ("source_id", "bibliography_format", "value"),
        )
    else:
        op.create_table(
            "source_citation_keys",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("bibliography_format", sa.String(length=20), nullable=False),
            sa.Column("value", sa.String(length=500), nullable=False),
            sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_id",
                "bibliography_format",
                "value",
                name="uq_source_citation_keys_source_format_value",
            ),
        )


def _validate_source_owned_table(
    connection: sa.Connection,
    table_name: str,
    expected_types: dict[str, sa.types.TypeEngine[object]],
    unique_columns: tuple[str, ...],
) -> None:
    schema = sa.inspect(connection)
    columns = {column["name"]: column for column in schema.get_columns(table_name)}
    if set(columns) != set(expected_types):
        raise _incompatible_table_error(table_name)

    for name, expected_type in expected_types.items():
        actual_type = " ".join(
            columns[name]["type"].compile(dialect=connection.dialect).upper().split()
        )
        wanted_type = " ".join(expected_type.compile(dialect=connection.dialect).upper().split())
        if (
            actual_type != wanted_type
            or columns[name]["nullable"]
            or columns[name]["default"] is not None
        ):
            raise _incompatible_table_error(table_name)

    if schema.get_pk_constraint(table_name)["constrained_columns"] != ["id"]:
        raise _incompatible_table_error(table_name)
    unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in schema.get_unique_constraints(table_name)
    }
    if unique_columns not in unique_constraints:
        raise _incompatible_table_error(table_name)
    foreign_keys = {
        (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
            foreign_key["options"].get("ondelete"),
        )
        for foreign_key in schema.get_foreign_keys(table_name)
    }
    if foreign_keys != {(("source_id",), "sources", ("id",), "CASCADE")}:
        raise _incompatible_table_error(table_name)


def _incompatible_table_error(table_name: str) -> RuntimeError:
    return RuntimeError(
        f"Cannot resume migration {revision}: the existing {table_name!r} table does not match "
        "the expected source-identifier schema."
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because dropping identifiers or citation keys would "
        "lose research metadata. Restore a compatible backup instead."
    )
