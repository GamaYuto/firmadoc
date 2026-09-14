from __future__ import annotations

from enum import Enum
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class EstadoSesionQr(str, Enum):
    PENDIENTE = "PENDIENTE"
    USADO = "USADO"
    EXPIRADO = "EXPIRADO"
    CANCELADO = "CANCELADO"


class SesionQr(Base):
    __tablename__ = "sesionqr"

    sesid = Column(BigInteger, primary_key=True, autoincrement=True)
    firid = Column(BigInteger, ForeignKey("docfirma.firid", ondelete="CASCADE"), nullable=False)
    docid = Column(BigInteger, ForeignKey("docfir.docid", ondelete="CASCADE"), nullable=False)
    usrid = Column(String(60), nullable=False)
    tokhas = Column(String(64), nullable=False)
    estado = Column(String(20), nullable=False, default=EstadoSesionQr.PENDIENTE.value)
    feccre = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    fecexp = Column(DateTime(timezone=True), nullable=False)
    fecusa = Column(DateTime(timezone=True), nullable=True)
    iporig = Column(String(45), nullable=True)

    firma = relationship("DocFirma")
    documento = relationship("DocFir")

    __table_args__ = (
        UniqueConstraint("tokhas", name="uq_sesionqr_tokhas"),
        CheckConstraint(
            "estado IN ('PENDIENTE', 'USADO', 'EXPIRADO', 'CANCELADO')",
            name="ck_sesionqr_estado",
        ),
        CheckConstraint("tokhas ~ '^[0-9a-f]{64}$'", name="ck_sesionqr_tokhas_hex"),
        Index("ix_sesionqr_firid_estado", "firid", "estado"),
    )
