"""Add recoverable page-aware PDF highlights.

Revision ID: 20260901_0010
Revises: 20260901_0009
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0010"
down_revision: str | None = "20260901_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "highlights"
_ATTACHMENT_INDEX = "ix_highlights_attachment_id"


def upgrade() -> None:
    connection = op.get_bind()
    schema = sa.inspect(connection)
    if _TABLE_NAME in schema.get_table_names():
        _validate_highlight_table(connection)
    else:
        op.create_table(
            _TABLE_NAME,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("attachment_id", sa.Integer(), nullable=False),
            sa.Column("page_number", sa.Integer(), nullable=False),
            sa.Column("selected_text", sa.Text(), nullable=False),
            sa.Column("rectangles", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.CheckConstraint(
                "page_number >= 1",
                name="ck_highlights_page_number_positive",
            ),
            sa.CheckConstraint(
                "length(selected_text) BETWEEN 1 AND 10000",
                name="ck_highlights_selected_text_length",
            ),
            sa.ForeignKeyConstraint(
                ["attachment_id"],
                ["attachments.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in sa.inspect(connection).get_indexes(_TABLE_NAME)
    }
    existing_index = indexes.get(_ATTACHMENT_INDEX)
    if existing_index is None:
        op.create_index(_ATTACHMENT_INDEX, _TABLE_NAME, ["attachment_id"], unique=False)
    elif existing_index != ("attachment_id",):
        raise _incompatible_table_error()


def _validate_highlight_table(connection: sa.Connection) -> None:
    expected_columns: dict[str, sa.types.TypeEngine[object]] = {
        "id": sa.Integer(),
        "attachment_id": sa.Integer(),
        "page_number": sa.Integer(),
        "selected_text": sa.Text(),
        "rectangles": sa.JSON(),
        "created_at": sa.DateTime(),
    }
    schema = sa.inspect(connection)
    columns = {column["name"]: column for column in schema.get_columns(_TABLE_NAME)}
    if set(columns) != set(expected_columns):
        raise _incompatible_table_error()

    for name, expected_type in expected_columns.items():
        actual_type = " ".join(
            columns[name]["type"].compile(dialect=connection.dialect).upper().split()
        )
        wanted_type = " ".join(expected_type.compile(dialect=connection.dialect).upper().split())
        if (
            actual_type != wanted_type
            or columns[name]["nullable"]
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
    if foreign_keys != {(("attachment_id",), "attachments", ("id",), "CASCADE")}:
        raise _incompatible_table_error()
    check_constraints = {
        constraint["name"]: "".join(str(constraint["sqltext"]).split()).casefold()
        for constraint in schema.get_check_constraints(_TABLE_NAME)
    }
    if check_constraints != {
        "ck_highlights_page_number_positive": "page_number>=1",
        "ck_highlights_selected_text_length": "length(selected_text)between1and10000",
    }:
        raise _incompatible_table_error()


def _incompatible_table_error() -> RuntimeError:
    return RuntimeError(
        f"Cannot resume migration {revision}: the existing {_TABLE_NAME!r} table does not match "
        "the expected page-highlight schema."
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because dropping highlights would lose research "
        "annotations. Restore a compatible backup instead."
    )
