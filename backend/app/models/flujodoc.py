from sqlalchemy import BigInteger, Column, String, Integer, Boolean, DateTime, CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.core.database import Base
from typing import List

class Flujodoc(Base):
    __tablename__ = 'flujodoc'

    fluid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flucod: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    flunom: Mapped[str] = mapped_column(String(150), nullable=False)
    fluver: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    usrcre: Mapped[str] = mapped_column(String(60), nullable=False)
    feccre: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    usrmod: Mapped[str] = mapped_column(String(60), nullable=True)
    fecmod: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    pasos: Mapped[List["Flupaso"]] = relationship("Flupaso", back_populates="flujo", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("flucod != ''", name="ck_flujodoc_flucod_empty"),
        CheckConstraint("flunom != ''", name="ck_flujodoc_flunom_empty"),
        CheckConstraint("fluver > 0", name="ck_flujodoc_fluver_positive"),
        CheckConstraint("estado IN ('BORRADOR', 'ACTIVO', 'INACTIVO')", name="ck_flujodoc_estado"),
        CheckConstraint("usrcre != ''", name="ck_flujodoc_usrcre_empty"),
        Index("uq_flujodoc_flucod_fluver", "flucod", "fluver", unique=True),
        Index("uq_flujodoc_flucod_activo", "flucod", unique=True, postgresql_where=text("estado = 'ACTIVO'")),
    )
