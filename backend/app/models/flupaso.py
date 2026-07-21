from sqlalchemy import BigInteger, Column, String, Integer, Boolean, DateTime, CheckConstraint, Index, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.core.database import Base
from typing import Any, Dict

class Flupaso(Base):
    __tablename__ = 'flupaso'

    pasid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fluid: Mapped[int] = mapped_column(BigInteger, ForeignKey('flujodoc.fluid', ondelete='RESTRICT'), nullable=False)
    pascod: Mapped[str] = mapped_column(String(30), nullable=False)
    pasnom: Mapped[str] = mapped_column(String(100), nullable=False)
    pastip: Mapped[str] = mapped_column(String(20), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    rolreq: Mapped[str] = mapped_column(String(40), nullable=False)
    obliga: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    plazo: Mapped[int] = mapped_column(Integer, nullable=True)
    config: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    usrcre: Mapped[str] = mapped_column(String(60), nullable=False)
    feccre: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    usrmod: Mapped[str] = mapped_column(String(60), nullable=True)
    fecmod: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    flujo = relationship("Flujodoc", back_populates="pasos")
    ejecuciones: Mapped[list["DocPaso"]] = relationship("DocPaso", back_populates="paso_definicion")

    __table_args__ = (
        CheckConstraint("pascod != ''", name="ck_flupaso_pascod_empty"),
        CheckConstraint("pasnom != ''", name="ck_flupaso_pasnom_empty"),
        CheckConstraint(
            "pastip IN ('DILIGENCIAR', 'REVISAR', 'APROBAR', 'FIRMAR', 'ATESTIGUAR', 'CERRAR', 'PUBLICAR')",
            name="ck_flupaso_pastip"
        ),
        CheckConstraint("orden > 0", name="ck_flupaso_orden_positive"),
        CheckConstraint("rolreq != ''", name="ck_flupaso_rolreq_empty"),
        CheckConstraint("plazo IS NULL OR plazo > 0", name="ck_flupaso_plazo_positive"),
        Index("uq_flupaso_fluid_pascod", "fluid", "pascod", unique=True),
        Index("uq_flupaso_fluid_orden", "fluid", "orden", unique=True),
        Index("idx_flupaso_fluid_activo", "fluid", "activo"),
    )
