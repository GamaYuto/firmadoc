from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.docfir import DocFir
from app.models.docfirma import DocFirma, EstadoDocFirma
from app.models.docpart import DocPart
from app.models.docpaso import DocPaso
from app.models.sesionqr import EstadoSesionQr, SesionQr
from app.schemas.firma_frontend import SignatureResult
from app.services.frontend_signature_service import frontend_signature_service


class QrService:
    @staticmethod
    def _assert_handwritten_flow(db: Session, firma: DocFirma) -> None:
        step = db.scalar(
            select(DocPaso)
            .join(DocPart, DocPart.dpasid == DocPaso.dpasid)
            .where(DocPart.parid == firma.parid)
        )
        if not step or step.pastip != "FIRMAR" or firma.tipfir != "MANUSCRITA":
            raise HTTPException(status_code=404, detail="Sesion QR no encontrada")

    def crear_sesion_qr(
        self,
        db: Session,
        firid: int,
        requester_user_id: str,
        iporig: Optional[str] = None,
    ) -> tuple[int, str, datetime]:
        firma = db.get(DocFirma, firid)
        if not firma:
            raise HTTPException(status_code=404, detail="Firma no encontrada")
        self._assert_handwritten_flow(db, firma)

        if firma.estado in (
            EstadoDocFirma.COMPLETADA.value,
            EstadoDocFirma.FALLIDA.value,
            EstadoDocFirma.CANCELADA.value,
        ):
            raise HTTPException(status_code=409, detail=f"La firma ya se encuentra {firma.estado}")

        participante = db.get(DocPart, firma.parid)
        documento = db.get(DocFir, firma.docid)
        requester = requester_user_id.strip().lower()
        is_signer = participante and participante.usrid.lower() == requester
        is_preparer = documento and (documento.usrcre or "").strip().lower() == requester
        if not participante or not (is_signer or is_preparer):
            raise HTTPException(status_code=403, detail="No autorizado para generar QR de esta firma")

        # Invalidar sesiones QR previas pendientes para esta firma
        previas = db.scalars(
            select(SesionQr).where(
                SesionQr.firid == firid,
                SesionQr.estado == EstadoSesionQr.PENDIENTE.value,
            )
        ).all()
        for p in previas:
            p.estado = EstadoSesionQr.CANCELADO.value

        raw_token = secrets.token_urlsafe(32)
        tokhas = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        fecexp = datetime.now(timezone.utc) + timedelta(minutes=10)

        sesion = SesionQr(
            firid=firma.firid,
            docid=firma.docid,
            usrid=participante.usrid,
            tokhas=tokhas,
            estado=EstadoSesionQr.PENDIENTE.value,
            fecexp=fecexp,
            iporig=iporig,
        )
        db.add(sesion)
        db.commit()
        db.refresh(sesion)

        return sesion.sesid, raw_token, fecexp

    def consultar_estado_qr(self, db: Session, sesid: int) -> str:
        sesion = db.get(SesionQr, sesid)
        if not sesion:
            return "NO_ENCONTRADA"
        self._assert_handwritten_flow(db, sesion.firma)

        if sesion.estado == EstadoSesionQr.PENDIENTE.value:
            now = datetime.now(timezone.utc)
            if now > sesion.fecexp:
                sesion.estado = EstadoSesionQr.EXPIRADO.value
                db.commit()

        return sesion.estado

    def obtener_sesion_movil(self, db: Session, raw_token: str) -> SesionQr:
        tokhas = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        sesion = db.scalar(select(SesionQr).where(SesionQr.tokhas == tokhas))
        if not sesion:
            raise HTTPException(status_code=404, detail="Sesion QR no encontrada")
        self._assert_handwritten_flow(db, sesion.firma)

        now = datetime.now(timezone.utc)
        if now > sesion.fecexp:
            if sesion.estado == EstadoSesionQr.PENDIENTE.value:
                sesion.estado = EstadoSesionQr.EXPIRADO.value
                db.commit()
            raise HTTPException(status_code=410, detail="La sesion QR ha expirado")

        if sesion.estado != EstadoSesionQr.PENDIENTE.value:
            raise HTTPException(status_code=409, detail=f"La sesion QR ya fue {sesion.estado.lower()}")

        return sesion

    async def completar_firma_movil(
        self,
        db: Session,
        raw_token: str,
        png_data_url: str,
        mobile_user_id: str,
        iporig: Optional[str] = None,
    ) -> SignatureResult:
        sesion = self.obtener_sesion_movil(db, raw_token)

        if sesion.usrid.lower() != mobile_user_id.strip().lower():
            raise HTTPException(
                status_code=403,
                detail=f"Usuario autenticado ({mobile_user_id}) no corresponde al firmante asignado ({sesion.usrid})",
            )

        # Confirmar la firma manuscrita reutilizando el motor existente
        result = await frontend_signature_service.confirm_handwritten(
            db=db,
            firid=sesion.firid,
            png_data_url=png_data_url,
            actor_user=mobile_user_id,
            iporig=iporig or "",
        )

        sesion.estado = EstadoSesionQr.USADO.value
        sesion.fecusa = datetime.now(timezone.utc)
        db.commit()

        return result


qr_service = QrService()
