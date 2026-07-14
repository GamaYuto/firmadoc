from sqlalchemy.orm import Session
from typing import List, Optional
from app.models.tplcamp import TplCamp
from app.schemas.campo import CampoCreate, CampoUpdate

def get_campo(db: Session, camid: int) -> Optional[TplCamp]:
    return db.query(TplCamp).filter(TplCamp.camid == camid).first()

def get_campo_by_code(db: Session, tplid: int, camcod: str) -> Optional[TplCamp]:
    return db.query(TplCamp).filter(TplCamp.tplid == tplid, TplCamp.camcod == camcod).first()

def get_campo_by_orden(db: Session, tplid: int, orden: int) -> Optional[TplCamp]:
    return db.query(TplCamp).filter(TplCamp.tplid == tplid, TplCamp.orden == orden).first()

def list_campos_by_template(db: Session, tplid: int, include_inactive: bool = False) -> List[TplCamp]:
    query = db.query(TplCamp).filter(TplCamp.tplid == tplid)
    if not include_inactive:
        query = query.filter(TplCamp.activo == True)
    return query.order_by(TplCamp.orden.asc()).all()

def create_campo(db: Session, tplid: int, obj_in: CampoCreate, usrcre: str) -> TplCamp:
    db_obj = TplCamp(
        tplid=tplid,
        camcod=obj_in.camcod,
        camnom=obj_in.camnom,
        camtip=obj_in.camtip,
        pagina=obj_in.pagina,
        posx=obj_in.posx,
        posy=obj_in.posy,
        ancho=obj_in.ancho,
        alto=obj_in.alto,
        obliga=obj_in.obliga,
        orden=obj_in.orden,
        config=obj_in.config,
        activo=True,
        usrcre=usrcre
    )
    db.add(db_obj)
    return db_obj

def update_campo(db: Session, db_obj: TplCamp, obj_in: CampoUpdate, usrmod: str) -> TplCamp:
    update_data = obj_in.model_dump(exclude_unset=True)
    for field in update_data:
        setattr(db_obj, field, update_data[field])
    db_obj.usrmod = usrmod
    return db_obj

def clone_campos_for_version(db: Session, old_tplid: int, new_tplid: int, usrcre: str) -> List[TplCamp]:
    old_campos = list_campos_by_template(db, old_tplid, include_inactive=False)
    new_campos = []
    for oc in old_campos:
        nc = TplCamp(
            tplid=new_tplid,
            camcod=oc.camcod,
            camnom=oc.camnom,
            camtip=oc.camtip,
            pagina=oc.pagina,
            posx=oc.posx,
            posy=oc.posy,
            ancho=oc.ancho,
            alto=oc.alto,
            obliga=oc.obliga,
            orden=oc.orden,
            config=oc.config,
            activo=True,
            usrcre=usrcre
        )
        db.add(nc)
        new_campos.append(nc)
    return new_campos
