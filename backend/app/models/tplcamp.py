from sqlalchemy import Column, BigInteger, String, Integer, Boolean, DateTime, Numeric, ForeignKey, CheckConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from app.core.database import Base

class TplCamp(Base):
    __tablename__ = 'tplcamp'

    camid = Column(BigInteger, primary_key=True, autoincrement=True)
    tplid = Column(BigInteger, ForeignKey('plantill.tplid', ondelete='RESTRICT', name='fk_tplcamp_tplid_plantill'), nullable=False)
    camcod = Column(String(30), nullable=False)
    camnom = Column(String(100), nullable=False)
    camtip = Column(String(20), nullable=False)
    pagina = Column(Integer, nullable=False)
    posx = Column(Numeric(10, 4), nullable=False)
    posy = Column(Numeric(10, 4), nullable=False)
    ancho = Column(Numeric(10, 4), nullable=False)
    alto = Column(Numeric(10, 4), nullable=False)
    obliga = Column(Boolean, nullable=False, default=False)
    orden = Column(Integer, nullable=False)
    config = Column(JSONB, nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    usrcre = Column(String(60), nullable=False)
    feccre = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    usrmod = Column(String(60), nullable=True)
    fecmod = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    __table_args__ = (
        CheckConstraint("camcod != ''", name='ck_tplcamp_camcod_empty'),
        CheckConstraint("camnom != ''", name='ck_tplcamp_camnom_empty'),
        CheckConstraint(
            "camtip IN ('TEXTO', 'TEXLAR', 'FECHA', 'CASILLA', 'OPCION', 'NOMBRE', 'TIPDOC', 'NUMDOC', 'FIRMA', 'SELLO')",
            name='ck_tplcamp_camtip'
        ),
        CheckConstraint("pagina > 0", name='ck_tplcamp_pagina_positive'),
        CheckConstraint("posx >= 0 AND posx <= 1", name='ck_tplcamp_posx_range'),
        CheckConstraint("posy >= 0 AND posy <= 1", name='ck_tplcamp_posy_range'),
        CheckConstraint("ancho > 0 AND ancho <= 1", name='ck_tplcamp_ancho_range'),
        CheckConstraint("alto > 0 AND alto <= 1", name='ck_tplcamp_alto_range'),
        CheckConstraint("posx + ancho <= 1", name='ck_tplcamp_posx_ancho_limit'),
        CheckConstraint("posy + alto <= 1", name='ck_tplcamp_posy_alto_limit'),
        CheckConstraint("orden > 0", name='ck_tplcamp_orden_positive'),
        Index('ix_tplcamp_tplid_camcod_unique', 'tplid', 'camcod', unique=True),
        Index('ix_tplcamp_tplid_orden_unique', 'tplid', 'orden', unique=True),
    )
