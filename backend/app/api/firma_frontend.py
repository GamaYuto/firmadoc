from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.firma_frontend import (
    HandwrittenSignatureConfirm,
    InternalSignatureConfirm,
    PendingList,
    PreparationDraftSave,
    PreparationRead,
    PublicationResponse,
    SendToSignatureResponse,
    SignatureDetail,
    SignatureResult,
)
from app.services.alfresco_service import AlfrescoService
from app.services.signature_exceptions import SignaturePublicationError
from app.services.frontend_signature_service import frontend_signature_service

router = APIRouter()


def get_current_user(x_firmadoc_user: str | None = Header(default=None, min_length=1, max_length=60)) -> str:
    if not settings.FIRMADOC_LAB_IDENTITY_ENABLED:
        raise HTTPException(status_code=503, detail="Identidad de laboratorio deshabilitada; configure identidad por proxy")
    if x_firmadoc_user is None:
        raise HTTPException(status_code=401, detail="Identidad de laboratorio requerida")
    user = x_firmadoc_user.strip().lower()
    if not user:
        raise HTTPException(status_code=400, detail="X-FirmaDoc-User invalido")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _publication_error_response(exc: SignaturePublicationError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "status": exc.publication_status if exc.publication_status != "RECOVERY_REQUIRED" else "RECOVERY_REQUIRED",
            "operation_id": exc.operation_id or "",
            "source_version": exc.source_version or "",
            "publication_status": exc.publication_status,
            "code": exc.code,
            "message": exc.message,
        },
    )


@router.get("/preparacion/{node_id}", response_model=PreparationRead)
async def get_or_create_preparation(
    node_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    return await frontend_signature_service.get_or_create_preparation(db, node_id, user, client_ip(request))


@router.get("/preparacion/doc/{docid}", response_model=PreparationRead)
async def get_preparation_by_doc(
    docid: int,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    return await frontend_signature_service.refresh_preparation(db, docid)


@router.post("/preparacion/{docid}/borrador", response_model=PreparationRead)
async def save_preparation_draft(
    docid: int,
    payload: PreparationDraftSave,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    return await frontend_signature_service.save_draft(db, docid, payload, user, client_ip(request))


@router.post("/preparacion/{docid}/enviar", response_model=SendToSignatureResponse)
def send_preparation_to_signature(
    docid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    return frontend_signature_service.send_to_signature(db, docid, user, client_ip(request))


@router.post("/preparacion/{docid}/guardar-enviar", response_model=SendToSignatureResponse)
async def save_and_send_preparation_to_signature(
    docid: int,
    payload: PreparationDraftSave,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    return await frontend_signature_service.save_and_send(db, docid, payload, user, client_ip(request))


@router.get("/pendientes", response_model=PendingList)
def list_pending_signatures(
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    items = frontend_signature_service.list_pending(db, user)
    return PendingList(items=items, total=len(items))


@router.get("/firmas/{firid}", response_model=SignatureDetail)
def get_signature_detail(
    firid: int,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    return frontend_signature_service.get_signature_detail(db, firid, user)


@router.post("/firmas/{firid}/confirmar-interna", response_model=SignatureResult)
async def confirm_internal_signature(
    firid: int,
    payload: InternalSignatureConfirm,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    if payload.confirm is not True:
        raise HTTPException(status_code=400, detail="Debe confirmar la firma electronica")
    return await frontend_signature_service.confirm_internal(db, firid, user, client_ip(request))


@router.post("/firmas/{firid}/confirmar-manuscrita", response_model=SignatureResult)
async def confirm_handwritten_signature(
    firid: int,
    payload: HandwrittenSignatureConfirm,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    return await frontend_signature_service.confirm_handwritten(db, firid, payload.png_data_url, user, client_ip(request))


@router.get("/firmas/{firid}/resultado", response_model=SignatureResult)
def get_signature_result(
    firid: int,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    return frontend_signature_service.get_result(db, firid, user)


@router.get("/firmas/{firid}/resultado/pdf", include_in_schema=False)
def get_signature_result_pdf(
    firid: int,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    path, filename = frontend_signature_service.get_result_pdf_path(db, firid, user)
    return FileResponse(path, media_type="application/pdf", filename=filename)


@router.post("/documentos/{docid}/publicar", response_model=PublicationResponse)
def publish_document_to_alfresco(
    docid: int,
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(get_current_user),
):
    user_agent = request.headers.get("user-agent")
    service = AlfrescoService()
    try:
        outcome = service.publish_document(
            db=db,
            docid=docid,
            actor_user=user,
            iporig=client_ip(request),
            user_agent=user_agent,
        )
    except SignaturePublicationError as exc:
        raise _publication_error_response(exc) from exc
    return PublicationResponse(
        status=outcome.status,
        operation_id=outcome.operation_id,
        source_version=outcome.source_version,
        publication_status=outcome.publication_status,
        code=outcome.code,
        message=outcome.message,
        final_version=outcome.final_version,
        final_hash_short=outcome.final_hash_short,
    )
