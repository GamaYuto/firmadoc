from sqlalchemy import BigInteger, Column, String, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base

class Audifir(Base):
    __tablename__ = 'audifir'

    audid = Column(BigInteger, primary_key=True, autoincrement=True)
    docid = Column(BigInteger, ForeignKey('docfir.docid', ondelete='RESTRICT'), nullable=True)
    enttip = Column(String(20), nullable=True)
    entid = Column(BigInteger, nullable=True)
    evento = Column(String(40), nullable=False)
    usrid = Column(String(60), nullable=True)
    iporig = Column(String(45), nullable=True)
    detalle = Column(Text, nullable=True)
    fecope = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("evento != ''", name='ck_audifir_evento_empty'),
        CheckConstraint("usrid != ''", name='ck_audifir_usrid_empty'),
        CheckConstraint("enttip IN ('DOCUMENTO', 'PLANTILLA', 'CAMPO', 'SISTEMA', 'FLUJODOC', 'FLUPASO', 'PASO')", name='ck_audifir_enttip'),
    )

    documento = relationship("DocFir")
