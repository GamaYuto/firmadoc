from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models.flupaso import Flupaso
from app.schemas.flujo import PasoCreate, PasoUpdate

class CRUDFlupaso:
    def get_by_id(self, db: Session, pasid: int) -> Optional[Flupaso]:
        return db.scalars(select(Flupaso).where(Flupaso.pasid == pasid)).first()

    def list_by_flow(self, db: Session, fluid: int) -> List[Flupaso]:
        return list(db.scalars(select(Flupaso).where(Flupaso.fluid == fluid).order_by(Flupaso.orden)).all())

    def create(self, db: Session, fluid: int, obj_in: PasoCreate, usrcre: str) -> Flupaso:
        db_obj = Flupaso(
            fluid=fluid,
            pascod=obj_in.pascod,
            pasnom=obj_in.pasnom,
            pastip=obj_in.pastip,
            orden=obj_in.orden,
            rolreq=obj_in.rolreq,
            obliga=obj_in.obliga,
            plazo=obj_in.plazo,
            config=obj_in.config,
            usrcre=usrcre,
            activo=True
        )
        db.add(db_obj)
        return db_obj

    def update(self, db: Session, db_obj: Flupaso, obj_in: PasoUpdate, usrmod: str) -> Flupaso:
        update_data = obj_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)
        db_obj.usrmod = usrmod
        return db_obj

    def deactivate(self, db: Session, db_obj: Flupaso, usrmod: str) -> Flupaso:
        db_obj.activo = False
        db_obj.usrmod = usrmod
        return db_obj

    def clone_for_version(self, db: Session, old_pasos: List[Flupaso], new_fluid: int, usrcre: str) -> List[Flupaso]:
        new_pasos = []
        for p in old_pasos:
            if p.activo:
                new_paso = Flupaso(
                    fluid=new_fluid,
                    pascod=p.pascod,
                    pasnom=p.pasnom,
                    pastip=p.pastip,
                    orden=p.orden,
                    rolreq=p.rolreq,
                    obliga=p.obliga,
                    plazo=p.plazo,
                    config=p.config,
                    usrcre=usrcre,
                    activo=True
                )
                db.add(new_paso)
                new_pasos.append(new_paso)
        return new_pasos

crud_flupaso = CRUDFlupaso()
