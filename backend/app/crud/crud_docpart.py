from typing import List, Optional, Dict, Any, Sequence
from sqlalchemy.orm import Session
from sqlalchemy import select, update, func
from datetime import datetime, timezone

from app.models.docpart import DocPart
from app.core.identity import IdentitySnapshot
from app.schemas.docpart import DocPartTransition

class ParticipantConcurrencyError(Exception):
    def __init__(self, message: str = "Conflicto de concurrencia al actualizar el participante"):
        super().__init__(message)

class CRUDDocPart:
    def get(self, db: Session, parid: int) -> Optional[DocPart]:
        return db.scalars(select(DocPart).where(DocPart.parid == parid)).first()

    def get_step_id(self, db: Session, parid: int) -> Optional[int]:
        return db.scalar(select(DocPart.dpasid).where(DocPart.parid == parid))


    def get_for_update(self, db: Session, parid: int) -> Optional[DocPart]:
        return db.scalars(
            select(DocPart)
            .where(DocPart.parid == parid)
            .with_for_update()
        ).first()

    def list_by_step(self, db: Session, dpasid: int) -> Sequence[DocPart]:
        return db.scalars(
            select(DocPart)
            .where(DocPart.dpasid == dpasid)
            .order_by(DocPart.orden)
        ).all()

    def list_by_step_for_update(self, db: Session, dpasid: int) -> Sequence[DocPart]:
        return db.scalars(
            select(DocPart)
            .where(DocPart.dpasid == dpasid)
            .order_by(DocPart.parid)  # Lock order by PK to avoid deadlocks
            .with_for_update()
        ).all()

    def list_by_user(self, db: Session, usrid: str, estado: Optional[str] = None) -> Sequence[DocPart]:
        stmt = select(DocPart).where(DocPart.usrid == usrid)
        if estado:
            stmt = stmt.where(DocPart.estado == estado)
        return db.scalars(stmt).all()

    def create_many(self, db: Session, dpasid: int, participants_data: List[Dict[str, Any]], usrcre: str) -> List[DocPart]:
        db_objs = []
        for data in participants_data:
            snapshot: IdentitySnapshot = data["snapshot"]
            db_obj = DocPart(
                dpasid=dpasid,
                usrid=snapshot.usrid,
                nomcom=snapshot.nomcom,
                correo=snapshot.correo,
                rolpro=snapshot.rolpro,
                orden=data["orden"],
                obliga=data["obliga"],
                estado=data["estado"],
                usrcre=usrcre,
                verlock=1
            )
            db.add(db_obj)
            db_objs.append(db_obj)
        db.flush()
        return db_objs

    def transition(self, db: Session, parid: int, transition_data: DocPartTransition) -> None:
        values = {
            "estado": transition_data.estado,
            "verlock": transition_data.expected_verlock + 1,
            "usrmod": transition_data.usrmod,
            "fecmod": datetime.now(timezone.utc)
        }
        if transition_data.motivo is not None:
            values["motivo"] = transition_data.motivo
        
        if transition_data.fecdis is not None:
            values["fecdis"] = transition_data.fecdis
            
        if transition_data.fecini is not None:
            values["fecini"] = transition_data.fecini
            
        if transition_data.clear_fecfin:
            values["fecfin"] = None
        elif transition_data.fecfin is not None:
            values["fecfin"] = transition_data.fecfin

        stmt = (
            update(DocPart)
            .where(DocPart.parid == parid, DocPart.verlock == transition_data.expected_verlock)
            .values(**values)
        )
        result = db.execute(stmt)
        if result.rowcount == 0:
            raise ParticipantConcurrencyError()

    def save_result(self, db: Session, parid: int, expected_verlock: int, result_data: Dict[str, Any], usrmod: str) -> None:
        stmt = (
            update(DocPart)
            .where(DocPart.parid == parid, DocPart.verlock == expected_verlock)
            .values(
                result=result_data,
                verlock=expected_verlock + 1,
                usrmod=usrmod,
                fecmod=datetime.now(timezone.utc)
            )
        )
        result = db.execute(stmt)
        if result.rowcount == 0:
            raise ParticipantConcurrencyError()

    def count_by_state(self, db: Session, dpasid: int, estado: str) -> int:
        return db.scalar(
            select(func.count(DocPart.parid))
            .where(DocPart.dpasid == dpasid, DocPart.estado == estado)
        ) or 0

crud_docpart = CRUDDocPart()
