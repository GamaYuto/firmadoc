from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, update
from datetime import datetime, timezone, timedelta
from app.crud.crud_docfir import get_by_id as get_docfir
from app.crud.crud_flupaso import crud_flupaso
from app.crud.crud_docpaso import crud_docpaso
from app.models.audifir import Audifir
from app.models.docpaso import DocPaso, EstadoDocPaso
from app.schemas.docpaso import DocPasoTransition, DocPasoReactivate
from app.core.exceptions import StepConcurrencyError
import logging

logger = logging.getLogger(__name__)

class StepService:
    def _audit(self, db: Session, evento: str, entid: int, usrid: str, docid: int, detalle: str = None):
        audit = Audifir(
            enttip="PASO",
            entid=entid,
            evento=evento,
            usrid=usrid,
            docid=docid,
            detalle=detalle
        )
        db.add(audit)

    def instantiate_document_steps(self, db: Session, docid: int, usrcre: str):
        try:
            docfir = get_docfir(db, docid)
            if not docfir:
                raise ValueError("Documento no encontrado")
            if not docfir.fluid:
                raise ValueError("El documento no tiene un flujo asignado")

            pasos_def = crud_flupaso.list_by_flow(db, docfir.fluid)
            pasos_activos = [p for p in pasos_def if p.activo]
            if not pasos_activos:
                raise ValueError("El flujo no tiene pasos activos")

            existentes = crud_docpaso.get_by_document(db, docid)
            if existentes:
                raise ValueError("El documento ya tiene pasos instanciados")

            now = datetime.now(timezone.utc)
            instanciados = []
            
            for idx, pdef in enumerate(pasos_activos):
                estado = EstadoDocPaso.DISPONIBLE if idx == 0 else EstadoDocPaso.PENDIENTE
                fecdis = now if idx == 0 else None
                feclim = (now + timedelta(minutes=pdef.plazo)) if idx == 0 and pdef.plazo else None
                
                dpaso = crud_docpaso.create_from_definition(
                    db=db,
                    docid=docid,
                    flupaso=pdef,
                    estado=estado,
                    usrcre=usrcre,
                    fecdis=fecdis,
                    feclim=feclim
                )
                db.flush() # Flush to get dpasid
                
                evento = "PAS_DISP" if idx == 0 else "PAS_INST"
                self._audit(db, evento, dpaso.dpasid, usrcre, docid, f"Paso {pdef.pascod} instanciado")
                instanciados.append(dpaso)

            db.commit()
            return instanciados
            
        except IntegrityError:
            db.rollback()
            raise ValueError("Inconsistencia al instanciar los pasos (duplicados)")
        except Exception as e:
            db.rollback()
            raise e

    def execute_transition(self, db: Session, dpasid: int, transition: DocPasoTransition, usrmod: str):
        paso = crud_docpaso.get_by_id(db, dpasid)
        if not paso:
            raise ValueError("Paso no encontrado")

        estado_origen = paso.estado
        estado_destino = transition.estado_destino
        
        # Validar grafo de transiciones
        transiciones_validas = {
            "PENDIENTE": ["DISPONIBLE", "CANCELADO"],
            "DISPONIBLE": ["EN_PROCESO", "OMITIDO", "VENCIDO", "CANCELADO"],
            "EN_PROCESO": ["COMPLETADO", "RECHAZADO", "VENCIDO", "CANCELADO"],
            "VENCIDO": ["DISPONIBLE"],
            "COMPLETADO": [],
            "RECHAZADO": [],
            "OMITIDO": [],
            "CANCELADO": []
        }
        
        if estado_destino not in transiciones_validas.get(estado_origen, []):
            raise ValueError(f"Transición no permitida de {estado_origen} a {estado_destino}")

        # Reglas obligatorias de motivo
        motivo = transition.motivo
        if estado_destino in ["RECHAZADO", "OMITIDO", "CANCELADO"] and not motivo:
            raise ValueError(f"Motivo es obligatorio para transición a {estado_destino}")

        now = datetime.now(timezone.utc)
        fecini = None
        fecfin = None

        if estado_destino == "EN_PROCESO":
            fecini = now
        elif estado_destino in ["COMPLETADO", "RECHAZADO", "OMITIDO", "CANCELADO", "VENCIDO"]:
            fecfin = now
            if not paso.fecini:
                fecini = now
                
        try:
            crud_docpaso.transition(
                db=db,
                dpasid=dpasid,
                expected_verlock=transition.verlock,
                estado_destino=estado_destino,
                usrmod=usrmod,
                motivo=motivo,
                fecini=fecini,
                fecfin=fecfin
            )
            
            # Mapeo de evento
            eventos = {
                "DISPONIBLE": "PAS_DISP",
                "EN_PROCESO": "PAS_INIC",
                "COMPLETADO": "PAS_COMP",
                "RECHAZADO": "PAS_RECH",
                "OMITIDO": "PAS_OMIT",
                "CANCELADO": "PAS_CANC",
                "VENCIDO": "PAS_VENC"
            }
            evento = eventos.get(estado_destino, "PAS_ACTU")
            self._audit(db, evento, dpasid, usrmod, paso.docid, motivo)
            
            # Activación en cadena
            if estado_destino in ["COMPLETADO", "OMITIDO"]:
                # Nota: Si es omitido, en V1 asumimos que autoriza a continuar
                siguiente = crud_docpaso.activate_next_step(db, paso.docid, paso.orden, usrmod)
                if siguiente:
                    self._audit(db, "PAS_DISP", siguiente.dpasid, usrmod, paso.docid, f"Activado tras completar paso {paso.orden}")
            
            db.commit()
            db.refresh(paso)
            return paso
            
        except StepConcurrencyError as e:
            db.rollback()
            raise e
        except Exception as e:
            db.rollback()
            raise e

    def reactivate_step(self, db: Session, dpasid: int, reactivate: DocPasoReactivate, usrmod: str):
        paso = crud_docpaso.get_by_id(db, dpasid)
        if not paso:
            raise ValueError("Paso no encontrado")
            
        if paso.estado != "VENCIDO":
            raise ValueError("Solo se pueden reactivar pasos VENCIDOS")
            
        try:
            now = datetime.now(timezone.utc)
            feclim = (now + timedelta(minutes=paso.plazo)) if paso.plazo else None
            
            crud_docpaso.transition(
                db=db,
                dpasid=dpasid,
                expected_verlock=reactivate.verlock,
                estado_destino="DISPONIBLE",
                usrmod=usrmod,
                motivo=reactivate.motivo,
                fecdis=now,
                feclim=feclim,
                clear_fecfin=True # Limpiar fecfin anterior si existía
            )
            self._audit(db, "PAS_REAC", dpasid, usrmod, paso.docid, reactivate.motivo)
            
            db.commit()
            db.refresh(paso)
            return paso
        except StepConcurrencyError as e:
            db.rollback()
            raise e
        except Exception as e:
            db.rollback()
            raise e

    def mark_expired_steps(self, db: Session):
        now = datetime.now(timezone.utc)
        vencidos = db.scalars(
            select(DocPaso)
            .where(
                DocPaso.estado.in_(["DISPONIBLE", "EN_PROCESO"]),
                DocPaso.feclim < now
            )
        ).all()
        
        count = 0
        for paso in vencidos:
            try:
                crud_docpaso.transition(
                    db=db,
                    dpasid=paso.dpasid,
                    expected_verlock=paso.verlock,
                    estado_destino="VENCIDO",
                    usrmod="sistema",
                    fecfin=now,
                    fecini=now if not paso.fecini else None
                )
                self._audit(db, "PAS_VENC", paso.dpasid, "sistema", paso.docid, "Vencimiento automático")
                count += 1
            except StepConcurrencyError:
                # Ignoramos si hubo concurrencia, se procesará después
                pass
        
        db.commit()
        return count

step_service = StepService()
