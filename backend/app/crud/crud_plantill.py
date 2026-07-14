from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from app.models.plantill import Plantill
from app.schemas.plantilla import PlantillaCreate, PlantillaUpdate

def get_plantilla(db: Session, tplid: int) -> Optional[Plantill]:
    return db.query(Plantill).filter(Plantill.tplid == tplid).first()

def get_plantilla_by_code_version(db: Session, tplcod: str, tplver: int) -> Optional[Plantill]:
    return db.query(Plantill).filter(Plantill.tplcod == tplcod, Plantill.tplver == tplver).first()

def get_active_plantilla_by_code(db: Session, tplcod: str) -> Optional[Plantill]:
    return db.query(Plantill).filter(
        Plantill.tplcod == tplcod, 
        Plantill.estado == "ACTIVA", 
        Plantill.activo == True
    ).first()

def get_plantillas(db: Session, skip: int = 0, limit: int = 50, estado: Optional[str] = None, tplcod: Optional[str] = None) -> tuple[List[Plantill], int]:
    query = db.query(Plantill).filter(Plantill.activo == True)
    if estado:
        query = query.filter(Plantill.estado == estado)
    if tplcod:
        query = query.filter(Plantill.tplcod == tplcod)
    
    total = query.count()
    items = query.order_by(Plantill.tplid.desc()).offset(skip).limit(limit).all()
    return items, total

def create_plantilla(db: Session, obj_in: PlantillaCreate, usrcre: str) -> Plantill:
    db_obj = Plantill(
        tplcod=obj_in.tplcod,
        tplnom=obj_in.tplnom,
        tplver=1,
        nodid=obj_in.nodid,
        numpag=obj_in.numpag,
        estado="BORRADOR",
        activo=True,
        usrcre=usrcre
    )
    db.add(db_obj)
    # db.commit() and db.refresh() must be handled by the caller/service layer
    return db_obj

def update_plantilla(db: Session, db_obj: Plantill, obj_in: PlantillaUpdate, usrmod: str) -> Plantill:
    update_data = obj_in.model_dump(exclude_unset=True)
    for field in update_data:
        setattr(db_obj, field, update_data[field])
    db_obj.usrmod = usrmod
    return db_obj

def create_new_version(db: Session, db_obj: Plantill, usrcre: str) -> Plantill:
    new_tpl = Plantill(
        tplcod=db_obj.tplcod,
        tplnom=db_obj.tplnom,
        tplver=db_obj.tplver + 1,
        nodid=db_obj.nodid,
        numpag=db_obj.numpag,
        estado="BORRADOR",
        activo=True,
        usrcre=usrcre
    )
    db.add(new_tpl)
    return new_tpl
