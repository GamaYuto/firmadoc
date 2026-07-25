from sqlalchemy import BigInteger, Column, Integer, String, Boolean, DateTime, ForeignKey, CheckConstraint, UniqueConstraint, Index, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB
from app.core.database import Base

class DocPart(Base):
    __tablename__ = 'docpart'

    parid = Column(BigInteger, primary_key=True, autoincrement=True)
    dpasid = Column(BigInteger, ForeignKey('docpaso.dpasid', ondelete='RESTRICT'), nullable=False)
    usrid = Column(String(60), nullable=False)
    nomcom = Column(String(150), nullable=False)
    correo = Column(String(100), nullable=False)
    rolpro = Column(String(100), nullable=True)
    orden = Column(Integer, nullable=False, default=1)
    obliga = Column(Boolean, nullable=False, default=True)
    estado = Column(String(20), nullable=False)
    motivo = Column(String(500), nullable=True)
    result = Column(JSONB, nullable=True)
    
    fecdis = Column(DateTime(timezone=True), nullable=True)
    fecini = Column(DateTime(timezone=True), nullable=True)
    fecfin = Column(DateTime(timezone=True), nullable=True)
    
    verlock = Column(Integer, nullable=False, default=1)
    usrcre = Column(String(60), nullable=False)
    feccre = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    usrmod = Column(String(60), nullable=True)
    fecmod = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint('dpasid', 'usrid', name='uq_docpart_dpasid_usrid'),
        UniqueConstraint('dpasid', 'orden', name='uq_docpart_dpasid_orden'),
        CheckConstraint('orden > 0', name='ck_docpart_orden_positivo'),
        CheckConstraint('verlock >= 1', name='ck_docpart_verlock_positivo'),
        CheckConstraint("estado IN ('PENDIENTE', 'DISPONIBLE', 'EN_PROCESO', 'COMPLETADO', 'RECHAZADO', 'OMITIDO', 'VENCIDO', 'CANCELADO')", name='ck_docpart_estado_valido'),
        CheckConstraint(
            "(estado NOT IN ('RECHAZADO', 'OMITIDO', 'CANCELADO')) OR (motivo IS NOT NULL AND motivo != '')",
            name='ck_docpart_motivo_requerido'
        ),
        CheckConstraint(
            "(fecini IS NULL) OR (fecfin IS NULL) OR (fecfin >= fecini)",
            name='ck_docpart_fechas_consistentes'
        ),
        Index('idx_docpart_usrid_estado', 'usrid', 'estado'),
        Index('idx_docpart_dpasid_activos', 'dpasid', postgresql_where=text("estado IN ('DISPONIBLE', 'EN_PROCESO')"))
    )

    docpaso = relationship("DocPaso", back_populates="docparts")
