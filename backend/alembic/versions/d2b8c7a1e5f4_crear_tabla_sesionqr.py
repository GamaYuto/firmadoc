"""Crear tabla sesionqr

Revision ID: d2b8c7a1e5f4
Revises: c1a9b8a7f2d3
Create Date: 2026-09-14 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2b8c7a1e5f4"
down_revision: Union[str, Sequence[str], None] = "c1a9b8a7f2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sesionqr",
        sa.Column("sesid", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("firid", sa.BigInteger(), nullable=False),
        sa.Column("docid", sa.BigInteger(), nullable=False),
        sa.Column("usrid", sa.String(length=60), nullable=False),
        sa.Column("tokhas", sa.String(length=64), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False),
        sa.Column("feccre", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("fecexp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fecusa", sa.DateTime(timezone=True), nullable=True),
        sa.Column("iporig", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(["docid"], ["docfir.docid"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["firid"], ["docfirma.firid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("sesid"),
        sa.UniqueConstraint("tokhas", name="uq_sesionqr_tokhas"),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE', 'USADO', 'EXPIRADO', 'CANCELADO')",
            name="ck_sesionqr_estado",
        ),
        sa.CheckConstraint("tokhas ~ '^[0-9a-f]{64}$'", name="ck_sesionqr_tokhas_hex"),
    )
    op.create_index("ix_sesionqr_firid_estado", "sesionqr", ["firid", "estado"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sesionqr_firid_estado", table_name="sesionqr")
    op.drop_table("sesionqr")
