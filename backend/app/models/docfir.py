from sqlalchemy import BigInteger, Column, String, Text, DateTime, Boolean, CheckConstraint, Index
from sqlalchemy.sql import func
from app.core.database import Base
from enum import Enum

class EstadoDoc(str, Enum):
    BORRADOR = "BORRADOR"
    PENDIENTE = "PENDIENTE"
    CANCELADO = "CANCELADO"
    ERROR = "ERROR"

class DocFir(Base):
    __tablename__ = 'docfir'

    docid = Column(BigInteger, primary_key=True, autoincrement=True)
    nodid = Column(String(64), nullable=False)
    docnom = Column(String(255), nullable=False)
    mimtip = Column(String(100), nullable=False)
    tamano = Column(BigInteger, nullable=False)
    verini = Column(String(20), nullable=False)
    verfin = Column(String(20), nullable=True)
    estado = Column(String(20), nullable=False)
    hasori = Column(String(64), nullable=False)
    hasfir = Column(String(64), nullable=True)
    usrcre = Column(String(60), nullable=False)
    feccre = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    usrmod = Column(String(60), nullable=True)
    fecmod = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
    activo = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("nodid != ''", name="ck_docfir_nodid_empty"),
        CheckConstraint("docnom != ''", name="ck_docfir_docnom_empty"),
        CheckConstraint("mimtip = 'application/pdf'", name="ck_docfir_mimtip_pdf"),
        CheckConstraint("tamano > 0", name="ck_docfir_tamano_positive"),
        CheckConstraint("verini != ''", name="ck_docfir_verini_empty"),
        CheckConstraint("hasori ~ '^[0-9a-fA-F]{64}$'", name="ck_docfir_hasori_hex"),
        CheckConstraint("hasfir IS NULL OR hasfir ~ '^[0-9a-fA-F]{64}$'", name="ck_docfir_hasfir_hex"),
        CheckConstraint("usrcre != ''", name="ck_docfir_usrcre_empty"),
        CheckConstraint(
            "estado IN ('BORRADOR', 'PENDIENTE', 'CANCELADO', 'ERROR')",
            name="ck_docfir_estado"
        ),
        Index(
            'ix_docfir_nodid_verini_unique',
            'nodid', 'verini',
            unique=True,
            postgresql_where=(activo == True) & (estado != 'CANCELADO')
        ),
    )
