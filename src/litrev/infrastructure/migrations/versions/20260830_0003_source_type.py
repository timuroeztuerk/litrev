"""Record whether a source is a book or paper.

Revision ID: 20260830_0003
Revises: 20260830_0002
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0003"
down_revision: str | None = "20260830_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "source_type",
            sa.String(length=20),
            nullable=False,
            server_default="other",
        ),
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Litrev migrations are forward-only because changing the source schema in place could "
        "damage a research library. Restore a compatible backup instead."
    )
