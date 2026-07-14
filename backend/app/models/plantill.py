from sqlalchemy import Column, BigInteger, String, Integer, Boolean, DateTime, CheckConstraint, Index, text
from sqlalchemy.sql import func
from app.core.database import Base

class Plantill(Base):
    __tablename__ = 'plantill'

    tplid = Column(BigInteger, primary_key=True, autoincrement=True)
    tplcod = Column(String(30), nullable=False)
    tplnom = Column(String(150), nullable=False)
    tplver = Column(Integer, nullable=False)
    nodid = Column(String(64), nullable=True)
    numpag = Column(Integer, nullable=False)
    estado = Column(String(20), nullable=False)
    activo = Column(Boolean, nullable=False, default=True)
    usrcre = Column(String(60), nullable=False)
    feccre = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    usrmod = Column(String(60), nullable=True)
    fecmod = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    __table_args__ = (
        CheckConstraint("tplcod != ''", name='ck_plantill_tplcod_empty'),
        CheckConstraint("tplnom != ''", name='ck_plantill_tplnom_empty'),
        CheckConstraint("tplver > 0", name='ck_plantill_tplver_positive'),
        CheckConstraint("numpag > 0", name='ck_plantill_numpag_positive'),
        CheckConstraint("estado IN ('BORRADOR', 'ACTIVA', 'INACTIVA')", name='ck_plantill_estado'),
        CheckConstraint("usrcre != ''", name='ck_plantill_usrcre_empty'),
        Index('ix_plantill_tplcod_tplver_unique', 'tplcod', 'tplver', unique=True),
        Index('ix_plantill_tplcod_activa_unique', 'tplcod', unique=True, postgresql_where=text("estado = 'ACTIVA'")),
    )
