from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SsoReplay(Base):
    __tablename__ = "ssojti"

    jti_hash: Mapped[str] = mapped_column("jtihas", String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column("fecexp", DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime] = mapped_column("fecuse", DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_ssojti_fecexp", "fecexp"),
    )
