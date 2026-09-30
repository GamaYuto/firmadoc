from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional
from uuid import UUID
import os
import re
from app.services.alfresco_client import (
    AlfrescoClient, AlfrescoError, AlfrescoNotFoundError, 
    AlfrescoPermissionError, AlfrescoAuthenticationError, 
    AlfrescoInvalidContentError, AlfrescoDownloadLimitError,
    AlfrescoConnectionError, AlfrescoUnsupportedTypeError
)
from app.schemas.alfresco import NodeMetadata
from app.crud.crud_audifir import create_evento
from app.core.database import get_db
from sqlalchemy.orm import Session
import logging

from app.core.config import settings
from app.core.security import AuthenticatedPrincipal, get_current_principal

logger = logging.getLogger(__name__)

router = APIRouter()
client = AlfrescoClient()

def sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r'[\x00-\x1f\x7f-\x9f\/"\'\\]', '', name)
    if not name or name.isspace() or name.lower() == ".pdf":
        return "documento.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name

def parse_node_id(node_id: str | UUID) -> UUID:
    if isinstance(node_id, UUID):
        return node_id

    prefix = "workspace://SpacesStore/"
    if node_id.startswith(prefix):
        node_id = node_id[len(prefix):]
    try:
        return UUID(node_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Identificador de documento Alfresco inválido")

def handle_alfresco_exceptions(e: Exception, db: Session, node_id: UUID, ip: str):
    if isinstance(e, AlfrescoNotFoundError):
        create_evento(db, evento="ALF_NOT_FOUND", iporig=ip, detalle=f"Nodo no encontrado: {node_id}")
        raise HTTPException(status_code=404, detail="Nodo no encontrado en Alfresco")
    elif isinstance(e, AlfrescoPermissionError):
        create_evento(db, evento="ALF_PERM_ERR", iporig=ip, detalle=f"Permiso denegado: {node_id}")
        raise HTTPException(status_code=403, detail="Permiso denegado en Alfresco")
    elif isinstance(e, AlfrescoAuthenticationError):
        create_evento(db, evento="ALF_AUTH_ERR", iporig=ip, detalle="Error de autenticación con Alfresco")
        raise HTTPException(status_code=401, detail="Error de autenticación con Alfresco")
    elif isinstance(e, AlfrescoDownloadLimitError):
        create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=str(e))
        raise HTTPException(status_code=413, detail=str(e))
    elif isinstance(e, AlfrescoInvalidContentError):
        create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=str(e))
        raise HTTPException(status_code=422, detail=str(e))
    elif isinstance(e, AlfrescoUnsupportedTypeError):
        create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=str(e))
        raise HTTPException(status_code=415, detail=str(e))
    elif isinstance(e, AlfrescoConnectionError):
        create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=str(e))
        raise HTTPException(status_code=503, detail="Servicio de Alfresco no disponible temporalmente")
    elif getattr(e, 'status_code', None) in (400, 422, 415, 413, 404, 403, 401) or isinstance(e, HTTPException):
        raise e
    elif isinstance(e, AlfrescoError):
        create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=f"Error interno: {str(e)}")
        raise HTTPException(status_code=502, detail="Error de comunicación con Alfresco")
    else:
        logger.error(f"Error inesperado: {str(e)}")
        create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=f"Error inesperado: {str(e)}")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

@router.get("/nodes/{node_id}", response_model=NodeMetadata)
async def get_node_metadata(
    node_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    ip = request.client.host if request.client else None
    parsed_id = parse_node_id(node_id)
    try:
        metadata = await client.get_node_metadata(parsed_id)
        if not metadata.is_file:
            create_evento(db, evento="ALF_CONT_ERR", usrid=principal.user_id, iporig=ip, detalle=f"Nodo no es archivo: {node_id}")
            raise HTTPException(status_code=422, detail="El nodo solicitado no es un archivo")
        if not metadata.mime_type:
            create_evento(db, evento="ALF_CONT_ERR", usrid=principal.user_id, iporig=ip, detalle=f"Nodo sin mimeType: {node_id}")
            raise HTTPException(status_code=422, detail="El nodo no tiene un mimeType válido")
            
        create_evento(db, evento="ALF_NODE_READ", usrid=principal.user_id, iporig=ip, detalle=f"Metadatos consultados exitosamente: {parsed_id}")
        return metadata
    except Exception as e:
        handle_alfresco_exceptions(e, db, parsed_id, ip)

@router.get("/nodes/{node_id}/content")
async def get_node_content(
    node_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    ip = request.client.host if request.client else None
    parsed_id = parse_node_id(node_id)
    
    try:
        metadata = await client.get_node_metadata(parsed_id)
        if not metadata.is_file:
            create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=f"Nodo no es archivo: {node_id}")
            raise HTTPException(status_code=422, detail="El nodo solicitado no es un archivo")
        if metadata.mime_type != "application/pdf":
            create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=f"Nodo no es PDF: {node_id}")
            raise HTTPException(status_code=415, detail="El documento no es un PDF")
    except Exception as e:
        handle_alfresco_exceptions(e, db, parsed_id, ip)

    try:
        temp_path, downloaded_size, final_hash = await client.download_node_content(parsed_id)
    except Exception as e:
        handle_alfresco_exceptions(e, db, parsed_id, ip)
        
    try:
        create_evento(
            db, 
            evento="ALF_PDF_DOWN", 
            usrid=principal.user_id,
            iporig=ip, 
            detalle=f"nodeId: {parsed_id}, tamaño: {downloaded_size}, hash: {final_hash}, resultado: OK"
        )
    except Exception as e:
        logger.error(f"Fallo al guardar auditoría tras descarga exitosa: {e}")
    
    def file_streamer():
        try:
            with open(temp_path, "rb") as f:
                while chunk := f.read(8192):
                    yield chunk
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                
    filename = sanitize_filename(metadata.name)
    headers = {
        "Content-Disposition": f'inline; filename="{filename}"',
        "X-Content-Type-Options": "nosniff"
    }
    
    return StreamingResponse(
        file_streamer(),
        media_type="application/pdf",
        headers=headers
    )


@router.get("/explorar")
async def explore_alfresco_folder(
    folder_id: Optional[str] = None,
    skip_count: int = 0,
    max_items: int = 100,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    target_folder = folder_id or "-my-"

    try:
        return await client.list_folder_children(target_folder, skip_count=skip_count, max_items=max_items)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error al explorar carpeta de Alfresco: {str(exc)}") from exc


@router.get("/buscar")
async def search_alfresco_documents(
    q: str,
    max_items: int = 50,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    try:
        return await client.search_documents(q, max_items=max_items)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error al buscar en Alfresco: {str(exc)}") from exc

@router.get("/usuarios/buscar")
async def search_alfresco_users(
    q: str,
    max_items: int = 20,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    try:
        return await client.search_users(q, max_items=max_items)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error al buscar usuarios en Alfresco: {str(exc)}") from exc
