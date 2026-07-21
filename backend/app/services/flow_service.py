from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.crud.crud_flujodoc import crud_flujodoc
from app.crud.crud_flupaso import crud_flupaso
from app.models.audifir import Audifir
from app.schemas.flujo import FlujoCreate, FlujoUpdate, PasoCreate, PasoUpdate

class FlowService:
    def _audit(self, db: Session, evento: str, enttip: str, entid: int, usrcre: str, detalle: str = None):
        audit = Audifir(
            enttip=enttip,
            entid=entid,
            evento=evento,
            usrid=usrcre,
            detalle=detalle
        )
        db.add(audit)

    def create_flow(self, db: Session, obj_in: FlujoCreate):
        try:
            flow = crud_flujodoc.create(db=db, obj_in=obj_in)
            db.flush()
            self._audit(db, "FLU_CREA", "FLUJODOC", flow.fluid, obj_in.usrcre)
            db.commit()
            db.refresh(flow)
            return flow
        except Exception:
            db.rollback()
            raise

    def update_draft(self, db: Session, fluid: int, obj_in: FlujoUpdate):
        flow = crud_flujodoc.get_by_id(db, fluid)
        if not flow:
            raise ValueError("Flujo no encontrado")
        try:
            flow = crud_flujodoc.update_draft(db, db_obj=flow, obj_in=obj_in)
            db.flush()
            self._audit(db, "FLU_MODI", "FLUJODOC", flow.fluid, obj_in.usrmod)
            db.commit()
            db.refresh(flow)
            return flow
        except Exception as e:
            db.rollback()
            raise e

    def add_step(self, db: Session, fluid: int, obj_in: PasoCreate, usrcre: str):
        flow = crud_flujodoc.get_by_id(db, fluid)
        if not flow:
            raise ValueError("Flujo no encontrado")
        if flow.estado != "BORRADOR":
            raise ValueError("Solo se pueden agregar pasos a flujos en BORRADOR")
        try:
            paso = crud_flupaso.create(db, fluid, obj_in, usrcre)
            db.flush()
            self._audit(db, "PAS_CREA", "FLUPASO", paso.pasid, usrcre)
            db.commit()
            db.refresh(paso)
            return paso
        except IntegrityError:
            db.rollback()
            raise ValueError("El código o el orden del paso ya existen en este flujo")
        except Exception as e:
            db.rollback()
            raise e

    def activate_flow(self, db: Session, fluid: int, usrmod: str):
        flow = crud_flujodoc.get_by_id(db, fluid)
        if not flow:
            raise ValueError("Flujo no encontrado")
        if flow.estado != "BORRADOR":
            raise ValueError("Solo se pueden activar flujos en BORRADOR")
        
        # Verify if another active version exists
        active_flow = crud_flujodoc.get_active_by_code(db, flow.flucod)
        
        try:
            if active_flow:
                crud_flujodoc.deactivate(db, active_flow, usrmod)
                self._audit(db, "FLU_INAC", "FLUJODOC", active_flow.fluid, usrmod)
                db.flush()
            
            crud_flujodoc.activate(db, flow, usrmod)
            self._audit(db, "FLU_ACTI", "FLUJODOC", flow.fluid, usrmod)
            db.commit()
            db.refresh(flow)
            return flow
        except IntegrityError:
            db.rollback()
            raise ValueError("Inconsistencia en unicidad al activar el flujo")
        except Exception as e:
            db.rollback()
            raise e

    def deactivate_flow(self, db: Session, fluid: int, usrmod: str):
        flow = crud_flujodoc.get_by_id(db, fluid)
        if not flow:
            raise ValueError("Flujo no encontrado")
        if flow.estado != "ACTIVO":
            raise ValueError("Solo se pueden inactivar flujos ACTIVOS")
        
        try:
            crud_flujodoc.deactivate(db, flow, usrmod)
            self._audit(db, "FLU_INAC", "FLUJODOC", flow.fluid, usrmod)
            db.commit()
            db.refresh(flow)
            return flow
        except Exception as e:
            db.rollback()
            raise e

    def create_new_version(self, db: Session, fluid: int, usrcre: str):
        flow = crud_flujodoc.get_by_id(db, fluid)
        if not flow:
            raise ValueError("Flujo original no encontrado")
        
        try:
            new_flow = crud_flujodoc.create_new_version(db, flow, usrcre)
            db.flush()
            self._audit(db, "FLU_VERS", "FLUJODOC", new_flow.fluid, usrcre, f"Clonado de versión {flow.fluver}")
            
            pasos = crud_flupaso.list_by_flow(db, flow.fluid)
            new_pasos = crud_flupaso.clone_for_version(db, pasos, new_flow.fluid, usrcre)
            db.flush()
            for p in new_pasos:
                self._audit(db, "PAS_CREA", "FLUPASO", p.pasid, usrcre, "Paso clonado")
                
            db.commit()
            db.refresh(new_flow)
            return new_flow
        except IntegrityError:
            db.rollback()
            raise ValueError("Error de unicidad al crear nueva versión")
        except Exception as e:
            db.rollback()
            raise e

flow_service = FlowService()
