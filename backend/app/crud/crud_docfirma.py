from typing import List, Optional, Dict, Any, Sequence
from sqlalchemy.orm import Session
from sqlalchemy import select, func, update
from datetime import datetime, timezone
import uuid

from app.models.docfirma import DocFirma, EstadoDocFirma
from app.models.firpos import Firpos
from app.services.signature_exceptions import SignatureConcurrencyError

class CRUDDocFirma:
    def get_signature_attempt(self, db: Session, firid: int) -> Optional[DocFirma]:
        return db.scalars(select(DocFirma).where(DocFirma.firid == firid)).first()

    def get_signature_attempt_for_update(self, db: Session, firid: int) -> Optional[DocFirma]:
        return db.scalars(
            select(DocFirma)
            .where(DocFirma.firid == firid)
            .with_for_update()
        ).first()

    def get_active_attempt_by_document(self, db: Session, docid: int) -> Optional[DocFirma]:
        return db.scalars(
            select(DocFirma)
            .where(
                DocFirma.docid == docid,
                DocFirma.estado.in_(['INICIADA', 'GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO'])
            )
        ).first()

    def get_completed_attempt_by_participant(self, db: Session, parid: int) -> Optional[DocFirma]:
        return db.scalars(
            select(DocFirma)
            .where(DocFirma.parid == parid, DocFirma.estado == 'COMPLETADA')
        ).first()

    def get_attempts_by_participant(self, db: Session, parid: int) -> Sequence[DocFirma]:
        return db.scalars(
            select(DocFirma)
            .where(DocFirma.parid == parid)
            .order_by(DocFirma.intnum)
        ).all()

    def get_positions_by_attempt(self, db: Session, firid: int) -> Sequence[Firpos]:
        return db.scalars(
            select(Firpos)
            .where(Firpos.firid == firid)
            .order_by(Firpos.orden)
        ).all()

    def get_next_document_sequence(self, db: Session, docid: int) -> int:
        max_seq = db.scalar(
            select(func.max(DocFirma.secuen))
            .where(DocFirma.docid == docid)
        )
        return (max_seq or 0) + 1

    def get_next_participant_attempt(self, db: Session, parid: int) -> int:
        max_attempt = db.scalar(
            select(func.max(DocFirma.intnum))
            .where(DocFirma.parid == parid)
        )
        return (max_attempt or 0) + 1

    def create_signature_attempt(
        self,
        db: Session,
        docid: int,
        parid: int,
        tipfir: str,
        verori: str,
        hasori: str,
        secuen: int,
        intnum: int,
        usrcre: str
    ) -> DocFirma:
        db_obj = DocFirma(
            docid=docid,
            parid=parid,
            opeid=uuid.uuid4(),
            secuen=secuen,
            intnum=intnum,
            tipfir=tipfir,
            estado=EstadoDocFirma.INICIADA,
            verori=verori,
            hasori=hasori,
            fecini=datetime.now(timezone.utc),
            revnum=1,
            usrcre=usrcre
        )
        db.add(db_obj)
        db.flush()
        return db_obj

    def create_signature_positions(
        self,
        db: Session,
        firid: int,
        positions: List[Dict[str, Any]]
    ) -> List[Firpos]:
        db_objs = []
        for pos in positions:
            db_obj = Firpos(
                firid=firid,
                pagina=pos["pagina"],
                posx=pos["posx"],
                posy=pos["posy"],
                ancho=pos["ancho"],
                alto=pos["alto"],
                rotaci=pos.get("rotaci", 0),
                orden=pos["orden"],
                camid=pos.get("camid")
            )
            db.add(db_obj)
            db_objs.append(db_obj)
        db.flush()
        return db_objs

    def update_attempt_state(
        self,
        db: Session,
        firid: int,
        expected_revnum: int,
        new_state: EstadoDocFirma,
        values: Optional[Dict[str, Any]] = None,
        usrmod: Optional[str] = None
    ) -> int:
        now = datetime.now(timezone.utc)
        update_values = {
            "estado": new_state,
            "revnum": expected_revnum + 1,
            "fecmod": now
        }
        if usrmod:
            update_values["usrmod"] = usrmod
        if values:
            update_values.update(values)

        stmt = (
            update(DocFirma)
            .where(DocFirma.firid == firid, DocFirma.revnum == expected_revnum)
            .values(**update_values)
        )
        result = db.execute(stmt)
        if result.rowcount == 0:
            raise SignatureConcurrencyError("Conflicto de concurrencia al actualizar el intento de firma (revnum desactualizado)")
        return expected_revnum + 1

crud_docfirma = CRUDDocFirma()
