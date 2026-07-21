from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from datetime import datetime, timezone
from app.models.docpaso import DocPaso
from app.models.flupaso import Flupaso
from app.core.exceptions import StepConcurrencyError

class CRUDDocPaso:
    def get_by_id(self, db: Session, dpasid: int) -> Optional[DocPaso]:
        return db.scalars(select(DocPaso).where(DocPaso.dpasid == dpasid)).first()

    def get_by_document(self, db: Session, docid: int) -> List[DocPaso]:
        return list(db.scalars(select(DocPaso).where(DocPaso.docid == docid).order_by(DocPaso.orden)).all())

    def create_from_definition(self, db: Session, docid: int, flupaso: Flupaso, estado: str, usrcre: str, fecdis: Optional[datetime] = None, feclim: Optional[datetime] = None) -> DocPaso:
        db_obj = DocPaso(
            docid=docid,
            pasid=flupaso.pasid,
            orden=flupaso.orden,
            estado=estado,
            fecdis=fecdis,
            feclim=feclim,
            pastip=flupaso.pastip,
            rolreq=flupaso.rolreq,
            obliga=flupaso.obliga,
            plazo=flupaso.plazo,
            config=flupaso.config,
            usrcre=usrcre,
            verlock=1,
            activo=True
        )
        db.add(db_obj)
        return db_obj

    def transition(self, db: Session, dpasid: int, expected_verlock: int, estado_destino: str, usrmod: str, 
                   motivo: Optional[str] = None, fecini: Optional[datetime] = None, fecfin: Optional[datetime] = None,
                   fecdis: Optional[datetime] = None, feclim: Optional[datetime] = None,
                   clear_fecfin: bool = False) -> None:
        
        values = {
            "estado": estado_destino,
            "verlock": expected_verlock + 1,
            "usrmod": usrmod,
            "fecmod": datetime.now(timezone.utc)
        }
        if motivo is not None:
            values["motivo"] = motivo
        if fecini is not None:
            values["fecini"] = fecini
        if clear_fecfin:
            values["fecfin"] = None
        elif fecfin is not None:
            values["fecfin"] = fecfin
        if fecdis is not None:
            values["fecdis"] = fecdis
        if feclim is not None:
            values["feclim"] = feclim
            
        stmt = (
            update(DocPaso)
            .where(DocPaso.dpasid == dpasid, DocPaso.verlock == expected_verlock)
            .values(**values)
        )
        result = db.execute(stmt)
        if result.rowcount == 0:
            raise StepConcurrencyError()

    def save_result(self, db: Session, dpasid: int, expected_verlock: int, result_data: Dict[str, Any], usrmod: str) -> None:
        stmt = (
            update(DocPaso)
            .where(DocPaso.dpasid == dpasid, DocPaso.verlock == expected_verlock)
            .values(
                result=result_data,
                verlock=expected_verlock + 1,
                usrmod=usrmod,
                fecmod=datetime.now(timezone.utc)
            )
        )
        result = db.execute(stmt)
        if result.rowcount == 0:
            raise StepConcurrencyError()

    def activate_next_step(self, db: Session, docid: int, current_orden: int, usrmod: str) -> Optional[DocPaso]:
        next_step = db.scalars(
            select(DocPaso)
            .where(DocPaso.docid == docid, DocPaso.orden > current_orden)
            .order_by(DocPaso.orden)
        ).first()

        if next_step and next_step.estado == "PENDIENTE":
            from datetime import timedelta
            now = datetime.now(timezone.utc)
            feclim = now + timedelta(minutes=next_step.plazo) if next_step.plazo else None
            
            stmt = (
                update(DocPaso)
                .where(DocPaso.dpasid == next_step.dpasid, DocPaso.verlock == next_step.verlock)
                .values(
                    estado="DISPONIBLE",
                    fecdis=now,
                    feclim=feclim,
                    verlock=next_step.verlock + 1,
                    usrmod=usrmod,
                    fecmod=now
                )
            )
            result = db.execute(stmt)
            if result.rowcount == 0:
                raise StepConcurrencyError()
            db.refresh(next_step)
            return next_step
        return None

crud_docpaso = CRUDDocPaso()
