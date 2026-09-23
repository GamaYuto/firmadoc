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
    MobileSessionDetail,
    MobileSignatureConfirm,
    PendingList,
    PreparationDraftSave,
    PreparationRead,
    PublicationResponse,
    QrSessionCreateResponse,
    QrSessionStatusResponse,
    SendToSignatureResponse,
    SignatureDetail,
    SignatureResult,
    PreparationParticipant,
    PreparationPosition,
)
from pydantic import BaseModel
from urllib.parse import urlparse
from app.models.docfirma import TipoFirma
from app.services.alfresco_service import AlfrescoService
from app.services.signature_exceptions import SignaturePublicationError
from app.services.frontend_signature_service import frontend_signature_service
from app.services.qr_service import qr_service

from app.core.security import AuthenticatedPrincipal, get_current_principal

router = APIRouter()


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

class SignatureStartPayload(BaseModel):
    node_id: str
    signer_user_id: str
    page: int
    posx: float
    posy: float
    width: float
    height: float

@router.post("/iniciar", response_model=SendToSignatureResponse, status_code=201)
async def start_signature_flow(
    payload: SignatureStartPayload,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    from app.api.alfresco import parse_node_id
    from app.services.alfresco_client import AlfrescoClient
    ip = client_ip(request)
    parsed_id = parse_node_id(payload.node_id)
    
    current_user_id = principal.user_id.strip().lower()
    target_user_id = payload.signer_user_id.strip().lower()
    
    if target_user_id == "yo" or not target_user_id:
        target_user_id = current_user_id
    
    if target_user_id != current_user_id:
        alf_client = AlfrescoClient()
        users = await alf_client.search_users(target_user_id, max_items=10)
        found = any(u.get("userName", "").lower() == target_user_id for u in users)
        if not found:
            raise HTTPException(status_code=400, detail=f"Usuario Alfresco no encontrado: {target_user_id}")
    
    prep = await frontend_signature_service.get_or_create_preparation(db, parsed_id, principal.user_id, ip)
    
    draft_payload = PreparationDraftSave(
        participants=[
            PreparationParticipant(usrid=target_user_id, orden=1, obliga=True)
        ],
        positions=[
            PreparationPosition(
                pagina=payload.page,
                posx=payload.posx,
                posy=payload.posy,
                ancho=payload.width,
                alto=payload.height,
                rotaci=0,
                orden=1,
                tipfir=TipoFirma.MANUSCRITA.value,
                usrid=target_user_id
            )
        ]
    )
    
    return await frontend_signature_service.save_and_send(db, prep.docid, draft_payload, principal.user_id, ip)



@router.get("/preparacion/{node_id}", response_model=PreparationRead)
async def get_or_create_preparation(
    node_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return await frontend_signature_service.get_or_create_preparation(db, node_id, principal.user_id, client_ip(request))


@router.get("/preparacion/doc/{docid}", response_model=PreparationRead)
async def get_preparation_by_doc(
    docid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return await frontend_signature_service.refresh_preparation(db, docid)


@router.post("/preparacion/{docid}/borrador", response_model=PreparationRead)
async def save_preparation_draft(
    docid: int,
    payload: PreparationDraftSave,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return await frontend_signature_service.save_draft(db, docid, payload, principal.user_id, client_ip(request))


@router.post("/preparacion/{docid}/enviar", response_model=SendToSignatureResponse)
def send_preparation_to_signature(
    docid: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return frontend_signature_service.send_to_signature(db, docid, principal.user_id, client_ip(request))


@router.post("/preparacion/{docid}/guardar-enviar", response_model=SendToSignatureResponse)
async def save_and_send_preparation_to_signature(
    docid: int,
    payload: PreparationDraftSave,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return await frontend_signature_service.save_and_send(db, docid, payload, principal.user_id, client_ip(request))


@router.get("/pendientes", response_model=PendingList)
def list_pending_signatures(
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    items = frontend_signature_service.list_pending(db, principal.user_id)
    return PendingList(items=items, total=len(items))


@router.get("/firmas/{firid}", response_model=SignatureDetail)
def get_signature_detail(
    firid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return frontend_signature_service.get_signature_detail(db, firid, principal.user_id)


@router.post("/firmas/{firid}/confirmar-interna", response_model=SignatureResult)
async def confirm_internal_signature(
    firid: int,
    payload: InternalSignatureConfirm,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    if payload.confirm is not True:
        raise HTTPException(status_code=400, detail="Debe confirmar la firma electronica")
    return await frontend_signature_service.confirm_internal(db, firid, principal.user_id, client_ip(request))


@router.post("/firmas/{firid}/confirmar-manuscrita", response_model=SignatureResult)
async def confirm_handwritten_signature(
    firid: int,
    payload: HandwrittenSignatureConfirm,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return await frontend_signature_service.confirm_handwritten(db, firid, payload.png_data_url, principal.user_id, client_ip(request))


@router.get("/firmas/{firid}/resultado", response_model=SignatureResult)
def get_signature_result(
    firid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return frontend_signature_service.get_result(db, firid, principal.user_id)


@router.get("/firmas/{firid}/resultado/pdf", include_in_schema=False)
def get_signature_result_pdf(
    firid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    path, filename = frontend_signature_service.get_result_pdf_path(db, firid, principal.user_id)
    return FileResponse(path, media_type="application/pdf", filename=filename)


@router.post("/documentos/{docid}/publicar", response_model=PublicationResponse)
def publish_document_to_alfresco(
    docid: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    user_agent = request.headers.get("user-agent")
    service = AlfrescoService()
    try:
        outcome = service.publish_document(
            db=db,
            docid=docid,
            actor_user=principal.user_id,
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


@router.post("/firmas/{firid}/qr", response_model=QrSessionCreateResponse)
def create_signature_qr_session(
    firid: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    sesid, token, fecexp = qr_service.crear_sesion_qr(
        db=db,
        firid=firid,
        requester_user_id=principal.user_id,
        iporig=client_ip(request),
    )
    return QrSessionCreateResponse(
        sesid=sesid,
        token=token,
        qr_url=f"/firma-movil/{token}",
        expires_in=600,
        expires_at=fecexp.isoformat(),
    )


@router.get("/qr/{sesid}/estado", response_model=QrSessionStatusResponse)
def get_qr_session_status(
    sesid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    estado = qr_service.consultar_estado_qr(db, sesid)
    return QrSessionStatusResponse(sesid=sesid, estado=estado)


@router.get("/movil/sesion/{token}", response_model=MobileSessionDetail)
def get_mobile_session_detail(
    token: str,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    sesion = qr_service.obtener_sesion_movil(db, token)
    if sesion.usrid.lower() != principal.user_id.strip().lower():
        raise HTTPException(
            status_code=403,
            detail=f"Usuario autenticado ({principal.user_id}) no corresponde al firmante asignado ({sesion.usrid})",
        )
    firma = sesion.firma
    doc = sesion.documento
    return MobileSessionDetail(
        token=token,
        docnom=doc.docnom if doc else "Documento",
        usrid=sesion.usrid,
        tipfir=firma.tipfir if firma else "MANUSCRITA",
        docid=sesion.docid,
        firid=sesion.firid,
    )


@router.post("/movil/confirmar", response_model=SignatureResult)
async def confirm_mobile_signature(
    payload: MobileSignatureConfirm,
    request: Request,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    return await qr_service.completar_firma_movil(
        db=db,
        raw_token=payload.token,
        png_data_url=payload.png_data_url,
        mobile_user_id=principal.user_id,
        iporig=client_ip(request),
    )

class ValidatedUrlResponse(BaseModel):
    url: str | None = None

@router.get("/validate-return-url", response_model=ValidatedUrlResponse)
def validate_return_url(url: str):
    if not url:
        return ValidatedUrlResponse(url=None)
    
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ValidatedUrlResponse(url=None)
            
        allowed_origins = getattr(settings, "FIRMADOC_ALLOWED_RETURN_ORIGINS", "")
        allowed = [origin.strip() for origin in allowed_origins.split(",") if origin.strip()]
        
        is_allowed = False
        for origin in allowed:
            try:
                allowed_parsed = urlparse(origin)
                # Check if it matches hostname and optionally port
                if parsed.hostname == allowed_parsed.hostname:
                    is_allowed = True
                    break
            except Exception:
                continue
                
        if is_allowed:
            return ValidatedUrlResponse(url=url)
        else:
            return ValidatedUrlResponse(url=None)
    except Exception:
        return ValidatedUrlResponse(url=None)
