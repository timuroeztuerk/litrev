"""Persist attachment conversion results.

Revision ID: 20260830_0004
Revises: 20260830_0003
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0004"
down_revision: str | None = "20260830_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    expected_columns = (
        sa.Column("extracted_path", sa.String(length=255), nullable=True),
        sa.Column("conversion_message", sa.Text(), nullable=True),
        sa.Column("conversion_diagnostics", sa.JSON(), nullable=True),
    )
    connection = op.get_bind()
    existing_columns = {
        column["name"]: column for column in sa.inspect(connection).get_columns("attachments")
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
            or existing["default"] is not None
        ):
            raise RuntimeError(
                f"Cannot resume migration {revision}: the existing {expected.name!r} column "
                "does not match the expected conversion-results schema."
            )

    for expected in expected_columns:
        if expected.name not in existing_columns:
            op.add_column("attachments", expected)


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because dropping conversion results could lose "
        "research data. Restore a compatible backup instead."
    )
