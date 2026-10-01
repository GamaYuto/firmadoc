from sqlalchemy.orm import Session
from sqlalchemy import exc
from app.models.docfir import DocFir, EstadoDoc
from typing import Optional, Tuple, List

def create_documento(db: Session, nodid: str, docnom: str, tamano: int, verini: str, hasori: str, usrcre: str) -> DocFir:
    db_obj = DocFir(
        nodid=nodid,
        docnom=docnom,
        mimtip="application/pdf",
        tamano=tamano,
        verini=verini,
        estado=EstadoDoc.BORRADOR.value,
        hasori=hasori,
        usrcre=usrcre
    )
    db.add(db_obj)
    db.flush()
    return db_obj

def get_by_id(db: Session, docid: int) -> Optional[DocFir]:
    return db.query(DocFir).filter(DocFir.docid == docid).first()

def get_active_by_node_version(db: Session, nodid: str, verini: str) -> Optional[DocFir]:
    return db.query(DocFir).filter(
        DocFir.nodid == nodid,
        DocFir.verini == verini,
        DocFir.activo == True,
        DocFir.estado != EstadoDoc.CANCELADO.value
    ).first()

ACTIVE_STATUSES = (
    EstadoDoc.BORRADOR.value,
    EstadoDoc.PREPARADO.value,
    EstadoDoc.EN_CURSO.value,
    EstadoDoc.PENDIENTE_FIRMA.value,
    EstadoDoc.FIRMADO_PARCIAL.value,
    EstadoDoc.PENDIENTE_PUBLICACION.value,
    EstadoDoc.ERROR_PUBLICACION.value,
)

def get_active_by_node(db: Session, nodid: str) -> Optional[DocFir]:
    return (
        db.query(DocFir)
        .filter(
            DocFir.nodid == nodid,
            DocFir.activo.is_(True),
            DocFir.estado.in_(ACTIVE_STATUSES),
        )
        .order_by(DocFir.docid.desc())
        .first()
    )

def build_active_process_detail(db: Session, doc: DocFir) -> dict:
    from app.models.docfirma import DocFirma
    latest = (
        db.query(DocFirma)
        .filter(DocFirma.docid == doc.docid)
        .order_by(DocFirma.secuen.desc(), DocFirma.firid.desc())
        .first()
    )
    return {
        "code": "ACTIVE_PROCESS",
        "message": "Ya existe un proceso activo para este documento",
        "docid": doc.docid,
        "status": doc.estado,
        "firid": latest.firid if latest else None,
        "signature_status": latest.estado if latest else None,
    }


def list_documentos(db: Session, limit: int = 100, offset: int = 0, estado: Optional[str] = None, nodid: Optional[str] = None, activo: Optional[bool] = None) -> Tuple[List[DocFir], int]:
    limit = min(limit, 1000)
    query = db.query(DocFir)
    if estado:
        query = query.filter(DocFir.estado == estado)
    if nodid:
        query = query.filter(DocFir.nodid == nodid)
    if activo is not None:
        query = query.filter(DocFir.activo == activo)
    
    total = query.count()
    items = query.order_by(DocFir.docid.desc()).offset(offset).limit(limit).all()
    return items, total

def cancel_documento(db: Session, db_obj: DocFir, usrmod: str) -> DocFir:
    db_obj.estado = EstadoDoc.CANCELADO.value
    db_obj.usrmod = usrmod
    db.add(db_obj)
    db.flush()
    return db_obj

def mark_error(db: Session, db_obj: DocFir, usrmod: str) -> DocFir:
    db_obj.estado = EstadoDoc.ERROR.value
    db_obj.usrmod = usrmod
    db.add(db_obj)
    db.flush()
    return db_obj
