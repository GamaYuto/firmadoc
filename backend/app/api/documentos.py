from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.orm import Session
from typing import Optional, Any
from uuid import UUID
from app.core.database import get_db
from app.schemas.documento import DocFirCreate, DocFirRead, DocFirList, DocFirCancel
from app.services.document_service import DocumentService
from app.services.alfresco_client import AlfrescoClient
from app.api.alfresco import handle_alfresco_exceptions
from app.crud.crud_docfir import get_by_id, list_documentos
from app.crud.crud_audifir import create_evento

from app.core.security import AuthenticatedPrincipal, get_current_principal

router = APIRouter()
alfresco_client = AlfrescoClient()
doc_service = DocumentService(alfresco_client)

@router.post("/iniciar", response_model=DocFirRead, status_code=201)
async def iniciar_proceso(
    data: DocFirCreate,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    ip = request.client.host if request.client else ""
    try:
        return await doc_service.iniciar_proceso(db, data.node_id, principal.user_id, ip)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        # Delegate alfresco errors to the unified handler
        handle_alfresco_exceptions(e, db, data.node_id, ip)

@router.post("/{docid}/cancelar", response_model=DocFirRead)
def cancelar_proceso(
    docid: int,
    data: DocFirCancel,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    ip = request.client.host if request.client else ""
    return doc_service.cancelar_proceso(db, docid, data.motivo, principal, ip)

@router.get("/{docid}", response_model=DocFirRead)
def obtener_proceso(
    docid: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    ip = request.client.host if request.client else ""
    docfir = get_by_id(db, docid)
    if not docfir:
        raise HTTPException(status_code=404, detail="Proceso no encontrado")
        
    try:
        evento = create_evento(db, evento="DOC_CONSU", usrid=principal.user_id, iporig=ip, detalle=f"Consulta docid: {docid}")
        evento.docid = docid
        db.commit()
    except Exception:
        db.rollback()
        
    return docfir

@router.get("", response_model=DocFirList)
def listar_procesos(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    estado: Optional[str] = None,
    nodid: Optional[str] = None,
    activo: Optional[bool] = None,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    items, total = list_documentos(db, limit=limit, offset=offset, estado=estado, nodid=nodid, activo=activo)
    return DocFirList(items=items, total=total)
