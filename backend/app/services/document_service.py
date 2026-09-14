from __future__ import annotations

import os
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from app.services.alfresco_client import AlfrescoClient
from app.services.temporary_artifact_service import TemporaryArtifactService, temporary_artifact_service
from app.crud.crud_docfir import create_documento, get_active_by_node_version, cancel_documento, get_by_id, mark_error
from app.crud.crud_audifir import create_evento
from app.models.docfir import DocFir, EstadoDoc
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)

class DocumentService:
    def __init__(self, alfresco_client: AlfrescoClient, artifact_service: Optional[TemporaryArtifactService] = None):
        self.alfresco_client = alfresco_client
        self.artifact_service = artifact_service or temporary_artifact_service

    async def iniciar_proceso(self, db: Session, node_id: UUID, usrcre: str, ip: str) -> DocFir:
        temp_path = None
        try:
            # 1. Consultar metadatos en Alfresco
            metadata = await self.alfresco_client.get_node_metadata(node_id)
            if not metadata.is_file:
                create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=f"Nodo no es archivo: {node_id}")
                db.commit()
                raise HTTPException(status_code=422, detail="El nodo solicitado no es un archivo")
            if metadata.mime_type != "application/pdf":
                create_evento(db, evento="ALF_CONT_ERR", iporig=ip, detalle=f"Nodo no es PDF: {node_id}")
                db.commit()
                raise HTTPException(status_code=415, detail="El documento no es un PDF")
            
            verini = metadata.version_label or "1.0"
            
            # 2. Verificar concurrencia temprana
            existing = get_active_by_node_version(db, str(node_id), verini)
            if existing:
                create_evento(db, evento="DOC_DUPLI", iporig=ip, usrid=usrcre, detalle=f"Intento duplicado nodid: {node_id}, ver: {verini}")
                db.commit()
                raise HTTPException(status_code=409, detail="Ya existe un proceso activo para este documento y versión")

            # 3. Descargar y validar PDF
            temp_path, size_bytes, file_hash = await self.alfresco_client.download_node_content(node_id)
            
            # 4. Iniciar transacción de BD
            docfir = create_documento(
                db=db,
                nodid=str(node_id),
                docnom=metadata.name,
                tamano=size_bytes,
                verini=verini,
                hasori=file_hash,
                usrcre=usrcre
            )
            
            # 5. Auditoría
            evento = create_evento(
                db=db,
                evento="DOC_INICIO",
                usrid=usrcre,
                iporig=ip,
                detalle=f"Inicio de proceso. nodid: {node_id}, ver: {verini}, hash: {file_hash}"
            )
            evento.docid = docfir.docid
            
            db.commit()
            db.refresh(docfir)
            return docfir

        except IntegrityError as e:
            db.rollback()
            # Falla de unicidad (índice parcial) o CheckConstraint
            logger.error(f"Integrity error iniciando proceso: {e}")
            raise HTTPException(status_code=409, detail="Ya existe un proceso activo para este documento y versión")
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error inesperado al iniciar proceso documental: {e}")
            
            # Try to log the technical error if possible
            try:
                create_evento(db, evento="ALF_CONT_ERR", iporig=ip, usrid=usrcre, detalle=f"Error iniciando proceso: {str(e)}")
                db.commit()
            except Exception:
                db.rollback()
                
            # If it's an alfresco error, we can throw it so the router's exception handler catches it
            from app.services.alfresco_client import AlfrescoError
            if isinstance(e, AlfrescoError):
                raise e
            raise HTTPException(status_code=500, detail="Error interno del servidor")
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception as e:
                    logger.warning(f"Error eliminando archivo temporal {temp_path}: {e}")

    def cancelar_proceso(self, db: Session, docid: int, motivo: str, usrmod: str | Any, ip: str) -> DocFir:
        try:
            if hasattr(usrmod, "user_id") and hasattr(usrmod, "is_admin"):
                actor_user = usrmod.user_id
                is_admin = usrmod.is_admin
            else:
                actor_user = str(usrmod).strip().lower()
                is_admin = actor_user in ("admin", "administrador")

            docfir = get_by_id(db, docid)
            if not docfir:
                raise HTTPException(status_code=404, detail="Proceso no encontrado")

            if actor_user != (docfir.usrcre or "").strip().lower() and not is_admin:
                raise HTTPException(status_code=403, detail="No autorizado para cancelar este proceso")

            cancelable_states = (
                EstadoDoc.BORRADOR.value,
                EstadoDoc.EN_CURSO.value,
                EstadoDoc.PENDIENTE_FIRMA.value,
                EstadoDoc.FIRMADO_PARCIAL.value,
                EstadoDoc.PENDIENTE_PUBLICACION.value,
                EstadoDoc.ERROR_PUBLICACION.value,
            )
            if docfir.estado not in cancelable_states:
                raise HTTPException(status_code=400, detail=f"No se puede cancelar un proceso en estado {docfir.estado}")
                
            cancel_documento(db, docfir, actor_user)
            
            # Auditoría
            evento = create_evento(
                db=db,
                evento="DOC_CANCEL",
                usrid=actor_user,
                iporig=ip,
                detalle=f"motivo: {motivo[:450]}"
            )
            evento.docid = docfir.docid
            
            db.commit()
            db.refresh(docfir)

            # Limpiar artefactos temporales de firmas del proceso cancelado
            try:
                for firma in docfir.docfirmas or []:
                    for p in self.artifact_service.find_matching_paths(prefix=f"fir-{firma.firid}-"):
                        self.artifact_service.cleanup_path(p)
            except Exception as clean_err:
                logger.warning(f"Error limpiando artefactos temporales tras cancelar proceso {docid}: {clean_err}")
            return docfir
        except HTTPException:
            db.rollback()
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error cancelando proceso: {e}")
            raise HTTPException(status_code=500, detail="Error interno cancelando proceso")
