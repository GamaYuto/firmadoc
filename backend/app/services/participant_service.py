from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.docpaso import DocPaso
from app.models.docpart import DocPart
from app.crud.crud_docpaso import crud_docpaso
from app.crud.crud_docpart import crud_docpart, ParticipantConcurrencyError
from app.crud.crud_audifir import create_evento_tx
from app.schemas.docpart import DocPartCreate, DocPartTransition
from app.core.identity import IdentityResolver, IdentitySnapshot
from app.core.exceptions import StepConcurrencyError

class ParticipantService:
    
    def _record_audit(self, db: Session, evento: str, actor_id: str, docpart: DocPart, estado_ant: str, estado_nue: str, motivo: Optional[str] = None):
        detalle = f"Estado: {estado_ant} -> {estado_nue}"
        if motivo:
            detalle += f" | Motivo: {motivo}"
        create_evento_tx(
            db=db,
            evento=evento,
            enttip="PARTICIPANTE",
            entid=docpart.parid,
            docid=docpart.docpaso.docid if docpart.docpaso else None,
            usrid=actor_id,
            detalle=detalle
        )

    def _lock_participant_context(self, db: Session, parid: int, expected_verlock: Optional[int] = None) -> Tuple[DocPaso, DocPart]:
        dpasid = crud_docpart.get_step_id(db, parid)
        if not dpasid:
            raise ValueError("Participante no encontrado")
        
        step = crud_docpaso.get_for_update(db, dpasid)
        if not step:
            raise ValueError("Paso no encontrado")
            
        p = crud_docpart.get_for_update(db, parid)
        if not p or p.dpasid != dpasid:
            raise ValueError("Relación participante-paso inválida")
            
        if expected_verlock is not None and p.verlock != expected_verlock:
            raise ParticipantConcurrencyError()
            
        return step, p

    def _activate_parallel(self, db: Session, dpasid: int, actor_id: str, now: datetime):
        participants = crud_docpart.list_by_step_for_update(db, dpasid)
        for p in participants:
            if p.estado in ("PENDIENTE", "VENCIDO"):
                old_estado = p.estado
                crud_docpart.transition(
                    db=db,
                    parid=p.parid,
                    transition_data=DocPartTransition(
                        expected_verlock=p.verlock,
                        estado="DISPONIBLE",
                        fecdis=now,
                        clear_fecfin=True,
                        usrmod=actor_id
                    )
                )
                self._record_audit(db, "PAR_DISP" if old_estado == "PENDIENTE" else "PAR_REAC", actor_id, p, old_estado, "DISPONIBLE")

    def _activate_sequential(self, db: Session, dpasid: int, actor_id: str, now: datetime):
        participants = crud_docpart.list_by_step_for_update(db, dpasid)
        # Find first eligible
        eligible = [p for p in participants if p.estado in ("PENDIENTE", "VENCIDO")]
        if not eligible:
            return
        
        first = eligible[0]
        old_estado = first.estado
        crud_docpart.transition(
            db=db,
            parid=first.parid,
            transition_data=DocPartTransition(
                expected_verlock=first.verlock,
                estado="DISPONIBLE",
                fecdis=now,
                clear_fecfin=True,
                usrmod=actor_id
            )
        )
        self._record_audit(db, "PAR_DISP" if old_estado == "PENDIENTE" else "PAR_REAC", actor_id, first, old_estado, "DISPONIBLE")
        
        # Others pending
        for p in eligible[1:]:
            if p.estado == "VENCIDO":
                crud_docpart.transition(
                    db=db,
                    parid=p.parid,
                    transition_data=DocPartTransition(
                        expected_verlock=p.verlock,
                        estado="PENDIENTE",
                        clear_fecfin=True,
                        usrmod=actor_id
                    )
                )
                self._record_audit(db, "PAR_REAC", actor_id, p, "VENCIDO", "PENDIENTE")

    def _activate_next_sequential(self, db: Session, step: DocPaso, actor_id: str, now: datetime):
        modo = step.config.get("part_modo", "PARALELO")
        if modo != "SECUENCIAL":
            return
        self._activate_sequential(db, step.dpasid, actor_id, now)

    def _cancel_non_terminal(self, db: Session, dpasid: int, actor_id: str, now: datetime, motivo: str):
        participants = crud_docpart.list_by_step_for_update(db, dpasid)
        for p in participants:
            if p.estado not in ("COMPLETADO", "RECHAZADO", "OMITIDO", "CANCELADO", "VENCIDO"):
                old_estado = p.estado
                crud_docpart.transition(
                    db=db,
                    parid=p.parid,
                    transition_data=DocPartTransition(
                        expected_verlock=p.verlock,
                        estado="CANCELADO",
                        motivo=motivo,
                        fecfin=now,
                        usrmod=actor_id
                    )
                )
                self._record_audit(db, "PAR_CANC", actor_id, p, old_estado, "CANCELADO", motivo)

    def _get_config(self, step: DocPaso) -> dict:
        config = step.config or {}
        return {
            "part_estrategia": config.get("part_estrategia", "TODOS"),
            "part_modo": config.get("part_modo", "PARALELO"),
            "rechazo_inmediato": config.get("rechazo_inmediato", True)
        }

    def _evaluate_step_resolution(self, db: Session, step: DocPaso, actor_id: str, now: datetime):
        participants = crud_docpart.list_by_step_for_update(db, step.dpasid)
        config = self._get_config(step)
        estrategia = config["part_estrategia"]
        rechazo_inmediato = config["rechazo_inmediato"]
        
        req = [p for p in participants if p.obliga]
        if not req:
            return

        req_comp = [p for p in req if p.estado == "COMPLETADO"]
        req_omit = [p for p in req if p.estado == "OMITIDO"]
        req_rech = [p for p in req if p.estado == "RECHAZADO"]
        req_venc = [p for p in req if p.estado == "VENCIDO"]
        req_active = [p for p in req if p.estado not in ("COMPLETADO", "OMITIDO", "RECHAZADO", "CANCELADO", "VENCIDO")]

        step_res_estado = None
        
        if estrategia == "TODOS":
            if len(req_omit) == len(req):
                step_res_estado = "OMITIDO"
            elif req_rech:
                step_res_estado = "RECHAZADO"
            elif len(req_comp) > 0 and len(req_comp) + len(req_omit) == len(req):
                step_res_estado = "COMPLETADO"
                
        elif estrategia == "UNO":
            if req_comp:
                step_res_estado = "COMPLETADO"
            elif req_rech and rechazo_inmediato:
                step_res_estado = "RECHAZADO"
            elif not req_active:
                if req_rech:
                    step_res_estado = "RECHAZADO"
                elif len(req_omit) == len(req):
                    step_res_estado = "OMITIDO"
                elif req_venc:
                    step_res_estado = "VENCIDO"

        if step_res_estado:
            # Prevent double transition
            if step.estado not in ("COMPLETADO", "RECHAZADO", "OMITIDO", "CANCELADO", "VENCIDO"):
                crud_docpaso.transition(
                    db=db,
                    dpasid=step.dpasid,
                    expected_verlock=step.verlock,
                    estado_destino=step_res_estado,
                    motivo="Resolución automática de participantes",
                    fecini=step.fecini or now,
                    fecfin=now,
                    usrmod=actor_id
                )
                create_evento_tx(db, f"PASO_{step_res_estado[:4]}", "PASO", step.dpasid, step.docid, actor_id, detalle=f"Resolución {estrategia}")
                
                # Cancel remaining participants
                self._cancel_non_terminal(db, step.dpasid, actor_id, now, "Resolución de paso alcanzada")
                
                if step_res_estado in ("COMPLETADO", "OMITIDO"):
                    # Activate next step
                    next_step = crud_docpaso.activate_next_step(db, step.docid, step.orden, actor_id)
                    if next_step:
                        create_evento_tx(db, "PASO_DISP", "PASO", next_step.dpasid, next_step.docid, actor_id)
                        # Activate its participants
                        self._activate_step_participants(db, next_step, actor_id, now)

    def _activate_step_participants(self, db: Session, step: DocPaso, actor_id: str, now: datetime):
        config = self._get_config(step)
        if config["part_modo"] == "SECUENCIAL":
            self._activate_sequential(db, step.dpasid, actor_id, now)
        else:
            self._activate_parallel(db, step.dpasid, actor_id, now)

    def assign_participants(self, db: Session, dpasid: int, data_list: List[DocPartCreate], actor_id: str, resolver: IdentityResolver):
        now = datetime.now(timezone.utc)
        step = crud_docpaso.get_for_update(db, dpasid)
        if not step:
            raise ValueError("Paso no encontrado")
            
        if step.estado not in ("PENDIENTE", "DISPONIBLE"):
            raise ValueError(f"No se pueden asignar participantes en estado {step.estado}")
            
        # Validations
        if not any(d.obliga for d in data_list):
            raise ValueError("Debe haber al menos un participante obligatorio")
            
        usr_ids = set()
        ordens = set()
        resolved_data = []
        for d in data_list:
            if d.usrid in usr_ids:
                raise ValueError(f"Usuario {d.usrid} duplicado")
            if d.orden in ordens:
                raise ValueError(f"Orden {d.orden} duplicado")
            usr_ids.add(d.usrid)
            ordens.add(d.orden)
            
            snapshot = resolver.resolve_user(d.usrid)
            resolved_data.append({
                "snapshot": snapshot,
                "orden": d.orden,
                "obliga": d.obliga,
                "estado": "PENDIENTE"
            })
            
        # Check existing participants in case of DISPONIBLE
        existing = crud_docpart.list_by_step_for_update(db, dpasid)
        if step.estado == "DISPONIBLE":
            for e in existing:
                if e.fecini is not None or e.estado in ("EN_PROCESO", "COMPLETADO", "RECHAZADO", "OMITIDO", "CANCELADO", "VENCIDO"):
                    raise ValueError("No se puede asignar si ya hay participantes en proceso o terminales")
        
        # Create
        created = crud_docpart.create_many(db, dpasid, resolved_data, actor_id)
        for c in created:
            self._record_audit(db, "PAR_ASIG", actor_id, c, "N/A", "PENDIENTE")
            
        if step.estado == "DISPONIBLE":
            self._activate_step_participants(db, step, actor_id, now)

    def start_participation(self, db: Session, parid: int, actor_id: str, expected_verlock: int):
        now = datetime.now(timezone.utc)
        step, p = self._lock_participant_context(db, parid, expected_verlock)
        
        if p.usrid != actor_id:
            raise ValueError("Actor no autorizado para iniciar esta participación")
            
        if p.estado != "DISPONIBLE":
            raise ValueError(f"No se puede iniciar desde {p.estado}")
            
        crud_docpart.transition(db, parid, DocPartTransition(
            expected_verlock=expected_verlock,
            estado="EN_PROCESO",
            fecini=now if p.fecini is None else p.fecini,
            usrmod=actor_id
        ))
        self._record_audit(db, "PAR_INIC", actor_id, p, "DISPONIBLE", "EN_PROCESO")
        
        # Update step if needed
        if step.estado == "DISPONIBLE":
            crud_docpaso.transition(db, step.dpasid, step.verlock, "EN_PROCESO", actor_id, fecini=now)
            create_evento_tx(db, "PASO_INIC", "PASO", step.dpasid, step.docid, actor_id)

    def complete_participation(self, db: Session, parid: int, actor_id: str, expected_verlock: int, result: Optional[Dict[str, Any]] = None):
        now = datetime.now(timezone.utc)
        step, p = self._lock_participant_context(db, parid, expected_verlock)
            
        if p.usrid != actor_id:
            raise ValueError("Actor no autorizado para completar esta participación")
            
        if p.estado not in ("DISPONIBLE", "EN_PROCESO"):
            raise ValueError(f"No se puede completar desde {p.estado}")
            
        # Update step if needed
        if step.estado == "DISPONIBLE":
            crud_docpaso.transition(db, step.dpasid, step.verlock, "EN_PROCESO", actor_id, fecini=now)
            create_evento_tx(db, "PASO_INIC", "PASO", step.dpasid, step.docid, actor_id)

        old_estado = p.estado
        crud_docpart.transition(db, parid, DocPartTransition(
            expected_verlock=expected_verlock,
            estado="COMPLETADO",
            fecfin=now,
            fecini=p.fecini or now,
            usrmod=actor_id
        ))
        if result:
            crud_docpart.save_result(db, parid, expected_verlock + 1, result, actor_id)
            
        self._record_audit(db, "PAR_COMP", actor_id, p, old_estado, "COMPLETADO")
        
        self._evaluate_step_resolution(db, step, actor_id, now)
        self._activate_next_sequential(db, step, actor_id, now)

    def reject_participation(self, db: Session, parid: int, actor_id: str, expected_verlock: int, motivo: str):
        now = datetime.now(timezone.utc)
        step, p = self._lock_participant_context(db, parid, expected_verlock)
            
        if p.usrid != actor_id:
            raise ValueError("Actor no autorizado")
            
        if p.estado not in ("DISPONIBLE", "EN_PROCESO"):
            raise ValueError(f"No se puede rechazar desde {p.estado}")
            
        # Update step if needed
        if step.estado == "DISPONIBLE":
            crud_docpaso.transition(db, step.dpasid, step.verlock, "EN_PROCESO", actor_id, fecini=now)
            create_evento_tx(db, "PASO_INIC", "PASO", step.dpasid, step.docid, actor_id)

        old_estado = p.estado
        crud_docpart.transition(db, parid, DocPartTransition(
            expected_verlock=expected_verlock,
            estado="RECHAZADO",
            motivo=motivo,
            fecfin=now,
            usrmod=actor_id
        ))
        self._record_audit(db, "PAR_RECH", actor_id, p, old_estado, "RECHAZADO", motivo)
        
        self._evaluate_step_resolution(db, step, actor_id, now)

    def omit_participation(self, db: Session, parid: int, actor_id: str, expected_verlock: int, motivo: str):
        now = datetime.now(timezone.utc)
        step, p = self._lock_participant_context(db, parid, expected_verlock)
            
        if p.usrid == actor_id:
            raise ValueError("No se permite autoomisión")
            
        if p.estado not in ("PENDIENTE", "DISPONIBLE", "EN_PROCESO"):
            raise ValueError(f"No se puede omitir desde {p.estado}")
            
        # Update step if needed
        if step.estado == "DISPONIBLE":
            crud_docpaso.transition(db, step.dpasid, step.verlock, "EN_PROCESO", actor_id, fecini=now)
            create_evento_tx(db, "PASO_INIC", "PASO", step.dpasid, step.docid, actor_id)

        old_estado = p.estado
        crud_docpart.transition(db, parid, DocPartTransition(
            expected_verlock=expected_verlock,
            estado="OMITIDO",
            motivo=motivo,
            fecfin=now,
            usrmod=actor_id
        ))
        self._record_audit(db, "PAR_OMIT", actor_id, p, old_estado, "OMITIDO", motivo)
        
        self._evaluate_step_resolution(db, step, actor_id, now)
        self._activate_next_sequential(db, step, actor_id, now)

    def expire_step_participants(self, db: Session, dpasid: int, actor_id: str):
        now = datetime.now(timezone.utc)
        step = crud_docpaso.get_for_update(db, dpasid)
        if not step:
            return
            
        participants = crud_docpart.list_by_step_for_update(db, dpasid)
        for p in participants:
            if p.estado not in ("COMPLETADO", "RECHAZADO", "OMITIDO", "CANCELADO", "VENCIDO"):
                old_estado = p.estado
                crud_docpart.transition(db, p.parid, DocPartTransition(
                    expected_verlock=p.verlock,
                    estado="VENCIDO",
                    motivo="Vencimiento de paso",
                    fecfin=now,
                    usrmod=actor_id
                ))
                self._record_audit(db, "PAR_VENC", actor_id, p, old_estado, "VENCIDO")

    def reactivate_step_participants(self, db: Session, dpasid: int, actor_id: str):
        now = datetime.now(timezone.utc)
        step = crud_docpaso.get_for_update(db, dpasid)
        if not step:
            return
        self._activate_step_participants(db, step, actor_id, now)

participant_service = ParticipantService()
