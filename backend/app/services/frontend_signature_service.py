from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable
from uuid import UUID

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.identity import FakeIdentityResolver, IdentitySnapshot
from app.crud.crud_audifir import create_evento_tx
from app.crud.crud_docfirma import crud_docfirma
from app.crud.crud_docfir import get_active_by_node_version
from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.docpart import DocPart
from app.models.docpaso import DocPaso, EstadoDocPaso
from app.models.flujodoc import Flujodoc
from app.models.flupaso import Flupaso
from app.schemas.firma_frontend import (
    PageInfo,
    ParticipantRead,
    PendingItem,
    PositionRead,
    PreparationDraftSave,
    PreparationRead,
    PreparationPosition,
    SendToSignatureResponse,
    SignatureDetail,
    SignatureResult,
)
from app.schemas.docfirma import ResultContract, ResultFase, ResultFlag
from app.services.alfresco_client import AlfrescoClient
from app.services.document_service import DocumentService
from app.services.pdf_signature_service import pdf_signature_service
from app.services.pdf_validation_service import pdf_validation_service
from app.services.signature_exceptions import SignatureError, SignaturePayloadError
from app.services.temporary_artifact_service import temporary_artifact_service


DEFAULT_FLOW_CODE = "FIRMADOC_MVP_SIGN"
_DRAFT_KEY = "firmadoc_preparation_v1"
_ALLOWED_TYPES = {TipoFirma.MANUSCRITA.value, TipoFirma.INTERNA.value}


@dataclass(frozen=True, slots=True)
class _SourceArtifact:
    path: Path
    expected_sha256: str
    cleanup_on_failure: bool


class FrontendSignatureService:
    def __init__(self, alfresco_client: AlfrescoClient | None = None) -> None:
        self.alfresco_client = alfresco_client or AlfrescoClient()
        self.document_service = DocumentService(self.alfresco_client)
        self.identity_resolver = FakeIdentityResolver()

    async def get_or_create_preparation(
        self,
        db: Session,
        node_id: UUID,
        actor_user: str,
        ip: str,
    ) -> PreparationRead:
        metadata = await self.alfresco_client.get_node_metadata(node_id)
        if not metadata.is_file:
            raise HTTPException(status_code=422, detail="El nodo solicitado no es un archivo")
        if metadata.mime_type != "application/pdf":
            raise HTTPException(status_code=415, detail="El documento no es un PDF")

        version = metadata.version_label or "1.0"
        doc = get_active_by_node_version(db, str(node_id), version)
        if not doc:
            doc = await self.document_service.iniciar_proceso(db, node_id, actor_user, ip)

        step = self._ensure_signing_step(db, doc, actor_user)
        pages = await self._get_pdf_pages(node_id)
        response = self._build_preparation_read(db, doc, step, pages)
        db.commit()
        db.refresh(doc)
        db.refresh(step)
        return response

    async def refresh_preparation(self, db: Session, docid: int) -> PreparationRead:
        doc = self._get_doc(db, docid)
        step = self._get_signing_step(db, doc.docid)
        pages = await self._get_pdf_pages(UUID(doc.nodid))
        response = self._build_preparation_read(db, doc, step, pages)
        db.commit()
        return response

    async def save_draft(
        self,
        db: Session,
        docid: int,
        payload: PreparationDraftSave,
        actor_user: str,
        ip: str,
    ) -> PreparationRead:
        response = await self._save_draft_tx(db, docid, payload, actor_user, ip)
        db.commit()
        return response

    async def save_and_send(
        self,
        db: Session,
        docid: int,
        payload: PreparationDraftSave,
        actor_user: str,
        ip: str,
    ) -> SendToSignatureResponse:
        await self._save_draft_tx(db, docid, payload, actor_user, ip)
        return self._send_to_signature_tx(db, docid, actor_user, ip, commit=True)

    def _is_admin_or_gestor(self, actor: Any) -> bool:
        if hasattr(actor, "is_gestor"):
            return actor.is_gestor
        if isinstance(actor, str):
            u = actor.strip().lower()
            return u in ("admin", "administrador", "gestor", "preparador")
        return False

    def _actor_id(self, actor: Any) -> str:
        if hasattr(actor, "user_id"):
            return actor.user_id.strip().lower()
        return str(actor).strip().lower()

    async def _save_draft_tx(
        self,
        db: Session,
        docid: int,
        payload: PreparationDraftSave,
        actor_user: str,
        ip: str,
    ) -> PreparationRead:
        doc = self._get_doc(db, docid)
        self._assert_editable_document(doc)
        actor_id = self._actor_id(actor_user)
        if actor_id != (doc.usrcre or "").strip().lower() and not self._is_admin_or_gestor(actor_user):
            raise HTTPException(status_code=403, detail="No autorizado para modificar el borrador de este documento")
        step = self._get_signing_step_for_update(db, doc.docid)
        pages = await self._ensure_page_sizes(db, doc, step)
        self._validate_participants(payload)
        self._validate_positions(payload.positions, pages)

        participants = [self._upsert_participant(db, step, participant, actor_user) for participant in payload.participants]
        draft_positions = [self._position_to_json(pos) for pos in payload.positions]
        current_result = step.result or {}
        current_draft = current_result.get(_DRAFT_KEY) or {}
        current_result[_DRAFT_KEY] = {
            "participants": [self._participant_to_json(participant) for participant in payload.participants],
            "positions": draft_positions,
            "sent_users": current_draft.get("sent_users", []),
            "updated_by": actor_user,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        step.result = current_result
        flag_modified(step, "result")
        doc.estado = EstadoDoc.BORRADOR.value
        doc.usrmod = actor_user
        doc.fecmod = datetime.now(timezone.utc)
        create_evento_tx(
            db,
            evento="PREP_SAVE",
            enttip="PASO",
            entid=step.dpasid,
            docid=doc.docid,
            usrid=actor_user,
            iporig=ip,
            detalle="Borrador de posiciones de firma guardado",
        )
        return self._build_preparation_read(db, doc, step, pages)

    def send_to_signature(
        self,
        db: Session,
        docid: int,
        actor_user: str,
        ip: str,
    ) -> SendToSignatureResponse:
        return self._send_to_signature_tx(db, docid, actor_user, ip, commit=True)

    def _send_to_signature_tx(
        self,
        db: Session,
        docid: int,
        actor_user: str,
        ip: str,
        *,
        commit: bool,
    ) -> SendToSignatureResponse:
        doc = self._get_doc_for_update(db, docid)
        self._assert_editable_document(doc)
        actor_id = self._actor_id(actor_user)
        if actor_id != (doc.usrcre or "").strip().lower() and not self._is_admin_or_gestor(actor_user):
            raise HTTPException(status_code=403, detail="No autorizado para enviar este documento a firma")
        step = self._get_signing_step_for_update(db, doc.docid)
        draft = self._get_draft(step)
        if not draft:
            raise HTTPException(status_code=400, detail="No hay borrador de firma para enviar")

        active_for_doc = crud_docfirma.get_active_attempt_by_document(db, doc.docid)
        if active_for_doc:
            raise HTTPException(status_code=409, detail="Ya existe una firma activa para este documento")

        participant_data = self._next_participant_to_send(draft)
        participant = self._get_participant_by_user_for_update(db, step.dpasid, participant_data["usrid"])
        if not participant:
            raise HTTPException(status_code=400, detail="El firmante del borrador no existe")
        if participant.estado not in ("PENDIENTE", "DISPONIBLE"):
            raise HTTPException(status_code=409, detail="El firmante ya no esta disponible")

        positions = [pos for pos in draft["positions"] if pos["usrid"] == participant.usrid]
        if not positions:
            raise HTTPException(status_code=400, detail="El firmante no tiene posiciones para enviar")
        self._validate_position_dicts(positions, self._page_sizes_from_result(step))
        if self._active_attempt_for_participant(db, participant.parid):
            raise HTTPException(status_code=409, detail="Ya existe una firma activa para este firmante")

        tipfir = positions[0]["tipfir"]
        if any(pos["tipfir"] != tipfir for pos in positions):
            raise HTTPException(status_code=422, detail="Un firmante no puede mezclar tipos de firma en un intento")
        docfirma = crud_docfirma.create_signature_attempt(
            db=db,
            docid=doc.docid,
            parid=participant.parid,
            tipfir=tipfir,
            verori=doc.verini,
            hasori=doc.hasori.lower(),
            secuen=crud_docfirma.get_next_document_sequence(db, doc.docid),
            intnum=crud_docfirma.get_next_participant_attempt(db, participant.parid),
            usrcre=actor_user,
        )
        crud_docfirma.create_signature_positions(db, docfirma.firid, positions)

        participant.estado = "DISPONIBLE"
        participant.fecdis = participant.fecdis or datetime.now(timezone.utc)
        participant.usrmod = actor_user
        participant.fecmod = datetime.now(timezone.utc)
        step.estado = "DISPONIBLE"
        step.fecdis = step.fecdis or datetime.now(timezone.utc)
        step.usrmod = actor_user
        step.fecmod = datetime.now(timezone.utc)
        doc.estado = EstadoDoc.PENDIENTE_FIRMA.value
        doc.usrmod = actor_user
        doc.fecmod = datetime.now(timezone.utc)

        create_evento_tx(
            db,
            evento="PREP_SEND",
            enttip="FIRMA",
            entid=docfirma.firid,
            docid=doc.docid,
            usrid=actor_user,
            iporig=ip,
            detalle="Preparacion enviada a firma; publicacion Alfresco no ejecutada",
        )
        draft["sent_users"] = sorted(set(draft.get("sent_users", [])) | {participant.usrid})
        current_result = step.result or {}
        current_result[_DRAFT_KEY] = draft
        step.result = current_result
        flag_modified(step, "result")
        if commit:
            db.commit()
        return SendToSignatureResponse(
            firid=docfirma.firid,
            docid=doc.docid,
            parid=participant.parid,
            status=docfirma.estado,
            positions=[
                PositionRead(
                    pagina=int(pos["pagina"]),
                    posx=float(pos["posx"]),
                    posy=float(pos["posy"]),
                    ancho=float(pos["ancho"]),
                    alto=float(pos["alto"]),
                    rotaci=int(pos.get("rotaci", 0)),
                    orden=int(pos["orden"]),
                    tipfir=tipfir,
                    usrid=participant.usrid,
                    saved=True,
                )
                for pos in positions
            ],
        )

    def list_pending(self, db: Session, actor_user: str) -> list[PendingItem]:
        user = actor_user.strip().lower()
        rows = db.execute(
            select(DocFirma, DocPart, DocPaso, DocFir)
            .join(DocPart, DocPart.parid == DocFirma.parid)
            .join(DocPaso, DocPaso.dpasid == DocPart.dpasid)
            .join(DocFir, DocFir.docid == DocFirma.docid)
            .where(
                DocPart.usrid == user,
                DocPart.estado.in_(["DISPONIBLE", "EN_PROCESO"]),
                DocFirma.estado == EstadoDocFirma.INICIADA.value,
            )
            .order_by(DocPart.fecdis.desc().nullslast(), DocFirma.firid.desc())
        ).all()
        return [
            PendingItem(
                firid=firma.firid,
                docid=doc.docid,
                parid=part.parid,
                document_name=doc.docnom,
                etapa=step.pastip,
                rol=part.rolpro or step.rolreq,
                fecha=part.fecdis or firma.feccre,
                estado=part.estado,
                tipfir=firma.tipfir,
            )
            for firma, part, step, doc in rows
        ]

    def get_signature_detail(self, db: Session, firid: int, actor_user: str) -> SignatureDetail:
        firma, participant, step, doc = self._get_signature_context(db, firid)
        self._assert_signature_viewer(actor_user, participant, doc, firma)
        positions = crud_docfirma.get_positions_by_attempt(db, firid)
        return SignatureDetail(
            firid=firma.firid,
            docid=doc.docid,
            parid=participant.parid,
            node_id=doc.nodid,
            document_name=doc.docnom,
            document_status=doc.estado,
            signer_user=participant.usrid,
            signer_name=participant.nomcom,
            signer_role=participant.rolpro,
            tipfir=firma.tipfir,
            estado=firma.estado,
            revnum=firma.revnum,
            participant_verlock=participant.verlock,
            positions=[
                PositionRead(
                    pagina=pos.pagina,
                    posx=float(pos.posx),
                    posy=float(pos.posy),
                    ancho=float(pos.ancho),
                    alto=float(pos.alto),
                    rotaci=pos.rotaci,
                    orden=pos.orden,
                    tipfir=firma.tipfir,
                    usrid=participant.usrid,
                    saved=True,
                )
                for pos in positions
            ],
            max_png_size=settings.FIRMADOC_MAX_PNG_SIZE,
        )

    async def confirm_internal(self, db: Session, firid: int, actor_user: str, ip: str) -> SignatureResult:
        firma, participant, _step, doc = self._get_signature_context(db, firid)
        self._validate_confirmation(firma, participant, actor_user, expected_type=TipoFirma.INTERNA.value)
        source = await self._resolve_source_for_signature(db, doc)
        completed = False
        try:
            pdf_signature_service.generate_signature_pdf_and_mark_generated(
                db,
                firid=firma.firid,
                source_pdf_path=source.path,
                signature_image_path=None,
                expected_source_hash=source.expected_sha256,
                usrmod=actor_user,
            )
            has_next = self._advance_sequential_signature(db, firid, actor_user, ip)
            db.commit()
            completed = True
            message = (
                "Firma registrada. Pendiente del siguiente firmante."
                if has_next
                else "Firma registrada. Documento consolidado completado y pendiente de publicacion en Alfresco."
            )
            return self._build_result(db, firid, message, actor_user)
        except SignatureError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            if completed or source.cleanup_on_failure:
                self._cleanup_path(source.path)

    async def confirm_handwritten(
        self,
        db: Session,
        firid: int,
        png_data_url: str,
        actor_user: str,
        ip: str = "",
        iporig: str | None = None,
    ) -> SignatureResult:
        client_ip = ip or iporig or ""
        firma, participant, _step, doc = self._get_signature_context(db, firid)
        self._validate_confirmation(firma, participant, actor_user, expected_type=TipoFirma.MANUSCRITA.value)
        source = await self._resolve_source_for_signature(db, doc)
        image_path = self._write_signature_png(png_data_url)
        completed = False
        try:
            pdf_signature_service.generate_signature_pdf_and_mark_generated(
                db,
                firid=firma.firid,
                source_pdf_path=source.path,
                signature_image_path=image_path,
                expected_source_hash=source.expected_sha256,
                usrmod=actor_user,
            )
            has_next = self._advance_sequential_signature(db, firid, actor_user, client_ip)
            db.commit()
            completed = True
            message = (
                "Firma registrada. Pendiente del siguiente firmante."
                if has_next
                else "Firma registrada. Documento consolidado completado y pendiente de publicacion en Alfresco."
            )
            return self._build_result(db, firid, message, actor_user)
        except SignatureError as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            if completed or source.cleanup_on_failure:
                self._cleanup_path(source.path)

    def get_result(self, db: Session, firid: int, actor_user: str) -> SignatureResult:
        firma, participant, _step, doc = self._get_signature_context(db, firid)
        self._assert_signature_viewer(actor_user, participant, doc, firma)
        return self._build_result(db, firid, "Resultado de firma consultado", actor_user)

    def get_result_pdf_path(self, db: Session, firid: int, actor_user: str) -> tuple[Path, str]:
        firma, participant, _step, doc = self._get_signature_context(db, firid)
        self._assert_signature_viewer(actor_user, participant, doc, firma)
        if doc.estado not in (EstadoDoc.PENDIENTE_PUBLICACION.value, EstadoDoc.COMPLETADO.value):
            raise HTTPException(status_code=409, detail="El resultado local todavia no esta disponible")

        temporary_artifact_service.cleanup_expired()
        artifact_path = temporary_artifact_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf")
        if not artifact_path or not artifact_path.exists():
            raise HTTPException(status_code=404, detail="No hay PDF generado localmente para esta firma")

        filename = f"{Path(doc.docnom).stem}-resultado.pdf"
        return artifact_path, filename

    def _ensure_signing_step(self, db: Session, doc: DocFir, actor_user: str) -> DocPaso:
        existing = self._get_signing_step(db, doc.docid, required=False)
        if existing:
            return existing

        flow = db.scalars(
            select(Flujodoc).where(Flujodoc.flucod == DEFAULT_FLOW_CODE, Flujodoc.estado == "ACTIVO")
        ).first()
        if not flow:
            flow = Flujodoc(
                flucod=DEFAULT_FLOW_CODE,
                flunom="FirmaDoc MVP Firma",
                fluver=1,
                estado="ACTIVO",
                usrcre=actor_user,
                activo=True,
            )
            db.add(flow)
            db.flush()
            flupaso = Flupaso(
                fluid=flow.fluid,
                pascod="FIRMA",
                pasnom="Firma",
                pastip="FIRMAR",
                orden=1,
                rolreq="Firmante",
                obliga=True,
                plazo=60,
                config={"part_estrategia": "TODOS", "part_modo": "SECUENCIAL"},
                usrcre=actor_user,
                activo=True,
            )
            db.add(flupaso)
            db.flush()
        else:
            flupaso = db.scalars(
                select(Flupaso)
                .where(Flupaso.fluid == flow.fluid, Flupaso.pastip == "FIRMAR", Flupaso.activo.is_(True))
                .order_by(Flupaso.orden)
            ).first()
            if not flupaso:
                raise HTTPException(status_code=500, detail="El flujo de firma no tiene paso FIRMAR")

        doc.fluid = flow.fluid
        step = DocPaso(
            docid=doc.docid,
            pasid=flupaso.pasid,
            orden=flupaso.orden,
            estado="PENDIENTE",
            pastip=flupaso.pastip,
            rolreq=flupaso.rolreq,
            obliga=flupaso.obliga,
            plazo=flupaso.plazo,
            config=flupaso.config,
            usrcre=actor_user,
            verlock=1,
            activo=True,
        )
        db.add(step)
        db.flush()
        create_evento_tx(db, "PAS_INST", "PASO", step.dpasid, doc.docid, actor_user, detalle="Paso FIRMAR creado para MVP frontend")
        return step

    async def _get_pdf_pages(self, node_id: UUID) -> list[PageInfo]:
        temp_path = None
        try:
            temp_path, _size, _sha = await self.alfresco_client.download_node_content(node_id)
            pdf_validation_service.validate_source_pdf(temp_path)
            with fitz.open(temp_path) as document:
                return [
                    PageInfo(
                        page=index + 1,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        rotation=int(page.rotation or 0),
                    )
                    for index, page in enumerate(document)
                ]
        finally:
            self._cleanup_path(temp_path)

    def _build_preparation_read(
        self,
        db: Session,
        doc: DocFir,
        step: DocPaso,
        pages: list[PageInfo],
    ) -> PreparationRead:
        self._store_page_sizes(step, pages)
        participants = [
            ParticipantRead(
                parid=p.parid,
                usrid=p.usrid,
                nomcom=p.nomcom,
                correo=p.correo,
                rolpro=p.rolpro,
                orden=p.orden,
                estado=p.estado,
                verlock=p.verlock,
            )
            for p in db.scalars(select(DocPart).where(DocPart.dpasid == step.dpasid).order_by(DocPart.orden)).all()
        ]
        active = db.scalars(
            select(DocFirma)
            .where(DocFirma.docid == doc.docid, DocFirma.estado == EstadoDocFirma.INICIADA.value)
            .order_by(DocFirma.firid.desc())
        ).first()
        positions = self._read_positions(db, step, active)
        return PreparationRead(
            docid=doc.docid,
            node_id=doc.nodid,
            document_name=doc.docnom,
            version=doc.verini,
            status=doc.estado,
            hash_original=doc.hasori,
            pages=pages,
            participants=participants,
            positions=positions,
            active_firid=active.firid if active else None,
        )

    def _read_positions(self, db: Session, step: DocPaso, active: DocFirma | None) -> list[PositionRead]:
        if active:
            participant = db.get(DocPart, active.parid)
            return [
                PositionRead(
                    pagina=pos.pagina,
                    posx=float(pos.posx),
                    posy=float(pos.posy),
                    ancho=float(pos.ancho),
                    alto=float(pos.alto),
                    rotaci=pos.rotaci,
                    orden=pos.orden,
                    tipfir=active.tipfir,
                    usrid=participant.usrid if participant else "",
                    saved=True,
                )
                for pos in crud_docfirma.get_positions_by_attempt(db, active.firid)
            ]
        draft = self._get_draft(step)
        if not draft:
            return []
        return [
            PositionRead(
                pagina=int(pos["pagina"]),
                posx=float(pos["posx"]),
                posy=float(pos["posy"]),
                ancho=float(pos["ancho"]),
                alto=float(pos["alto"]),
                rotaci=int(pos.get("rotaci", 0)),
                orden=int(pos["orden"]),
                tipfir=pos["tipfir"],
                usrid=pos["usrid"],
                saved=True,
            )
            for pos in draft.get("positions", [])
        ]

    def _upsert_participant(self, db: Session, step: DocPaso, participant_payload, actor_user: str) -> DocPart:
        user_id = participant_payload.usrid
        participant = self._get_participant_by_user_for_update(db, step.dpasid, user_id)
        snapshot = self.identity_resolver.resolve_user(user_id)
        if participant:
            if participant.estado not in ("PENDIENTE", "DISPONIBLE"):
                raise HTTPException(status_code=409, detail="El firmante ya no puede modificarse")
            participant.nomcom = snapshot.nomcom
            participant.correo = snapshot.correo
            participant.rolpro = snapshot.rolpro
            participant.orden = participant_payload.orden
            participant.obliga = participant_payload.obliga
            participant.usrmod = actor_user
            participant.fecmod = datetime.now(timezone.utc)
            return participant

        participant = DocPart(
            dpasid=step.dpasid,
            usrid=snapshot.usrid,
            nomcom=snapshot.nomcom,
            correo=snapshot.correo,
            rolpro=snapshot.rolpro,
            orden=participant_payload.orden,
            obliga=participant_payload.obliga,
            estado="PENDIENTE",
            verlock=1,
            usrcre=actor_user,
        )
        db.add(participant)
        db.flush()
        create_evento_tx(db, "PAR_ASIG", "PARTICIPANTE", participant.parid, step.docid, actor_user)
        return participant

    def _participant_to_json(self, participant) -> dict:
        return {"usrid": participant.usrid, "orden": int(participant.orden), "obliga": bool(participant.obliga)}

    def _validate_participants(self, payload: PreparationDraftSave) -> None:
        participants = {participant.usrid: participant for participant in payload.participants}
        if len(participants) != len(payload.participants):
            raise HTTPException(status_code=409, detail="Participante duplicado")
        for position in payload.positions:
            if position.usrid not in participants:
                raise HTTPException(status_code=422, detail="Cada posicion debe tener un participante valido")
            if int(position.orden) != int(participants[position.usrid].orden):
                raise HTTPException(status_code=422, detail="El orden de la posicion no coincide con el participante")

    def _validate_positions(self, positions: Iterable[PreparationPosition], pages: list[PageInfo]) -> None:
        self._validate_position_dicts([self._position_to_json(position) for position in positions], pages)

    def _validate_position_dicts(self, positions: list[dict], pages: list[PageInfo]) -> None:
        if not positions:
            raise HTTPException(status_code=400, detail="Debe guardar al menos una posicion")
        page_map = {page.page: page for page in pages}
        seen_orders: set[tuple[str, int]] = set()
        for pos in positions:
            if pos["tipfir"] not in _ALLOWED_TYPES:
                raise HTTPException(status_code=422, detail="Tipo de firma no soportado")
            page = page_map.get(int(pos["pagina"]))
            if not page:
                raise HTTPException(status_code=422, detail="La posicion apunta a una pagina inexistente")
            x = Decimal(str(pos["posx"]))
            y = Decimal(str(pos["posy"]))
            width = Decimal(str(pos["ancho"]))
            height = Decimal(str(pos["alto"]))
            if x + width > Decimal(str(page.width)) or y + height > Decimal(str(page.height)):
                raise HTTPException(status_code=422, detail="La posicion excede los limites de la pagina")
            order_key = (pos["usrid"], int(pos["orden"]))
            if order_key in seen_orders:
                raise HTTPException(status_code=409, detail="Orden duplicado para el mismo firmante")
            seen_orders.add(order_key)

    def _next_participant_to_send(self, draft: dict) -> dict:
        sent_users = set(draft.get("sent_users", []))
        participants = sorted(draft.get("participants") or [], key=lambda item: int(item["orden"]))
        for participant in participants:
            if participant["usrid"] not in sent_users:
                return participant
        raise HTTPException(status_code=409, detail="Todos los firmantes del borrador ya fueron enviados")

    def _next_participant_after_current(self, draft: dict, current_usrid: str) -> dict | None:
        participants = sorted(draft.get("participants") or [], key=lambda item: int(item["orden"]))
        current_index = next((index for index, participant in enumerate(participants) if participant["usrid"] == current_usrid), None)
        if current_index is None:
            raise HTTPException(status_code=400, detail="El firmante actual no pertenece al borrador")
        sent_users = set(draft.get("sent_users", []))
        for participant in participants[current_index + 1 :]:
            if participant["usrid"] not in sent_users:
                return participant
        return None

    def _advance_sequential_signature(self, db: Session, firid: int, actor_user: str, ip: str) -> bool:
        firma, participant, step, doc = self._get_signature_context_for_update(db, firid)
        draft = self._get_draft(step)
        if not draft:
            raise HTTPException(status_code=400, detail="No hay borrador de secuencia para continuar")

        now = datetime.now(timezone.utc)
        completion_result = ResultContract(
            schema_ver=1,
            fase=ResultFase.FINALIZACION,
            remcod=200,
            remmsg="Firma completada localmente",
            recint=0,
            verchk=True,
            haschk=True,
            flags=[ResultFlag.HASH_MATCH],
        )
        if participant.estado in ("DISPONIBLE", "EN_PROCESO"):
            participant.estado = "COMPLETADO"
            participant.fecfin = participant.fecfin or now
            participant.usrmod = actor_user
            participant.fecmod = now
            participant.verlock += 1

        sent_users = set(draft.get("sent_users", []))
        sent_users.add(participant.usrid)
        draft["sent_users"] = sorted(sent_users)
        current_result = step.result or {}
        current_result[_DRAFT_KEY] = draft
        step.result = current_result
        flag_modified(step, "result")

        firma.estado = EstadoDocFirma.COMPLETADA.value
        firma.fecfin = firma.fecfin or now
        firma.verfin = firma.verfin or doc.verini
        firma.result = completion_result.model_dump(mode="json")
        firma.usrmod = actor_user
        firma.fecmod = now
        firma.revnum += 1
        create_evento_tx(
            db,
            evento="FIR_COMP",
            enttip="FIRMA",
            entid=firid,
            docid=doc.docid,
            usrid=actor_user,
            iporig=ip,
            detalle="Firma completada localmente; publicacion Alfresco bloqueada",
        )
        db.flush()

        next_participant_data = self._next_participant_after_current(draft, participant.usrid)
        if not next_participant_data:
            if step.estado != EstadoDocPaso.COMPLETADO.value:
                step.fecini = step.fecini or now
                step.estado = EstadoDocPaso.COMPLETADO.value
                step.fecfin = step.fecfin or now
                step.verlock += 1
                step.usrmod = actor_user
                step.fecmod = now
            doc.estado = EstadoDoc.PENDIENTE_PUBLICACION.value
            doc.verfin = None
            doc.hasfir = doc.hasfir or firma.hasfin
            doc.usrmod = actor_user
            doc.fecmod = now
            return False

        next_participant = self._get_participant_by_user_for_update(db, step.dpasid, next_participant_data["usrid"])
        if not next_participant:
            raise HTTPException(status_code=400, detail="El siguiente firmante no existe")
        if next_participant.estado not in ("PENDIENTE", "DISPONIBLE"):
            raise HTTPException(status_code=409, detail="El siguiente firmante no esta disponible")

        positions = [pos for pos in draft.get("positions", []) if pos["usrid"] == next_participant.usrid]
        if not positions:
            raise HTTPException(status_code=400, detail="El siguiente firmante no tiene posiciones configuradas")
        self._validate_position_dicts(positions, self._page_sizes_from_result(step))
        tipfir = positions[0]["tipfir"]
        if any(pos["tipfir"] != tipfir for pos in positions):
            raise HTTPException(status_code=422, detail="Un firmante no puede mezclar tipos de firma en un intento")

        docfirma = crud_docfirma.create_signature_attempt(
            db=db,
            docid=doc.docid,
            parid=next_participant.parid,
            tipfir=tipfir,
            verori=doc.verini,
            hasori=doc.hasori.lower(),
            secuen=crud_docfirma.get_next_document_sequence(db, doc.docid),
            intnum=crud_docfirma.get_next_participant_attempt(db, next_participant.parid),
            usrcre=actor_user,
        )
        crud_docfirma.create_signature_positions(db, docfirma.firid, positions)
        next_participant.estado = "DISPONIBLE"
        next_participant.fecdis = next_participant.fecdis or now
        next_participant.usrmod = actor_user
        next_participant.fecmod = now

        create_evento_tx(
            db,
            evento="FIR_INIC",
            enttip="FIRMA",
            entid=docfirma.firid,
            docid=doc.docid,
            usrid=actor_user,
            iporig=ip,
            detalle=f"Siguiente firmante activado: {next_participant.usrid}",
        )
        return True

    def _position_to_json(self, position: PreparationPosition) -> dict:
        return {
            "pagina": int(position.pagina),
            "posx": str(position.posx.quantize(Decimal("0.0001"))),
            "posy": str(position.posy.quantize(Decimal("0.0001"))),
            "ancho": str(position.ancho.quantize(Decimal("0.0001"))),
            "alto": str(position.alto.quantize(Decimal("0.0001"))),
            "rotaci": int(position.rotaci),
            "orden": int(position.orden),
            "tipfir": position.tipfir,
            "usrid": position.usrid,
        }

    def _get_doc(self, db: Session, docid: int) -> DocFir:
        doc = db.get(DocFir, docid)
        if not doc:
            raise HTTPException(status_code=404, detail="Proceso no encontrado")
        return doc

    def _get_doc_for_update(self, db: Session, docid: int) -> DocFir:
        doc = db.scalars(select(DocFir).where(DocFir.docid == docid).with_for_update()).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Proceso no encontrado")
        return doc

    def _assert_editable_document(self, doc: DocFir) -> None:
        if doc.estado not in (EstadoDoc.BORRADOR.value, EstadoDoc.PREPARADO.value):
            raise HTTPException(status_code=409, detail=f"No se puede editar un proceso en estado {doc.estado}")

    def _get_signing_step(self, db: Session, docid: int, required: bool = True) -> DocPaso | None:
        step = db.scalars(
            select(DocPaso)
            .where(DocPaso.docid == docid, DocPaso.pastip == "FIRMAR")
            .order_by(DocPaso.orden)
        ).first()
        if required and not step:
            raise HTTPException(status_code=404, detail="Paso de firma no encontrado")
        return step

    def _get_signing_step_for_update(self, db: Session, docid: int) -> DocPaso:
        step = db.scalars(
            select(DocPaso)
            .where(DocPaso.docid == docid, DocPaso.pastip == "FIRMAR")
            .order_by(DocPaso.orden)
            .with_for_update()
        ).first()
        if not step:
            raise HTTPException(status_code=404, detail="Paso de firma no encontrado")
        return step

    def _get_participant_by_user_for_update(self, db: Session, dpasid: int, usrid: str) -> DocPart | None:
        return db.scalars(
            select(DocPart)
            .where(DocPart.dpasid == dpasid, DocPart.usrid == usrid)
            .with_for_update()
        ).first()

    def _active_attempt_for_participant(self, db: Session, parid: int) -> DocFirma | None:
        return db.scalars(
            select(DocFirma)
            .where(
                DocFirma.parid == parid,
                DocFirma.estado.in_(
                    [
                        EstadoDocFirma.INICIADA.value,
                        EstadoDocFirma.GENERADA.value,
                        EstadoDocFirma.SUBIENDO.value,
                        EstadoDocFirma.CARGADA.value,
                        EstadoDocFirma.VERIFICANDO.value,
                    ]
                ),
            )
        ).first()

    def _get_draft(self, step: DocPaso) -> dict | None:
        if not step.result:
            return None
        return step.result.get(_DRAFT_KEY)

    def _store_page_sizes(self, step: DocPaso, pages: list[PageInfo]) -> None:
        current = step.result or {}
        current.setdefault(
            "page_sizes",
            [{"page": page.page, "width": page.width, "height": page.height, "rotation": page.rotation} for page in pages],
        )
        step.result = current
        flag_modified(step, "result")

    def _page_sizes_from_result(self, step: DocPaso) -> list[PageInfo]:
        raw_pages = (step.result or {}).get("page_sizes") or []
        if not raw_pages:
            raise HTTPException(status_code=400, detail="No hay dimensiones de paginas para validar posiciones")
        return [PageInfo(**page) for page in raw_pages]

    async def _ensure_page_sizes(self, db: Session, doc: DocFir, step: DocPaso) -> list[PageInfo]:
        raw_pages = (step.result or {}).get("page_sizes") or []
        if raw_pages:
            return [PageInfo(**page) for page in raw_pages]
        pages = await self._get_pdf_pages(UUID(doc.nodid))
        self._store_page_sizes(step, pages)
        db.flush()
        return pages

    def _get_signature_context(self, db: Session, firid: int) -> tuple[DocFirma, DocPart, DocPaso, DocFir]:
        row = db.execute(
            select(DocFirma, DocPart, DocPaso, DocFir)
            .join(DocPart, DocPart.parid == DocFirma.parid)
            .join(DocPaso, DocPaso.dpasid == DocPart.dpasid)
            .join(DocFir, DocFir.docid == DocFirma.docid)
            .where(DocFirma.firid == firid)
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Firma no encontrada")
        return row

    def _get_signature_context_for_update(self, db: Session, firid: int) -> tuple[DocFirma, DocPart, DocPaso, DocFir]:
        firma_read, participant_read, step_read, doc_read = self._get_signature_context(db, firid)
        step = db.scalars(select(DocPaso).where(DocPaso.dpasid == step_read.dpasid).with_for_update()).first()
        participant = db.scalars(select(DocPart).where(DocPart.parid == participant_read.parid).with_for_update()).first()
        doc = db.scalars(select(DocFir).where(DocFir.docid == doc_read.docid).with_for_update()).first()
        firma = db.scalars(select(DocFirma).where(DocFirma.firid == firma_read.firid).with_for_update()).first()
        return firma, participant, step, doc

    def _get_latest_completed_attempt_for_document(self, db: Session, docid: int) -> DocFirma | None:
        return db.scalars(
            select(DocFirma)
            .where(DocFirma.docid == docid, DocFirma.estado == EstadoDocFirma.COMPLETADA.value)
            .order_by(DocFirma.secuen.desc(), DocFirma.firid.desc())
        ).first()

    def _assert_signer(self, actor_user: str, participant: DocPart) -> None:
        if actor_user.strip().lower() != participant.usrid:
            raise HTTPException(status_code=403, detail="No puede actuar por otro firmante")

    def _is_publication_actor(self, actor_user: str, doc: DocFir) -> bool:
        return actor_user.strip().lower() == (doc.usrcre or "").strip().lower()

    def _assert_signature_viewer(self, actor_user: str, participant: DocPart, doc: DocFir, firma: DocFirma) -> None:
        user = actor_user.strip().lower()
        if user == participant.usrid:
            return
        if (
            self._is_publication_actor(user, doc)
            and doc.estado in (EstadoDoc.PENDIENTE_PUBLICACION.value, EstadoDoc.COMPLETADO.value)
            and firma.estado == EstadoDocFirma.COMPLETADA.value
        ):
            return
        raise HTTPException(status_code=403, detail="No puede consultar esta firma")

    def _validate_confirmation(self, firma: DocFirma, participant: DocPart, actor_user: str, expected_type: str) -> None:
        self._assert_signer(actor_user, participant)
        if firma.tipfir != expected_type:
            raise HTTPException(status_code=400, detail="El tipo de firma no corresponde a esta accion")
        if firma.estado != EstadoDocFirma.INICIADA.value:
            raise HTTPException(status_code=409, detail="La firma ya fue procesada o no esta activa")
        if participant.estado not in ("DISPONIBLE", "EN_PROCESO"):
            raise HTTPException(status_code=409, detail="El firmante no esta en una etapa activa")

    async def _download_source_for_signature(self, doc: DocFir) -> str:
        temp_path, _size, sha256 = await self.alfresco_client.download_node_content(UUID(doc.nodid))
        if sha256.lower() != doc.hasori.lower():
            self._cleanup_path(temp_path)
            raise HTTPException(status_code=409, detail="El hash vigente del PDF no coincide con el proceso")
        return temp_path

    async def _resolve_source_for_signature(self, db: Session, doc: DocFir) -> _SourceArtifact:
        previous = self._get_latest_completed_attempt_for_document(db, doc.docid)
        if previous:
            source_path = temporary_artifact_service.find_latest_path(prefix=f"fir-{previous.firid}-", suffix=".pdf")
            if not source_path or not source_path.exists():
                raise HTTPException(
                    status_code=410,
                    detail="El PDF acumulado requerido para continuar la cadena de firmas ya no esta disponible",
                )
            validation = pdf_validation_service.validate_source_pdf(source_path)
            if validation.sha256.lower() != previous.hasfin.lower():
                raise HTTPException(
                    status_code=409,
                    detail="El PDF acumulado no coincide con la ultima firma completada",
                )
            return _SourceArtifact(path=source_path, expected_sha256=previous.hasfin.lower(), cleanup_on_failure=False)

        source_path = await self._download_source_for_signature(doc)
        return _SourceArtifact(path=Path(source_path), expected_sha256=doc.hasori.lower(), cleanup_on_failure=True)

    def _write_signature_png(self, data_url: str) -> Path:
        try:
            encoded = data_url.split(",", 1)[1]
            raw = base64.b64decode(encoded, validate=True)
        except (IndexError, binascii.Error) as exc:
            raise HTTPException(status_code=422, detail="La firma manuscrita no es un PNG valido") from exc
        if len(raw) > settings.FIRMADOC_MAX_PNG_SIZE:
            raise HTTPException(status_code=413, detail="La firma manuscrita supera el tamano permitido")
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(status_code=422, detail="La firma manuscrita no es un PNG valido")
        return temporary_artifact_service.write_bytes(raw, prefix="firma-ui-", suffix=".png")

    def _build_result(self, db: Session, firid: int, message: str, actor_user: str | None = None) -> SignatureResult:
        firma, _participant, step, doc = self._get_signature_context(db, firid)
        participants = db.scalars(select(DocPart).where(DocPart.dpasid == step.dpasid)).all()
        completed_signatures = sum(1 for participant in participants if participant.estado == "COMPLETADO")
        if doc.estado == EstadoDoc.COMPLETADO.value:
            publication = "PUBLISHED"
        elif doc.estado == EstadoDoc.PENDIENTE_PUBLICACION.value:
            publication = "PENDING"
        else:
            publication = "DISABLED"
        final_hash = firma.hasfin
        return SignatureResult(
            firid=firma.firid,
            docid=doc.docid,
            status=firma.estado,
            document_name=doc.docnom,
            document_status=doc.estado,
            completed_signatures=completed_signatures,
            total_signatures=len(participants),
            original_version=doc.verini,
            final_version=firma.verfin,
            final_hash=final_hash,
            final_hash_short=f"{final_hash[:12]}...{final_hash[-8:]}" if final_hash else None,
            date=firma.fecmod or datetime.now(timezone.utc),
            alfresco_publication=publication,
            alfresco_write_enabled=settings.FIRMADOC_ALFRESCO_WRITE_ENABLED,
            can_publish_alfresco=(
                actor_user is not None
                and doc.estado == EstadoDoc.PENDIENTE_PUBLICACION.value
                and self._is_publication_actor(actor_user, doc)
            ),
            message=message,
        )

    def _cleanup_path(self, path: str | Path | None) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


frontend_signature_service = FrontendSignatureService()
