from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path
import logging
import re
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.services.temporary_artifact_service import TemporaryArtifactService, temporary_artifact_service

logger = logging.getLogger(__name__)

from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.firpos import Firpos
from app.models.docfir import DocFir, EstadoDoc
from app.models.docpaso import DocPaso, EstadoDocPaso
from app.models.docpart import DocPart
from app.crud.crud_docfirma import crud_docfirma
from app.crud.crud_docpart import crud_docpart
from app.crud.crud_docpaso import crud_docpaso
from app.crud.crud_audifir import create_evento_tx
from app.core.identity import IdentitySnapshot
from app.schemas.docfirma import ResultContract, ResultFase, ResultFlag
from app.services.signature_exceptions import (
    SignatureNotFoundError,
    SignatureNotAllowedError,
    SignatureConcurrencyError,
    SignatureAlreadyCompletedError,
    SignatureActiveAttemptError,
    SignaturePayloadError,
    SignatureStateError,
    SignatureIntegrityError,
    SignatureVersionConflictError,
    SignatureRecoveryRequiredError
)

class SignatureService:

    def __init__(self, artifact_service: Optional[TemporaryArtifactService] = None) -> None:
        self.artifact_service = artifact_service or temporary_artifact_service

    def reserve_signature_attempt(
        self,
        db: Session,
        parid: int,
        tipfir: str,
        positions: List[Dict[str, Any]],
        verori: str,
        hasori: str,
        actor: IdentitySnapshot,
        expected_participant_verlock: int
    ) -> DocFirma:
        # Validate hashes
        if not re.match(r'^[0-9a-fA-F]{64}$', hasori):
            raise SignaturePayloadError("Formato hasori inválido (debe ser SHA-256 hex de 64 caracteres)")

        # 1. Resolve relation (non-blocking)
        part = db.scalars(select(DocPart).where(DocPart.parid == parid)).first()
        if not part:
            raise SignatureNotFoundError("Participante no encontrado")

        step = part.docpaso
        if not step:
            raise SignatureNotFoundError("Paso no encontrado")

        docid = step.docid
        doc = db.scalars(select(DocFir).where(DocFir.docid == docid)).first()
        if not doc:
            raise SignatureNotFoundError("Documento no encontrado")

        # 2-4. Locking Aggregate (Order: docpaso -> docpart -> docfir -> docfirma)
        step_locked = db.scalars(
            select(DocPaso)
            .where(DocPaso.dpasid == step.dpasid)
            .with_for_update()
        ).first()

        part_locked = db.scalars(
            select(DocPart)
            .where(DocPart.parid == parid)
            .with_for_update()
        ).first()

        doc_locked = db.scalars(
            select(DocFir)
            .where(DocFir.docid == docid)
            .with_for_update()
        ).first()

        # 5. Lock active attempt if any
        active_attempt = crud_docfirma.get_active_attempt_by_document(db, docid)
        if active_attempt:
            active_attempt_locked = db.scalars(
                select(DocFirma)
                .where(DocFirma.firid == active_attempt.firid)
                .with_for_update()
            ).first()
            raise SignatureActiveAttemptError("Existe un intento de firma activo para este documento")

        # Validate Actor
        if part_locked.usrid != actor.usrid:
            # Audit Denied before reserve
            create_evento_tx(
                db=db,
                evento="PAR_FDEN",
                enttip="PARTICIPANTE",
                entid=parid,
                docid=docid,
                usrid=actor.usrid,
                detalle="Intento de firma no autorizado: actor no coincide con participante"
            )
            db.flush()
            raise SignatureNotAllowedError("Actor no coincide con el participante asignado")

        # Validate Step
        if step_locked.pastip != 'FIRMAR':
            # Audit Denied
            create_evento_tx(
                db=db,
                evento="PAS_FDEN",
                enttip="PASO",
                entid=step_locked.dpasid,
                docid=docid,
                usrid=actor.usrid,
                detalle="Paso no es de tipo FIRMAR"
            )
            db.flush()
            raise SignatureNotAllowedError("El paso actual no requiere firma")

        # Validate Participant status
        if part_locked.estado not in ('DISPONIBLE', 'EN_PROCESO'):
            raise SignatureNotAllowedError(f"Participante no disponible para firmar (estado: {part_locked.estado})")

        # Validate expected_participant_verlock
        if part_locked.verlock != expected_participant_verlock:
            raise SignatureConcurrencyError("Conflicto de concurrencia: verlock del participante desactualizado")

        # Validate no completed signature exists
        completed_attempt = crud_docfirma.get_completed_attempt_by_participant(db, parid)
        if completed_attempt or part_locked.estado == 'COMPLETADO':
            raise SignatureAlreadyCompletedError("El participante ya completó la firma")

        # Increment participant verlock and transition state to EN_PROCESO if not already
        if part_locked.estado == 'DISPONIBLE':
            part_locked.estado = 'EN_PROCESO'
            part_locked.fecini = datetime.now(timezone.utc)
        part_locked.verlock += 1
        part_locked.fecmod = datetime.now(timezone.utc)
        part_locked.usrmod = actor.usrid

        # Calculate sequences
        secuen = crud_docfirma.get_next_document_sequence(db, docid)
        intnum = crud_docfirma.get_next_participant_attempt(db, parid)

        # Create docfirma INICIADA
        firma = crud_docfirma.create_signature_attempt(
            db=db,
            docid=docid,
            parid=parid,
            tipfir=tipfir,
            verori=verori,
            hasori=hasori,
            secuen=secuen,
            intnum=intnum,
            usrcre=actor.usrid
        )

        # Create firpos
        crud_docfirma.create_signature_positions(db, firma.firid, positions)

        # Audit event
        create_evento_tx(
            db=db,
            evento="FIR_INIC",
            enttip="FIRMA",
            entid=firma.firid,
            docid=docid,
            usrid=actor.usrid,
            detalle=f"Reserva de intento de firma iniciada. Secuencia: {secuen}, Intento: {intnum}"
        )

        return firma

    def mark_generated(self, db: Session, firid: int, expected_revnum: int, hasfin: str, usrmod: str) -> int:
        if not re.match(r'^[0-9a-fA-F]{64}$', hasfin):
            raise SignaturePayloadError("Formato hasfin inválido (debe ser SHA-256 hex de 64 caracteres)")

        firma = crud_docfirma.get_signature_attempt_for_update(db, firid)
        if not firma:
            raise SignatureNotFoundError()

        if firma.estado != EstadoDocFirma.INICIADA:
            raise SignatureStateError(f"Transición a GENERADA no permitida desde {firma.estado}")

        new_rev = crud_docfirma.update_attempt_state(
            db=db,
            firid=firid,
            expected_revnum=expected_revnum,
            new_state=EstadoDocFirma.GENERADA,
            values={"hasfin": hasfin},
            usrmod=usrmod
        )

        create_evento_tx(
            db=db,
            evento="FIR_GENE",
            enttip="FIRMA",
            entid=firid,
            docid=firma.docid,
            usrid=usrmod,
            detalle=f"Documento firmado generado localmente. Hash: {hasfin}"
        )
        return new_rev

    def mark_upload_started(self, db: Session, firid: int, expected_revnum: int, usrmod: str) -> int:
        firma = crud_docfirma.get_signature_attempt_for_update(db, firid)
        if not firma:
            raise SignatureNotFoundError()

        if firma.estado not in (EstadoDocFirma.GENERADA, EstadoDocFirma.COMPLETADA):
            raise SignatureStateError(f"Transición a SUBIENDO no permitida desde {firma.estado}")

        new_rev = crud_docfirma.update_attempt_state(
            db=db,
            firid=firid,
            expected_revnum=expected_revnum,
            new_state=EstadoDocFirma.SUBIENDO,
            values={"fecfin": None, "errcod": None, "motivo": None},
            usrmod=usrmod
        )

        create_evento_tx(
            db=db,
            evento="FIR_SUBE",
            enttip="FIRMA",
            entid=firid,
            docid=firma.docid,
            usrid=usrmod,
            detalle="Inicio de carga a Alfresco registrado"
        )
        return new_rev

    def mark_uploaded(self, db: Session, firid: int, expected_revnum: int, verfin: str, result_data: ResultContract, usrmod: str) -> int:
        firma = crud_docfirma.get_signature_attempt_for_update(db, firid)
        if not firma:
            raise SignatureNotFoundError()

        if firma.estado != EstadoDocFirma.SUBIENDO:
            raise SignatureStateError(f"Transición a CARGADA no permitida desde {firma.estado}")

        # Validate Result schema
        if result_data.fase != ResultFase.PUBLICACION:
            raise SignaturePayloadError("La fase en mark_uploaded debe ser PUBLICACION")

        new_rev = crud_docfirma.update_attempt_state(
            db=db,
            firid=firid,
            expected_revnum=expected_revnum,
            new_state=EstadoDocFirma.CARGADA,
            values={"verfin": verfin, "result": result_data.model_dump(mode="json")},
            usrmod=usrmod
        )

        create_evento_tx(
            db=db,
            evento="FIR_CARG",
            enttip="FIRMA",
            entid=firid,
            docid=firma.docid,
            usrid=usrmod,
            detalle=f"Documento cargado en Alfresco. Versión: {verfin}"
        )
        return new_rev

    def mark_verification_started(self, db: Session, firid: int, expected_revnum: int, usrmod: str) -> int:
        firma = crud_docfirma.get_signature_attempt_for_update(db, firid)
        if not firma:
            raise SignatureNotFoundError()

        if firma.estado != EstadoDocFirma.CARGADA:
            raise SignatureStateError(f"Transición a VERIFICANDO no permitida desde {firma.estado}")

        new_rev = crud_docfirma.update_attempt_state(
            db=db,
            firid=firid,
            expected_revnum=expected_revnum,
            new_state=EstadoDocFirma.VERIFICANDO,
            usrmod=usrmod
        )

        create_evento_tx(
            db=db,
            evento="FIR_VERI",
            enttip="FIRMA",
            entid=firid,
            docid=firma.docid,
            usrid=usrmod,
            detalle="Verificación de hash remoto iniciada"
        )
        return new_rev

    def persist_verification_evidence(self, db: Session, firid: int, expected_revnum: int, remcod: int, remmsg: str, flags: List[ResultFlag], usrmod: str) -> int:
        firma = crud_docfirma.get_signature_attempt_for_update(db, firid)
        if not firma:
            raise SignatureNotFoundError()

        if firma.estado != EstadoDocFirma.VERIFICANDO:
            raise SignatureStateError(f"Solo se puede persistir evidencia en estado VERIFICANDO. Estado actual: {firma.estado}")

        if ResultFlag.HASH_MATCH not in flags:
            raise SignaturePayloadError("La evidencia debe contener el flag HASH_MATCH")

        result_data = ResultContract(
            schema_ver=1,
            fase=ResultFase.VERIFICACION,
            remcod=remcod,
            remmsg=remmsg,
            recint=0,
            verchk=True,
            haschk=True,
            flags=flags
        )

        new_rev = crud_docfirma.update_attempt_state(
            db=db,
            firid=firid,
            expected_revnum=expected_revnum,
            new_state=EstadoDocFirma.VERIFICANDO, # remains VERIFICANDO until finalize completes it
            values={"result": result_data.model_dump(mode="json")},
            usrmod=usrmod
        )
        return new_rev

    def finalize_verified_signature(
        self,
        db: Session,
        firid: int,
        expected_revnum: int,
        expected_participant_verlock: int,
        actor: IdentitySnapshot
    ) -> None:
        # 1. Resolve relations (non-blocking)
        firma = db.scalars(select(DocFirma).where(DocFirma.firid == firid)).first()
        if not firma:
            raise SignatureNotFoundError("Intento de firma no encontrado")

        part = db.scalars(select(DocPart).where(DocPart.parid == firma.parid)).first()
        if not part:
            raise SignatureNotFoundError("Participante no encontrado")

        step = part.docpaso
        if not step:
            raise SignatureNotFoundError("Paso no encontrado")

        doc = db.scalars(select(DocFir).where(DocFir.docid == firma.docid)).first()
        if not doc:
            raise SignatureNotFoundError("Documento no encontrado")

        # 2-4. Acquire locks in strict order
        step_locked = db.scalars(select(DocPaso).where(DocPaso.dpasid == step.dpasid).with_for_update()).first()
        part_locked = db.scalars(select(DocPart).where(DocPart.parid == part.parid).with_for_update()).first()
        doc_locked = db.scalars(select(DocFir).where(DocFir.docid == doc.docid).with_for_update()).first()
        firma_locked = db.scalars(select(DocFirma).where(DocFirma.firid == firid).with_for_update()).first()

        # 5. Rama Idempotente
        if firma_locked.estado == EstadoDocFirma.COMPLETADA:
            # Check consistency only against database locked row
            is_consistent = (
                firma_locked.verfin is not None
                and firma_locked.hasfin is not None
                and firma_locked.result is not None
                and firma_locked.result.get("schema_ver") == 1
                and firma_locked.result.get("fase") in ("VERIFICACION", "FINALIZACION")
                and firma_locked.result.get("verchk") is True
                and firma_locked.result.get("haschk") is True
                and "HASH_MATCH" in firma_locked.result.get("flags", [])
                and part_locked.estado == "COMPLETADO"
            )
            if is_consistent:
                # Early return idempotente: no validation of expected_revnum, no modifying db
                return
            else:
                # Register system alert
                create_evento_tx(
                    db=db,
                    evento="SIS_IDER",
                    enttip="SISTEMA",
                    entid=None,
                    docid=firma.docid,
                    usrid=actor.usrid,
                    detalle=f"Alerta de integridad: firma {firid} en estado COMPLETADA es inconsistente con evidencia."
                )
                raise SignatureIntegrityError("Consistencia de firma completada inválida en base de datos")

        # Normal Flow: Validate expected_revnum and expected_participant_verlock
        if firma_locked.revnum != expected_revnum:
            raise SignatureConcurrencyError("Conflicto de concurrencia: revnum del intento desactualizado")
        if part_locked.verlock != expected_participant_verlock:
            raise SignatureConcurrencyError("Conflicto de concurrencia: verlock del participante desactualizado")

        if firma_locked.estado != EstadoDocFirma.VERIFICANDO:
            raise SignatureStateError(f"La firma debe estar en estado VERIFICANDO para finalizar (estado actual: {firma_locked.estado})")

        # Validate result evidence in locked row
        if not firma_locked.result:
            raise SignaturePayloadError("Falta la evidencia de verificación persistida en base de datos")

        try:
            res_contract = ResultContract(**firma_locked.result)
        except Exception as e:
            raise SignaturePayloadError(f"Contrato result persistido inválido: {str(e)}")

        if not res_contract.verchk or not res_contract.haschk or ResultFlag.HASH_MATCH not in res_contract.flags:
            raise SignaturePayloadError("La evidencia persistida no contiene la confirmación HASH_MATCH")

        if not firma_locked.verfin or not firma_locked.hasfin:
            raise SignaturePayloadError("Versión final o hash final ausente en el intento")

        # Update docfirma to COMPLETADA
        now = datetime.now(timezone.utc)
        res_contract.fase = ResultFase.FINALIZACION
        firma_locked.estado = EstadoDocFirma.COMPLETADA
        firma_locked.fecfin = now
        firma_locked.result = res_contract.model_dump(mode="json")
        firma_locked.revnum += 1
        firma_locked.fecmod = now
        firma_locked.usrmod = actor.usrid

        # Update participant state to COMPLETADO
        if part_locked.estado != 'COMPLETADO':
            part_locked.estado = 'COMPLETADO'
            part_locked.fecfin = now
            part_locked.verlock += 1
            part_locked.fecmod = now
            part_locked.usrmod = actor.usrid

        # Evaluate step resolution using existing participant_service logic if available,
        # or inline logic to avoid breaking locks hierarchy.
        # First, resolve locks on remaining participants ordered by parid
        other_parts = db.scalars(
            select(DocPart)
            .where(DocPart.dpasid == step.dpasid, DocPart.parid != part.parid)
            .order_by(DocPart.parid)
            .with_for_update()
        ).all()

        # Strategy evaluation
        from app.services.participant_service import ParticipantService
        p_service = ParticipantService()
        config = p_service._get_config(step_locked)
        estrategia = config["part_estrategia"]

        all_parts = [part_locked] + list(other_parts)
        req = [p for p in all_parts if p.obliga]
        req_comp = [p for p in req if p.estado == "COMPLETADO"]
        req_omit = [p for p in req if p.estado == "OMITIDO"]

        step_resolved_state = None
        if estrategia == "TODOS":
            if len(req_comp) + len(req_omit) == len(req):
                step_resolved_state = "COMPLETADO"
        elif estrategia == "UNO":
            if len(req_comp) > 0:
                step_resolved_state = "COMPLETADO"

        if step_resolved_state:
            step_locked.estado = step_resolved_state
            step_locked.fecfin = now
            if not step_locked.fecini:
                step_locked.fecini = now
            step_locked.verlock += 1
            step_locked.fecmod = now
            step_locked.usrmod = actor.usrid
            create_evento_tx(db, f"PASO_{step_resolved_state[:4]}", "PASO", step_locked.dpasid, step_locked.docid, actor.usrid)

            # Cancel remaining participants in step
            for p in all_parts:
                if p.estado not in ("COMPLETADO", "RECHAZADO", "OMITIDO", "CANCELADO", "VENCIDO"):
                    p.estado = "CANCELADO"
                    p.motivo = "Resolucion de paso alcanzada"
                    p.fecfin = now
                    p.verlock += 1
                    p.fecmod = now
                    p.usrmod = actor.usrid
                    create_evento_tx(db, "PAR_CANC", "PARTICIPANTE", p.parid, step_locked.docid, actor.usrid, detalle="Resolución de paso alcanzada")

            # Activate next step if resolved is COMPLETADO or OMITIDO
            if step_resolved_state in ("COMPLETADO", "OMITIDO"):
                next_step = db.scalars(
                    select(DocPaso)
                    .where(DocPaso.docid == step.docid, DocPaso.orden == step_locked.orden + 1)
                    .with_for_update()
                ).first()
                if next_step:
                    next_step.estado = EstadoDocPaso.DISPONIBLE.value
                    next_step.fecdis = now
                    if next_step.plazo:
                        from datetime import timedelta
                        next_step.feclim = now + timedelta(minutes=next_step.plazo)
                    next_step.verlock += 1
                    next_step.fecmod = now
                    next_step.usrmod = actor.usrid
                    create_evento_tx(db, "PASO_DISP", "PASO", next_step.dpasid, next_step.docid, actor.usrid)
                    # Activate next step participants
                    p_service._activate_step_participants(db, next_step, actor.usrid, now)

        # Update docfir state
        # Evaluate new state of the document process
        all_steps = db.scalars(select(DocPaso).where(DocPaso.docid == doc_locked.docid)).all()
        steps_active = [s for s in all_steps if s.estado not in ("COMPLETADO", "RECHAZADO", "CANCELADO", "OMITIDO")]

        has_pending_signatures = False
        for s in steps_active:
            if s.pastip == 'FIRMAR':
                has_pending_signatures = True
                break

        if has_pending_signatures:
            doc_locked.estado = EstadoDoc.FIRMADO_PARCIAL.value
        elif len(steps_active) > 0:
            doc_locked.estado = EstadoDoc.EN_CURSO.value
        else:
            # All steps finished
            if any(s.estado == "RECHAZADO" for s in all_steps):
                doc_locked.estado = EstadoDoc.RECHAZADO.value
            elif any(s.estado == "CANCELADO" for s in all_steps):
                doc_locked.estado = EstadoDoc.CANCELADO.value
            else:
                doc_locked.estado = EstadoDoc.COMPLETADO.value
                doc_locked.verfin = firma_locked.verfin
                doc_locked.hasfir = firma_locked.hasfin

        doc_locked.fecmod = now
        doc_locked.usrmod = actor.usrid

        # Record signature audit
        create_evento_tx(
            db=db,
            evento="FIR_COMP",
            enttip="FIRMA",
            entid=firid,
            docid=doc_locked.docid,
            usrid=actor.usrid,
            detalle=f"Firma completada y verificada. Versión: {firma_locked.verfin}"
        )

    def cancel_signature_attempt(self, db: Session, firid: int, expected_revnum: int, motivo: str, actor: IdentitySnapshot) -> int:
        if not motivo or not motivo.strip():
            raise SignaturePayloadError("El motivo de cancelación es obligatorio y no puede estar vacío")

        firma = crud_docfirma.get_signature_attempt_for_update(db, firid)
        if not firma:
            raise SignatureNotFoundError()

        if firma.estado not in (EstadoDocFirma.INICIADA, EstadoDocFirma.GENERADA):
            raise SignatureStateError(f"No se puede cancelar el intento en estado {firma.estado}")

        # Locking context for docpart since this will cancel the active attempt
        part_locked = db.scalars(select(DocPart).where(DocPart.parid == firma.parid).with_for_update()).first()

        now = datetime.now(timezone.utc)
        # Cancel participant as well if in EN_PROCESO
        if part_locked.estado == 'EN_PROCESO':
            part_locked.estado = 'DISPONIBLE'
            part_locked.verlock += 1
            part_locked.fecmod = now
            part_locked.usrmod = actor.usrid

        new_rev = crud_docfirma.update_attempt_state(
            db=db,
            firid=firid,
            expected_revnum=expected_revnum,
            new_state=EstadoDocFirma.CANCELADA,
            values={"motivo": motivo, "fecfin": now},
            usrmod=actor.usrid
        )

        create_evento_tx(
            db=db,
            evento="FIR_CANC",
            enttip="FIRMA",
            entid=firid,
            docid=firma.docid,
            usrid=actor.usrid,
            detalle=f"Intento de firma cancelado. Motivo: {motivo}"
        )

        return new_rev

    def cleanup_attempt_artifacts(self, firid: int) -> list[Path]:
        """Elimina físicamente los artefactos temporales de un intento tras confirmarse la transacción."""
        cleaned: list[Path] = []
        for p in self.artifact_service.find_matching_paths(prefix=f"fir-{firid}-"):
            self.artifact_service.cleanup_path(p)
            cleaned.append(p)
        return cleaned

    def mark_signature_conflict(
        self,
        db: Session,
        firid: int,
        expected_revnum: int,
        errcod: str,
        usrmod: str,
        result_data: ResultContract | None = None,
    ) -> int:
        if not errcod:
            raise SignaturePayloadError("El código de error es obligatorio para conflictos")

        firma = crud_docfirma.get_signature_attempt_for_update(db, firid)
        if not firma:
            raise SignatureNotFoundError()

        now = datetime.now(timezone.utc)
        values = {"errcod": errcod, "fecfin": now}
        if result_data is not None:
            values["result"] = result_data.model_dump(mode="json")

        new_rev = crud_docfirma.update_attempt_state(
            db=db,
            firid=firid,
            expected_revnum=expected_revnum,
            new_state=EstadoDocFirma.CONFLICTO,
            values=values,
            usrmod=usrmod
        )

        create_evento_tx(
            db=db,
            evento="FIR_CONF",
            enttip="FIRMA",
            entid=firid,
            docid=firma.docid,
            usrid=usrmod,
            detalle=f"Conflicto de firma detectado. Código de error: {errcod}"
        )
        return new_rev

    def mark_signature_failed(self, db: Session, firid: int, expected_revnum: int, errcod: str, usrmod: str) -> int:
        if not errcod:
            raise SignaturePayloadError("El código de error es obligatorio para intentos fallidos")

        firma = crud_docfirma.get_signature_attempt_for_update(db, firid)
        if not firma:
            raise SignatureNotFoundError()

        now = datetime.now(timezone.utc)
        new_rev = crud_docfirma.update_attempt_state(
            db=db,
            firid=firid,
            expected_revnum=expected_revnum,
            new_state=EstadoDocFirma.FALLIDA,
            values={"errcod": errcod, "fecfin": now},
            usrmod=usrmod
        )

        create_evento_tx(
            db=db,
            evento="FIR_FALL",
            enttip="FIRMA",
            entid=firid,
            docid=firma.docid,
            usrid=usrmod,
            detalle=f"Intento de firma fallido. Código de error: {errcod}"
        )
        return new_rev

signature_service = SignatureService()
