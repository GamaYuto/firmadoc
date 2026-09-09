from sqlalchemy import BigInteger, Column, Integer, Numeric, DateTime, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base

class Firpos(Base):
    __tablename__ = 'firpos'

    posid = Column(BigInteger, primary_key=True, autoincrement=True)
    firid = Column(BigInteger, ForeignKey('docfirma.firid', ondelete='RESTRICT'), nullable=False)
    pagina = Column(Integer, nullable=False)
    posx = Column(Numeric(12, 4), nullable=False)
    posy = Column(Numeric(12, 4), nullable=False)
    ancho = Column(Numeric(12, 4), nullable=False)
    alto = Column(Numeric(12, 4), nullable=False)
    rotaci = Column(Integer, nullable=False, default=0)
    orden = Column(Integer, nullable=False)
    camid = Column(BigInteger, ForeignKey('tplcamp.camid', ondelete='RESTRICT'), nullable=True)
    feccre = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    docfirma = relationship("DocFirma", back_populates="posiciones")
    tplcamp = relationship("TplCamp")

    __table_args__ = (
        UniqueConstraint('firid', 'orden', name='uq_firpos_firid_orden'),
        CheckConstraint('pagina > 0', name='ck_firpos_pagina'),
        CheckConstraint('posx >= 0', name='ck_firpos_posx'),
        CheckConstraint('posy >= 0', name='ck_firpos_posy'),
        CheckConstraint('ancho > 0', name='ck_firpos_ancho'),
        CheckConstraint('alto > 0', name='ck_firpos_alto'),
        CheckConstraint('orden > 0', name='ck_firpos_orden'),
        CheckConstraint('rotaci IN (0, 90, 180, 270)', name='ck_firpos_rotaci'),
    )
