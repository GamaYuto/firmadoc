from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.crud_audifir import create_evento_tx
from app.crud.crud_docfir import create_documento, get_active_by_node_version, get_active_by_node, build_active_process_detail
from app.crud.crud_docfirma import crud_docfirma
from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.docpart import DocPart
from app.models.docpaso import DocPaso, EstadoDocPaso
from app.models.flujodoc import Flujodoc
from app.models.flupaso import Flupaso
from app.models.sesionqr import EstadoSesionQr, SesionQr
from app.schemas.firma_frontend import (
    ManagerApprovalCreate,
    ManagerApprovalCreateResponse,
    ManagerApprovalDetail,
    ManagerApprovalStatus,
)
from app.services.alfresco_client import AlfrescoClient
from app.services.frontend_signature_service import frontend_signature_service
from app.services.pdf_signature_service import pdf_signature_service
from app.services.signature_exceptions import (
    SignatureConcurrencyError,
    SignatureError,
    SignatureStateError,
)


FLOW_CODE = "FIRMADOC_MANAGER_APPROVAL"
REJECTION_MESSAGE = (
    "Documento no autorizado por Gerencia. "
    "Por favor contacte al area de Gerencia."
)
_DRAFT_KEY = "firmadoc_preparation_v1"


class ManagerApprovalService:
    def __init__(self, alfresco_client: AlfrescoClient | None = None) -> None:
        self.alfresco_client = alfresco_client or AlfrescoClient()

    @staticmethod
    def _normalize_user(value: str | None) -> str:
        return (value or "").strip().lower()

    def _configured_manager(self) -> str:
        manager = self._normalize_user(settings.FIRMADOC_GERENCIA_USER_ID)
        if not manager or manager in {"admin", "administrator", "administrador"}:
            raise HTTPException(
                status_code=503,
                detail="La autorizacion de Gerencia no esta configurada",
            )
        return manager

    async def _manager_profile(self) -> tuple[str, str]:
        manager = self._configured_manager()
        users = await self.alfresco_client.search_users(manager, max_items=10)
        exact = next(
            (
                user
                for user in users
                if self._normalize_user(user.get("userName")) == manager
            ),
            None,
        )
        if not exact:
            raise HTTPException(
                status_code=503,
                detail="El usuario de Gerencia no existe o esta deshabilitado en Alfresco",
            )
        display_name = (
            (exact.get("displayName") or "").strip()
            or " ".join(
                part
                for part in (
                    (exact.get("firstName") or "").strip(),
                    (exact.get("lastName") or "").strip(),
                )
                if part
            )
            or manager
        )
        return manager, display_name

    @staticmethod
    def _ensure_flow(db: Session, actor_user: str) -> Flupaso:
        flow = db.scalar(
            select(Flujodoc).where(
                Flujodoc.flucod == FLOW_CODE,
                Flujodoc.estado == "ACTIVO",
            )
        )
        if not flow:
            flow = Flujodoc(
                flucod=FLOW_CODE,
                flunom="Autorizacion por Gerencia",
                fluver=1,
                estado="ACTIVO",
                activo=True,
                usrcre=actor_user,
            )
            db.add(flow)
            db.flush()
        step_definition = db.scalar(
            select(Flupaso).where(
                Flupaso.fluid == flow.fluid,
                Flupaso.pascod == "GERENCIA_APROBAR",
            )
        )
        if not step_definition:
            step_definition = Flupaso(
                fluid=flow.fluid,
                pascod="GERENCIA_APROBAR",
                pasnom="Autorizacion de Gerencia",
                pastip="APROBAR",
                orden=1,
                rolreq="Gerencia",
                obliga=True,
                config={"strategy": "ALL", "sequential": True},
                activo=True,
                usrcre=actor_user,
            )
            db.add(step_definition)
            db.flush()
        return step_definition

    @staticmethod
    def _validate_position(payload: ManagerApprovalCreate, pdf_path: str) -> list[dict]:
        with fitz.open(pdf_path) as document:
            if payload.page > document.page_count:
                raise HTTPException(status_code=422, detail="La posicion apunta a una pagina inexistente")
            page = document[payload.page - 1]
            if (
                float(payload.posx + payload.width) > float(page.rect.width)
                or float(payload.posy + payload.height) > float(page.rect.height)
            ):
                raise HTTPException(status_code=422, detail="La posicion excede los limites de la pagina")
            page_sizes = [
                {
                    "page": index + 1,
                    "width": float(item.rect.width),
                    "height": float(item.rect.height),
                    "rotation": int(item.rotation or 0),
                }
                for index, item in enumerate(document)
            ]
        return page_sizes

    async def create_request(
        self,
        db: Session,
        payload: ManagerApprovalCreate,
        requester_user: str,
        requester_name: str,
        ip: str,
    ) -> ManagerApprovalCreateResponse:
        manager, manager_name = await self._manager_profile()
        try:
            node_id = UUID(payload.node_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="node_id invalido") from exc

        source_path: str | None = None
        try:
            metadata = await self.alfresco_client.get_node_metadata(node_id)
            if str(metadata.node_id) != str(node_id) or not metadata.is_file:
                raise HTTPException(status_code=422, detail="El nodo solicitado no es un archivo")
            if metadata.mime_type != "application/pdf":
                raise HTTPException(status_code=415, detail="El documento no es un PDF")
            version = metadata.version_label or "1.0"
            existing = get_active_by_node(db, str(node_id))
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=build_active_process_detail(db, existing),
                )

            source_path, size_bytes, source_hash = await self.alfresco_client.download_node_content(node_id)
            page_sizes = self._validate_position(payload, source_path)
            step_definition = self._ensure_flow(db, requester_user)
            try:
                doc = create_documento(
                    db,
                    nodid=str(node_id),
                    docnom=metadata.name,
                    tamano=size_bytes,
                    verini=version,
                    hasori=source_hash.lower(),
                    usrcre=requester_user,
                )
            except IntegrityError as exc:
                db.rollback()
                existing = get_active_by_node(db, str(node_id))
                if existing:
                    detail = build_active_process_detail(db, existing)
                else:
                    detail = {
                        "code": "ACTIVE_PROCESS",
                        "message": "Ya existe un proceso activo para este documento",
                    }
                raise HTTPException(status_code=409, detail=detail) from exc
            doc.fluid = step_definition.fluid
            doc.estado = EstadoDoc.EN_CURSO.value

            now = datetime.now(timezone.utc)
            position = {
                "pagina": payload.page,
                "posx": str(payload.posx),
                "posy": str(payload.posy),
                "ancho": str(payload.width),
                "alto": str(payload.height),
                "rotaci": 0,
                "orden": 1,
                "tipfir": TipoFirma.INTERNA.value,
                "usrid": manager,
            }
            step = DocPaso(
                docid=doc.docid,
                pasid=step_definition.pasid,
                orden=1,
                estado=EstadoDocPaso.DISPONIBLE.value,
                fecdis=now,
                pastip="APROBAR",
                rolreq=(settings.FIRMADOC_GERENCIA_CARGO or "Gerencia").strip(),
                obliga=True,
                config={"strategy": "ALL", "sequential": True},
                result={
                    "page_sizes": page_sizes,
                    _DRAFT_KEY: {
                        "participants": [{"usrid": manager, "orden": 1, "obliga": True}],
                        "positions": [position],
                        "sent_users": [manager],
                    },
                    "requester_name": requester_name,
                },
                verlock=1,
                activo=True,
                usrcre=requester_user,
            )
            db.add(step)
            db.flush()
            participant = DocPart(
                dpasid=step.dpasid,
                usrid=manager,
                nomcom=manager_name,
                correo=f"{manager}@alfresco.local",
                rolpro=(settings.FIRMADOC_GERENCIA_CARGO or "Gerencia").strip(),
                orden=1,
                obliga=True,
                estado="DISPONIBLE",
                fecdis=now,
                verlock=1,
                usrcre=requester_user,
            )
            db.add(participant)
            db.flush()
            signature = crud_docfirma.create_signature_attempt(
                db=db,
                docid=doc.docid,
                parid=participant.parid,
                tipfir=TipoFirma.INTERNA.value,
                verori=doc.verini,
                hasori=doc.hasori,
                secuen=1,
                intnum=1,
                usrcre=requester_user,
            )
            crud_docfirma.create_signature_positions(db, signature.firid, [position])

            raw_token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(
                minutes=max(1, settings.FIRMADOC_GERENCIA_TOKEN_EXPIRE_MINUTES)
            )
            session = SesionQr(
                firid=signature.firid,
                docid=doc.docid,
                usrid=manager,
                tokhas=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
                estado=EstadoSesionQr.PENDIENTE.value,
                fecexp=expires_at,
                iporig=ip,
            )
            db.add(session)
            create_evento_tx(
                db,
                evento="GER_SOL",
                enttip="FIRMA",
                entid=signature.firid,
                docid=doc.docid,
                usrid=requester_user,
                iporig=ip,
                detalle=json.dumps(
                    {"manager": manager, "version": doc.verini, "source_hash": doc.hasori},
                    separators=(",", ":"),
                ),
            )
            db.commit()
            return ManagerApprovalCreateResponse(
                firid=signature.firid,
                docid=doc.docid,
                status="PENDIENTE",
                approval_url=f"/autorizar-gerencia#{raw_token}",
                expires_at=expires_at,
            )
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Ya existe un proceso activo para este documento") from exc
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            if source_path:
                try:
                    os.unlink(source_path)
                except OSError:
                    pass

    def _context(self, db: Session, raw_token: str, *, lock: bool = False):
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        query = (
            select(SesionQr, DocFirma, DocPart, DocPaso, DocFir)
            .join(DocFirma, DocFirma.firid == SesionQr.firid)
            .join(DocPart, DocPart.parid == DocFirma.parid)
            .join(DocPaso, DocPaso.dpasid == DocPart.dpasid)
            .join(DocFir, DocFir.docid == SesionQr.docid)
            .where(SesionQr.tokhas == token_hash)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = db.execute(query).first()
        if not row:
            raise HTTPException(status_code=404, detail="Solicitud de Gerencia no encontrada")
        session, signature, participant, step, doc = row
        if (
            signature.docid != session.docid
            or participant.parid != signature.parid
            or step.dpasid != participant.dpasid
            or step.pastip != "APROBAR"
            or signature.tipfir != TipoFirma.INTERNA.value
        ):
            raise HTTPException(status_code=404, detail="Solicitud de Gerencia no encontrada")
        return session, signature, participant, step, doc

    def _assert_manager(self, actor_user: str, expected_user: str) -> None:
        configured = self._configured_manager()
        if (
            self._normalize_user(actor_user) != configured
            or self._normalize_user(expected_user) != configured
        ):
            raise HTTPException(status_code=403, detail="Solo el gerente asignado puede responder esta solicitud")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _expire_if_needed(self, db: Session, context) -> None:
        session, signature, participant, step, doc = context
        if (
            session.estado == EstadoSesionQr.PENDIENTE.value
            and datetime.now(timezone.utc) > self._aware(session.fecexp)
        ):
            now = datetime.now(timezone.utc)
            session.estado = EstadoSesionQr.EXPIRADO.value
            signature.estado = EstadoDocFirma.CANCELADA.value
            signature.motivo = "Solicitud de Gerencia expirada"
            signature.fecfin = now
            signature.revnum += 1
            participant.estado = "CANCELADO"
            participant.motivo = "Solicitud de Gerencia expirada"
            participant.fecini = participant.fecini or now
            participant.fecfin = now
            step.estado = EstadoDocPaso.CANCELADO.value
            step.motivo = "Solicitud de Gerencia expirada"
            step.fecini = step.fecini or now
            step.fecfin = now
            doc.estado = EstadoDoc.CANCELADO.value
            create_evento_tx(
                db, "GER_EXP", "FIRMA", signature.firid, doc.docid, session.usrid,
                detalle="Solicitud de Gerencia expirada",
            )
            db.commit()
            raise HTTPException(status_code=410, detail="La solicitud de Gerencia ha expirado")

    async def _validate_remote(self, db: Session, context, actor_user: str, ip: str) -> str:
        _session, _signature, _participant, _step, doc = context
        source_path: str | None = None
        metadata = await self.alfresco_client.get_node_metadata(UUID(doc.nodid))
        source_path, _size, source_hash = await self.alfresco_client.download_node_content(UUID(doc.nodid))
        if (
            str(metadata.node_id) != doc.nodid
            or (metadata.version_label or "1.0") != doc.verini
            or source_hash.lower() != doc.hasori.lower()
        ):
            try:
                os.unlink(source_path)
            except OSError:
                pass
            self._mark_conflict(db, context, actor_user, ip)
            raise HTTPException(status_code=409, detail="El documento cambio en Alfresco")
        return source_path

    @staticmethod
    def _mark_conflict(db: Session, context, actor_user: str, ip: str) -> None:
        session, signature, participant, step, doc = context
        now = datetime.now(timezone.utc)
        session.estado = EstadoSesionQr.CANCELADO.value
        session.fecusa = now
        signature.estado = EstadoDocFirma.CONFLICTO.value
        signature.errcod = "GER_REMOTE_CONFLICT"
        signature.motivo = "El documento cambio en Alfresco"
        signature.fecfin = now
        signature.revnum += 1
        participant.estado = "RECHAZADO"
        participant.motivo = "El documento cambio en Alfresco"
        participant.fecini = participant.fecini or now
        participant.fecfin = now
        step.estado = EstadoDocPaso.RECHAZADO.value
        step.motivo = "El documento cambio en Alfresco"
        step.fecini = step.fecini or now
        step.fecfin = now
        doc.estado = EstadoDoc.RECHAZADO.value
        create_evento_tx(
            db, "GER_CON", "FIRMA", signature.firid, doc.docid, actor_user, ip,
            "Version o hash remoto distinto al origen aprobado",
        )
        db.commit()

    async def get_detail(
        self, db: Session, raw_token: str, actor_user: str, ip: str
    ) -> ManagerApprovalDetail:
        context = self._context(db, raw_token)
        session, signature, participant, step, doc = context
        self._assert_manager(actor_user, session.usrid)
        self._expire_if_needed(db, context)
        if session.estado != EstadoSesionQr.PENDIENTE.value or signature.estado != EstadoDocFirma.INICIADA.value:
            raise HTTPException(status_code=409, detail="La solicitud de Gerencia ya fue resuelta")
        source_path = await self._validate_remote(db, context, actor_user, ip)
        try:
            create_evento_tx(
                db, "GER_VIEW", "FIRMA", signature.firid, doc.docid, actor_user, ip,
                "Solicitud de Gerencia visualizada",
            )
            db.commit()
        finally:
            try:
                os.unlink(source_path)
            except OSError:
                pass
        requester_name = (step.result or {}).get("requester_name") or doc.usrcre
        return ManagerApprovalDetail(
            firid=signature.firid,
            docid=doc.docid,
            document_name=doc.docnom,
            requester_name=requester_name,
            requested_at=session.feccre,
            manager_name=participant.nomcom,
            manager_role=participant.rolpro or step.rolreq,
            status="PENDIENTE",
        )

    async def get_document_path(
        self, db: Session, raw_token: str, actor_user: str, ip: str
    ) -> tuple[str, str]:
        context = self._context(db, raw_token)
        session, signature, _participant, _step, doc = context
        self._assert_manager(actor_user, session.usrid)
        self._expire_if_needed(db, context)
        if session.estado != EstadoSesionQr.PENDIENTE.value or signature.estado != EstadoDocFirma.INICIADA.value:
            raise HTTPException(status_code=409, detail="La solicitud de Gerencia ya fue resuelta")
        path = await self._validate_remote(db, context, actor_user, ip)
        return path, doc.docnom

    async def authorize(self, db: Session, raw_token: str, actor_user: str, ip: str):
        context = self._context(db, raw_token)
        session, signature, _participant, _step, _doc = context
        self._assert_manager(actor_user, session.usrid)
        self._expire_if_needed(db, context)
        if (
            session.estado != EstadoSesionQr.PENDIENTE.value
            or signature.estado
            not in (EstadoDocFirma.INICIADA.value, EstadoDocFirma.GENERADA.value)
        ):
            raise HTTPException(status_code=409, detail="La solicitud de Gerencia ya fue resuelta")
        source_path = await self._validate_remote(db, context, actor_user, ip)
        try:
            if signature.estado == EstadoDocFirma.INICIADA.value:
                pdf_signature_service.generate_signature_pdf_and_mark_generated(
                    db,
                    firid=signature.firid,
                    source_pdf_path=source_path,
                    signature_image_path=None,
                    expected_source_hash=signature.hasori,
                    usrmod=actor_user,
                )

            locked = self._context(db, raw_token, lock=True)
            session, signature, _participant, _step, _doc = locked
            self._assert_manager(actor_user, session.usrid)
            if (
                session.estado != EstadoSesionQr.PENDIENTE.value
                or signature.estado != EstadoDocFirma.GENERADA.value
            ):
                raise HTTPException(status_code=409, detail="La solicitud de Gerencia ya fue resuelta")
            frontend_signature_service._advance_sequential_signature(
                db, signature.firid, actor_user, ip
            )
            session.estado = EstadoSesionQr.USADO.value
            session.fecusa = datetime.now(timezone.utc)
            create_evento_tx(
                db, "GER_AUT", "FIRMA", signature.firid, signature.docid, actor_user, ip,
                "Documento autorizado electronicamente por Gerencia",
            )
            db.commit()
            return frontend_signature_service._build_result(
                db,
                signature.firid,
                "Documento autorizado por Gerencia y pendiente de publicacion en Alfresco.",
                actor_user,
            )
        except HTTPException:
            db.rollback()
            raise
        except (SignatureConcurrencyError, SignatureStateError) as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="La solicitud de Gerencia ya esta siendo procesada") from exc
        except SignatureError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            try:
                os.unlink(source_path)
            except OSError:
                pass

    def reject(
        self, db: Session, raw_token: str, actor_user: str, reason: str, ip: str
    ) -> ManagerApprovalStatus:
        context = self._context(db, raw_token, lock=True)
        session, signature, participant, step, doc = context
        self._assert_manager(actor_user, session.usrid)
        self._expire_if_needed(db, context)
        if session.estado != EstadoSesionQr.PENDIENTE.value or signature.estado != EstadoDocFirma.INICIADA.value:
            raise HTTPException(status_code=409, detail="La solicitud de Gerencia ya fue resuelta")
        now = datetime.now(timezone.utc)
        clean_reason = reason.strip()[:500]
        session.estado = EstadoSesionQr.CANCELADO.value
        session.fecusa = now
        signature.estado = EstadoDocFirma.CANCELADA.value
        signature.motivo = clean_reason
        signature.fecfin = now
        signature.revnum += 1
        participant.estado = "RECHAZADO"
        participant.motivo = clean_reason
        participant.fecini = participant.fecini or now
        participant.fecfin = now
        participant.verlock += 1
        step.estado = EstadoDocPaso.RECHAZADO.value
        step.motivo = clean_reason
        step.fecini = step.fecini or now
        step.fecfin = now
        step.verlock += 1
        doc.estado = EstadoDoc.RECHAZADO.value
        create_evento_tx(
            db, "GER_REC", "FIRMA", signature.firid, doc.docid, actor_user, ip,
            clean_reason,
        )
        db.commit()
        return ManagerApprovalStatus(
            firid=signature.firid,
            docid=doc.docid,
            status="RECHAZADO",
            message=REJECTION_MESSAGE,
        )

    def status(self, db: Session, firid: int, actor_user: str) -> ManagerApprovalStatus:
        row = db.execute(
            select(SesionQr, DocFirma, DocPart, DocPaso, DocFir)
            .join(DocFirma, DocFirma.firid == SesionQr.firid)
            .join(DocPart, DocPart.parid == DocFirma.parid)
            .join(DocPaso, DocPaso.dpasid == DocPart.dpasid)
            .join(DocFir, DocFir.docid == SesionQr.docid)
            .where(SesionQr.firid == firid, DocPaso.pastip == "APROBAR")
            .order_by(SesionQr.sesid.desc())
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Solicitud de Gerencia no encontrada")
        session, signature, participant, _step, doc = row
        actor = self._normalize_user(actor_user)
        if actor not in {self._normalize_user(doc.usrcre), self._normalize_user(participant.usrid)}:
            raise HTTPException(status_code=403, detail="No autorizado para consultar esta solicitud")
        if (
            session.estado == EstadoSesionQr.PENDIENTE.value
            and datetime.now(timezone.utc) > self._aware(session.fecexp)
        ):
            try:
                self._expire_if_needed(db, row)
            except HTTPException as exc:
                if exc.status_code != 410:
                    raise
        if session.estado == EstadoSesionQr.EXPIRADO.value:
            status = "EXPIRADO"
            message = "La solicitud de Gerencia expiro."
        elif signature.estado == EstadoDocFirma.CONFLICTO.value:
            status = "CONFLICTO"
            message = "El documento cambio en Alfresco."
        elif doc.estado == EstadoDoc.RECHAZADO.value:
            status = "RECHAZADO"
            message = REJECTION_MESSAGE
        elif signature.estado == EstadoDocFirma.COMPLETADA.value:
            status = "AUTORIZADO"
            message = "Documento autorizado por Gerencia."
        else:
            status = "PENDIENTE"
            message = "Pendiente de autorizacion por Gerencia."
        return ManagerApprovalStatus(
            firid=signature.firid,
            docid=doc.docid,
            status=status,
            message=message,
            result_url=f"/firmas/{signature.firid}" if status == "AUTORIZADO" else None,
        )


manager_approval_service = ManagerApprovalService()
