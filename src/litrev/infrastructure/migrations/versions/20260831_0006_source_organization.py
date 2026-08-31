"""Add reusable tags and collections for source organization.

Revision ID: 20260831_0006
Revises: 20260831_0005
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0006"
down_revision: str | None = "20260831_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    existing_tables = set(sa.inspect(connection).get_table_names())

    if "tags" in existing_tables:
        _validate_named_table(connection, "tags")
    else:
        op.create_table(
            "tags",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("normalized_name", sa.String(length=300), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("normalized_name", name="uq_tags_normalized_name"),
        )

    if "collections" in existing_tables:
        _validate_named_table(connection, "collections")
    else:
        op.create_table(
            "collections",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("normalized_name", sa.String(length=300), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "normalized_name",
                name="uq_collections_normalized_name",
            ),
        )

    if "source_tags" in existing_tables:
        _validate_link_table(connection, "source_tags", "tag_id", "tags")
    else:
        op.create_table(
            "source_tags",
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("tag_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("source_id", "tag_id"),
        )

    if "source_collections" in existing_tables:
        _validate_link_table(
            connection,
            "source_collections",
            "collection_id",
            "collections",
        )
    else:
        op.create_table(
            "source_collections",
            sa.Column("source_id", sa.Integer(), nullable=False),
            sa.Column("collection_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["collection_id"],
                ["collections.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("source_id", "collection_id"),
        )


def _validate_named_table(connection: sa.Connection, table_name: str) -> None:
    schema = sa.inspect(connection)
    columns = {column["name"]: column for column in schema.get_columns(table_name)}
    expected_types = {
        "id": sa.Integer(),
        "name": sa.String(length=100),
        "normalized_name": sa.String(length=300),
    }
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
    unique_columns = {
        tuple(constraint["column_names"])
        for constraint in schema.get_unique_constraints(table_name)
    }
    if ("normalized_name",) not in unique_columns:
        raise _incompatible_table_error(table_name)


def _validate_link_table(
    connection: sa.Connection,
    table_name: str,
    organization_column: str,
    organization_table: str,
) -> None:
    schema = sa.inspect(connection)
    columns = {column["name"]: column for column in schema.get_columns(table_name)}
    expected_columns = {"source_id", organization_column}
    integer_type = sa.Integer().compile(dialect=connection.dialect).upper()
    if (
        set(columns) != expected_columns
        or any(column["nullable"] for column in columns.values())
        or any(column["default"] is not None for column in columns.values())
        or any(
            column["type"].compile(dialect=connection.dialect).upper() != integer_type
            for column in columns.values()
        )
    ):
        raise _incompatible_table_error(table_name)

    primary_key = set(schema.get_pk_constraint(table_name)["constrained_columns"])
    if primary_key != expected_columns:
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
    expected_foreign_keys = {
        (("source_id",), "sources", ("id",), "CASCADE"),
        ((organization_column,), organization_table, ("id",), "CASCADE"),
    }
    if foreign_keys != expected_foreign_keys:
        raise _incompatible_table_error(table_name)


def _incompatible_table_error(table_name: str) -> RuntimeError:
    return RuntimeError(
        f"Cannot resume migration {revision}: the existing {table_name!r} table does not match "
        "the expected source-organization schema."
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because dropping tags and collections would lose "
        "research organization. Restore a compatible backup instead."
    )
