from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.flujodoc import Flujodoc
from app.schemas.flujo import FlujoCreate, FlujoUpdate

class CRUDFlujodoc:
    def get_by_id(self, db: Session, fluid: int) -> Optional[Flujodoc]:
        return db.scalars(select(Flujodoc).where(Flujodoc.fluid == fluid)).first()

    def get_by_code_version(self, db: Session, flucod: str, fluver: int) -> Optional[Flujodoc]:
        return db.scalars(select(Flujodoc).where(Flujodoc.flucod == flucod, Flujodoc.fluver == fluver)).first()

    def get_active_by_code(self, db: Session, flucod: str) -> Optional[Flujodoc]:
        return db.scalars(select(Flujodoc).where(Flujodoc.flucod == flucod, Flujodoc.estado == "ACTIVO")).first()

    def list(self, db: Session, skip: int = 0, limit: int = 100) -> List[Flujodoc]:
        return list(db.scalars(select(Flujodoc).offset(skip).limit(limit)).all())

    def create(self, db: Session, obj_in: FlujoCreate) -> Flujodoc:
        db_obj = Flujodoc(
            flucod=obj_in.flucod,
            flunom=obj_in.flunom,
            fluver=1,
            estado="BORRADOR",
            usrcre=obj_in.usrcre,
            activo=True
        )
        db.add(db_obj)
        return db_obj

    def update_draft(self, db: Session, db_obj: Flujodoc, obj_in: FlujoUpdate) -> Flujodoc:
        if db_obj.estado != "BORRADOR":
            raise ValueError("Solo se pueden modificar flujos en estado BORRADOR")
        if obj_in.flunom is not None:
            db_obj.flunom = obj_in.flunom
        db_obj.usrmod = obj_in.usrmod
        return db_obj

    def activate(self, db: Session, db_obj: Flujodoc, usrmod: str) -> Flujodoc:
        db_obj.estado = "ACTIVO"
        db_obj.usrmod = usrmod
        return db_obj

    def deactivate(self, db: Session, db_obj: Flujodoc, usrmod: str) -> Flujodoc:
        db_obj.estado = "INACTIVO"
        db_obj.usrmod = usrmod
        return db_obj

    def create_new_version(self, db: Session, old_obj: Flujodoc, usrcre: str) -> Flujodoc:
        new_obj = Flujodoc(
            flucod=old_obj.flucod,
            flunom=old_obj.flunom,
            fluver=old_obj.fluver + 1,
            estado="BORRADOR",
            usrcre=usrcre,
            activo=True
        )
        db.add(new_obj)
        return new_obj

crud_flujodoc = CRUDFlujodoc()
