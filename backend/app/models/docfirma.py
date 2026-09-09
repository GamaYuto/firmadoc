from sqlalchemy import BigInteger, Column, Integer, String, CheckConstraint, UniqueConstraint, Index, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.sql import func
from app.core.database import Base
from enum import Enum
import uuid

class EstadoDocFirma(str, Enum):
    INICIADA = "INICIADA"
    GENERADA = "GENERADA"
    SUBIENDO = "SUBIENDO"
    CARGADA = "CARGADA"
    VERIFICANDO = "VERIFICANDO"
    COMPLETADA = "COMPLETADA"
    CONFLICTO = "CONFLICTO"
    FALLIDA = "FALLIDA"
    CANCELADA = "CANCELADA"

class TipoFirma(str, Enum):
    MANUSCRITA = "MANUSCRITA"
    INTERNA = "INTERNA"

class DocFirma(Base):
    __tablename__ = 'docfirma'

    firid = Column(BigInteger, primary_key=True, autoincrement=True)
    docid = Column(BigInteger, ForeignKey('docfir.docid', ondelete='RESTRICT'), nullable=False)
    parid = Column(BigInteger, ForeignKey('docpart.parid', ondelete='RESTRICT'), nullable=False)
    opeid = Column(UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)
    secuen = Column(Integer, nullable=False)
    intnum = Column(Integer, nullable=False)
    tipfir = Column(String(30), nullable=False)
    estado = Column(String(20), nullable=False)
    verori = Column(String(20), nullable=False)
    hasori = Column(String(64), nullable=False)
    verfin = Column(String(20), nullable=True)
    hasfin = Column(String(64), nullable=True)
    errcod = Column(String(40), nullable=True)
    motivo = Column(String(500), nullable=True)
    result = Column(JSONB, nullable=True)
    fecini = Column(DateTime(timezone=True), nullable=False)
    fecfin = Column(DateTime(timezone=True), nullable=True)
    revnum = Column(Integer, nullable=False, default=1)
    usrcre = Column(String(60), nullable=False)
    feccre = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    usrmod = Column(String(60), nullable=True)
    fecmod = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    documento = relationship("DocFir")
    participante = relationship("DocPart")
    posiciones = relationship("Firpos", back_populates="docfirma")

    __table_args__ = (
        UniqueConstraint('docid', 'secuen', name='uq_docfirma_docid_secuen'),
        UniqueConstraint('parid', 'intnum', name='uq_docfirma_parid_intnum'),
        CheckConstraint('secuen > 0', name='ck_docfirma_secuen_pos'),
        CheckConstraint('intnum > 0', name='ck_docfirma_intnum_pos'),
        CheckConstraint('revnum >= 1', name='ck_docfirma_revnum_pos'),
        CheckConstraint("tipfir IN ('MANUSCRITA', 'INTERNA')", name='ck_docfirma_tipfir'),
        CheckConstraint(
            "estado IN ('INICIADA', 'GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO', 'COMPLETADA', 'CONFLICTO', 'FALLIDA', 'CANCELADA')",
            name='ck_docfirma_estado'
        ),
        CheckConstraint("hasori ~ '^[0-9a-f]{64}$'", name='ck_docfirma_hasori_hex'),
        CheckConstraint("hasfin IS NULL OR hasfin ~ '^[0-9a-f]{64}$'", name='ck_docfirma_hasfin_hex'),
        CheckConstraint("fecfin IS NULL OR fecfin >= fecini", name='ck_docfirma_fechas'),
        CheckConstraint("estado != 'FALLIDA' OR errcod IS NOT NULL", name='ck_docfirma_errcod_fallida'),
        CheckConstraint("estado != 'CONFLICTO' OR errcod IS NOT NULL", name='ck_docfirma_errcod_conflicto'),
        CheckConstraint("estado != 'CANCELADA' OR (motivo IS NOT NULL AND TRIM(motivo) != '')", name='ck_docfirma_motivo_cancelada'),
        CheckConstraint(
            "estado NOT IN ('COMPLETADA','CONFLICTO','FALLIDA','CANCELADA') OR fecfin IS NOT NULL",
            name='ck_docfirma_fecfin_terminal'
        ),
        CheckConstraint(
            "estado IN ('COMPLETADA','CONFLICTO','FALLIDA','CANCELADA') OR fecfin IS NULL",
            name='ck_docfirma_fecfin_operativo'
        ),
        CheckConstraint(
            "estado NOT IN ('GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO', 'COMPLETADA') OR hasfin IS NOT NULL",
            name='ck_docfirma_hasfin_req'
        ),
        CheckConstraint(
            "estado NOT IN ('CARGADA', 'VERIFICANDO', 'COMPLETADA') OR verfin IS NOT NULL",
            name='ck_docfirma_verfin_req'
        ),
        CheckConstraint(
            "estado NOT IN ('VERIFICANDO', 'COMPLETADA') OR (result IS NOT NULL AND result != 'null'::jsonb)",
            name='ck_docfirma_result_req'
        ),
        CheckConstraint(
            "estado != 'COMPLETADA' OR (errcod IS NULL AND motivo IS NULL)",
            name='ck_docfirma_completada_limpia'
        ),
        Index(
            'uq_docfirma_docid_activa',
            'docid',
            unique=True,
            postgresql_where=func.coalesce(estado, '').in_(['INICIADA', 'GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO'])
        ),
        Index(
            'uq_docfirma_parid_compl',
            'parid',
            unique=True,
            postgresql_where=estado == 'COMPLETADA'
        ),
        Index('ix_docfirma_estado_fecmod', 'estado', 'fecmod'),
    )
