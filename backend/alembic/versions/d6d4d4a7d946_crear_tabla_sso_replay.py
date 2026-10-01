"""crear_tabla_ssojti

Revision ID: d6d4d4a7d946
Revises: d2b8c7a1e5f4
Create Date: 2026-10-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d6d4d4a7d946"
down_revision: Union[str, Sequence[str], None] = "d2b8c7a1e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ssojti",
        sa.Column("jtihas", sa.String(length=64), nullable=False),
        sa.Column("fecexp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecuse", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("jtihas"),
    )
    op.create_index("ix_ssojti_fecexp", "ssojti", ["fecexp"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ssojti_fecexp", table_name="ssojti")
    op.drop_table("ssojti")
