from __future__ import annotations

import base64
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.docfir import DocFir
from app.models.docfirma import DocFirma, EstadoDocFirma
from app.models.docpart import DocPart


class VerificationResult(BaseModel):
    codigo: str
    estado_verificacion: str  # COMPLETADA, EN_PROCESO, FALLIDA_O_CONFLICTO, NO_ENCONTRADO
    mensaje: str
    firmante_nombre: Optional[str] = None
    firmante_rol: Optional[str] = None
    institucion: str = "Clínica del Caribe S.A."
    documento_nombre: Optional[str] = None
    fecha_firma: Optional[datetime] = None


def encode_verification_code(opeid: UUID) -> str:
    """
    Codifica de forma determinista y reversible un UUIDv4 en un código compacto FMD-...
    Utiliza Base32 sin padding (RFC 4648), generando exactamente 26 caracteres alfanuméricos legibles.
    Longitud total: 30 caracteres ("FMD-" + 26 caracteres).
    """
    raw_b32 = base64.b32encode(opeid.bytes).decode("ascii").rstrip("=")
    return f"FMD-{raw_b32}"


def decode_verification_code(code: str) -> Optional[UUID]:
    """
    Decodifica un código FMD-... retornando el UUIDv4 original.
    Retorna None si el formato o longitud son inválidos.
    """
    clean = code.strip().upper()
    if not clean.startswith("FMD-"):
        return None
    raw_b32 = clean[4:]
    padding_needed = (8 - len(raw_b32) % 8) % 8
    try:
        raw_bytes = base64.b32decode(raw_b32 + "=" * padding_needed)
        if len(raw_bytes) != 16:
            return None
        return UUID(bytes=raw_bytes)
    except Exception:
        return None


def verify_code(db: Session, code: str) -> VerificationResult:
    """
    Consulta de solo lectura para verificación de firmas por código institucional.
    No expone credenciales, firmas de imagen, documentos clínicos ni hashes completos.
    """
    clean_code = code.strip().upper()
    opeid = decode_verification_code(clean_code)
    if not opeid:
        return VerificationResult(
            codigo=clean_code,
            estado_verificacion="NO_ENCONTRADO",
            mensaje="El código de verificación proporcionado no tiene un formato válido o no existe.",
        )

    row = db.execute(
        select(DocFirma, DocPart, DocFir)
        .join(DocPart, DocPart.parid == DocFirma.parid)
        .join(DocFir, DocFir.docid == DocFirma.docid)
        .where(DocFirma.opeid == opeid)
    ).first()

    if not row:
        return VerificationResult(
            codigo=clean_code,
            estado_verificacion="NO_ENCONTRADO",
            mensaje="No se encontró ningún registro asociado a este código de verificación.",
        )

    firma, participant, doc = row

    if firma.estado == EstadoDocFirma.COMPLETADA.value:
        return VerificationResult(
            codigo=clean_code,
            estado_verificacion="COMPLETADA",
            mensaje="Firma electrónica institucional verificada y confirmada.",
            firmante_nombre=participant.nomcom,
            firmante_rol=participant.rolpro or "Gerencia",
            institucion="Clínica del Caribe S.A.",
            documento_nombre=doc.docnom,
            fecha_firma=firma.fecfin or firma.fecmod,
        )
    elif firma.estado in (
        EstadoDocFirma.INICIADA.value,
        EstadoDocFirma.GENERADA.value,
        EstadoDocFirma.SUBIENDO.value,
        EstadoDocFirma.CARGADA.value,
        EstadoDocFirma.VERIFICANDO.value,
    ):
        return VerificationResult(
            codigo=clean_code,
            estado_verificacion="EN_PROCESO",
            mensaje="La firma electrónica asociada a este código se encuentra actualmente en proceso.",
            firmante_nombre=participant.nomcom,
            firmante_rol=participant.rolpro or "Gerencia",
            institucion="Clínica del Caribe S.A.",
            documento_nombre=doc.docnom,
        )
    else:
        return VerificationResult(
            codigo=clean_code,
            estado_verificacion="FALLIDA_O_CONFLICTO",
            mensaje="La solicitud de firma no concluyó satisfactoriamente o fue cancelada.",
            institucion="Clínica del Caribe S.A.",
        )
