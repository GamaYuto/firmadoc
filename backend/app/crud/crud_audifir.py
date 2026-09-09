from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Optional, List
from app.models.audifir import Audifir

def create_evento_tx(db: Session, evento: str, enttip: Optional[str] = None, entid: Optional[int] = None, docid: Optional[int] = None, usrid: Optional[str] = None, iporig: Optional[str] = None, detalle: Optional[str] = None) -> Audifir:
    if not evento:
        raise ValueError("El evento no puede estar vacío")
    
    db_obj = Audifir(
        docid=docid,
        enttip=enttip,
        entid=entid,
        evento=evento,
        usrid=usrid,
        iporig=iporig,
        detalle=detalle
    )
    db.add(db_obj)
    # Sin commit interno para integrarse en transacciones agregadas
    return db_obj

def create_evento(db: Session, evento: str, docid: Optional[int] = None, usrid: Optional[str] = None, iporig: Optional[str] = None, detalle: Optional[str] = None) -> Audifir:
    db_obj = create_evento_tx(db=db, evento=evento, docid=docid, usrid=usrid, iporig=iporig, detalle=detalle)
    db.flush()
    db.refresh(db_obj)
    db.commit()
    return db_obj

def get_evento(db: Session, audid: int) -> Optional[Audifir]:
    return db.scalars(select(Audifir).filter(Audifir.audid == audid)).first()

def get_eventos(db: Session, skip: int = 0, limit: int = 100) -> List[Audifir]:
    return list(db.scalars(select(Audifir).offset(skip).limit(limit)).all())
