"""Add editable bibliographic metadata to sources.

Revision ID: 20260831_0005
Revises: 20260830_0004
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0005"
down_revision: str | None = "20260830_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXPECTED_DEFAULTS = {
    "authors": "'[]'",
    "reading_status": "'unread'",
}


def upgrade() -> None:
    expected_columns = (
        sa.Column("authors", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("venue", sa.String(length=500), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=35), nullable=True),
        sa.Column("reading_status", sa.String(length=20), nullable=False, server_default="unread"),
    )
    connection = op.get_bind()
    existing_columns = {
        column["name"]: column for column in sa.inspect(connection).get_columns("sources")
    }

    for expected in expected_columns:
        existing = existing_columns.get(expected.name)
        if existing is None:
            continue

        actual_type = " ".join(existing["type"].compile(dialect=connection.dialect).upper().split())
        expected_type = " ".join(expected.type.compile(dialect=connection.dialect).upper().split())
        if (
            actual_type != expected_type
            or existing["nullable"] is not expected.nullable
            or existing["default"] != _EXPECTED_DEFAULTS.get(expected.name)
        ):
            raise RuntimeError(
                f"Cannot resume migration {revision}: the existing {expected.name!r} column "
                "does not match the expected source-metadata schema."
            )

    for expected in expected_columns:
        if expected.name not in existing_columns:
            op.add_column("sources", expected)


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because dropping source metadata would lose research "
        "data. Restore a compatible backup instead."
    )
