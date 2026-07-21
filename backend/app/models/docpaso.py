from sqlalchemy import BigInteger, Column, String, Integer, Boolean, DateTime, CheckConstraint, Index, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.core.database import Base
from typing import Any, Dict
from enum import Enum

class EstadoDocPaso(str, Enum):
    PENDIENTE = "PENDIENTE"
    DISPONIBLE = "DISPONIBLE"
    EN_PROCESO = "EN_PROCESO"
    COMPLETADO = "COMPLETADO"
    RECHAZADO = "RECHAZADO"
    OMITIDO = "OMITIDO"
    CANCELADO = "CANCELADO"
    VENCIDO = "VENCIDO"

class DocPaso(Base):
    __tablename__ = 'docpaso'

    dpasid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    docid: Mapped[int] = mapped_column(BigInteger, ForeignKey('docfir.docid', ondelete='RESTRICT'), nullable=False)
    pasid: Mapped[int] = mapped_column(BigInteger, ForeignKey('flupaso.pasid', ondelete='RESTRICT'), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    
    # Atributos de fechas
    fecdis: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    fecini: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    feclim: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    fecfin: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Resultado y motivo
    result: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=True)
    motivo: Mapped[str] = mapped_column(String(500), nullable=True)
    
    # Atributos congelados desde flupaso
    pastip: Mapped[str] = mapped_column(String(20), nullable=False)
    rolreq: Mapped[str] = mapped_column(String(40), nullable=False)
    obliga: Mapped[bool] = mapped_column(Boolean, nullable=False)
    plazo: Mapped[int] = mapped_column(Integer, nullable=True)
    config: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=True)

    # Control optimista y auditoría
    verlock: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    usrcre: Mapped[str] = mapped_column(String(60), nullable=False)
    feccre: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    usrmod: Mapped[str] = mapped_column(String(60), nullable=True)
    fecmod: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # Relaciones
    documento = relationship("DocFir", back_populates="docpasos")
    paso_definicion = relationship("Flupaso", back_populates="ejecuciones")

    __table_args__ = (
        CheckConstraint("orden > 0", name="ck_docpaso_orden_positive"),
        CheckConstraint("verlock > 0", name="ck_docpaso_verlock_positive"),
        CheckConstraint("usrcre != ''", name="ck_docpaso_usrcre_empty"),
        CheckConstraint("pastip IN ('DILIGENCIAR', 'REVISAR', 'APROBAR', 'FIRMAR', 'ATESTIGUAR', 'CERRAR', 'PUBLICAR')", name="ck_docpaso_pastip"),
        CheckConstraint("rolreq != ''", name="ck_docpaso_rolreq_empty"),
        CheckConstraint("plazo IS NULL OR plazo > 0", name="ck_docpaso_plazo_positive"),
        CheckConstraint(
            "estado IN ('PENDIENTE', 'DISPONIBLE', 'EN_PROCESO', 'COMPLETADO', 'RECHAZADO', 'OMITIDO', 'CANCELADO', 'VENCIDO')",
            name="ck_docpaso_estado"
        ),
        # Reglas temporales
        CheckConstraint(
            "(estado IN ('EN_PROCESO', 'COMPLETADO', 'RECHAZADO', 'OMITIDO', 'CANCELADO', 'VENCIDO') AND fecini IS NOT NULL) OR (estado NOT IN ('EN_PROCESO', 'COMPLETADO', 'RECHAZADO', 'OMITIDO', 'CANCELADO', 'VENCIDO'))",
            name="ck_docpaso_fecini_req"
        ),
        CheckConstraint(
            "(estado IN ('COMPLETADO', 'RECHAZADO', 'OMITIDO', 'CANCELADO') AND fecfin IS NOT NULL) OR (estado NOT IN ('COMPLETADO', 'RECHAZADO', 'OMITIDO', 'CANCELADO'))",
            name="ck_docpaso_fecfin_req"
        ),
        CheckConstraint("fecfin IS NULL OR fecini IS NULL OR fecfin >= fecini", name="ck_docpaso_fecfin_ge_fecini"),
        CheckConstraint("feclim IS NULL OR fecdis IS NULL OR feclim >= fecdis", name="ck_docpaso_feclim_ge_fecdis"),
        # Motivo obligatorio y sin espacios únicamente
        CheckConstraint(
            "(estado IN ('RECHAZADO', 'OMITIDO', 'CANCELADO') AND motivo IS NOT NULL AND TRIM(motivo) != '') OR (estado NOT IN ('RECHAZADO', 'OMITIDO', 'CANCELADO'))",
            name="ck_docpaso_motivo_req"
        ),
        CheckConstraint("motivo IS NULL OR TRIM(motivo) != ''", name="ck_docpaso_motivo_no_empty"),
        # Únicos
        Index("uq_docpaso_docid_pasid", "docid", "pasid", unique=True),
        Index("uq_docpaso_docid_orden", "docid", "orden", unique=True),
    )
