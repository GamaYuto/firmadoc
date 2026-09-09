from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - fallback for older installs
    import fitz  # type: ignore[no-redef]

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.crud.crud_docfirma import crud_docfirma
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.docpart import DocPart
from app.models.firpos import Firpos
from app.schemas.pdf_signature import GeneratedPdfArtifact, PdfValidationResult, PngValidationResult
from app.services.pdf_validation_service import PdfValidationService, pdf_validation_service
from app.services.signature_exceptions import (
    SignatureError,
    SignatureIntegrityError,
    SignatureNotFoundError,
    SignaturePayloadError,
    SignaturePlacementError,
    SignatureStateError,
)
from app.services.signature_image_service import SignatureImageService, signature_image_service
from app.services.signature_service import signature_service
from app.services.temporary_artifact_service import TemporaryArtifactService, temporary_artifact_service


_BOGOTA = ZoneInfo("America/Bogota")
_ALLOWED_ROTATIONS = {0, 90, 180, 270}
_FONT_SIZES = (9, 8, 7, 6)


@dataclass(frozen=True, slots=True)
class _SignatureSnapshot:
    firid: int
    docid: int
    parid: int
    opeid: UUID
    revnum: int
    hasori: str
    tipfir: str
    usrcre: str
    participant_nomcom: str
    participant_rolpro: str
    positions: tuple[Firpos, ...]


class PdfSignatureService:
    def __init__(
        self,
        pdf_validator: Optional[PdfValidationService] = None,
        image_validator: Optional[SignatureImageService] = None,
        artifact_service: Optional[TemporaryArtifactService] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.pdf_validator = pdf_validator or pdf_validation_service
        self.image_validator = image_validator or signature_image_service
        self.artifact_service = artifact_service or temporary_artifact_service
        self.clock = clock or self._default_clock

    def _default_clock(self) -> datetime:
        return datetime.now(_BOGOTA)

    def _format_server_time(self) -> str:
        current = self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=_BOGOTA)
        return current.astimezone(_BOGOTA).strftime("%Y-%m-%d %H:%M:%S COT")

    @staticmethod
    def _to_decimal(value: object, field_name: str) -> Decimal:
        try:
            decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise SignaturePlacementError(f"El campo {field_name} es invalido") from exc
        if not decimal_value.is_finite():
            raise SignaturePlacementError(f"El campo {field_name} es invalido")
        return decimal_value

    @staticmethod
    def _to_rotation(value: object) -> int:
        try:
            rotation = int(Decimal(str(value)))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise SignaturePlacementError("La rotacion de la posicion es invalida") from exc
        if rotation not in _ALLOWED_ROTATIONS:
            raise SignaturePlacementError("La rotacion de la posicion es invalida")
        return rotation

    @staticmethod
    def _visual_rect_to_pdf_rect(page: fitz.Page, rect: fitz.Rect) -> fitz.Rect:
        return rect * page.derotation_matrix

    def _resolve_visible_rect(self, page: fitz.Page, pos: Firpos) -> tuple[fitz.Rect, fitz.Rect]:
        visible_width = Decimal(str(page.rect.width))
        visible_height = Decimal(str(page.rect.height))

        x = self._to_decimal(pos.posx, "posx")
        y = self._to_decimal(pos.posy, "posy")
        width = self._to_decimal(pos.ancho, "ancho")
        height = self._to_decimal(pos.alto, "alto")
        self._to_rotation(pos.rotaci or 0)

        if x < 0 or y < 0:
            raise SignaturePlacementError("Las coordenadas de firma deben ser no negativas")
        if width <= 0 or height <= 0:
            raise SignaturePlacementError("El area de firma debe tener ancho y alto positivos")

        visible_rect = fitz.Rect(float(x), float(y), float(x + width), float(y + height))
        if x + width > visible_width or y + height > visible_height:
            raise SignaturePlacementError("La firma excede los limites visibles de la pagina")

        pdf_rect = self._visual_rect_to_pdf_rect(page, visible_rect)
        mediabox = page.mediabox
        epsilon = 0.0001
        if (
            pdf_rect.x0 < -epsilon
            or pdf_rect.y0 < -epsilon
            or pdf_rect.x1 > mediabox.width + epsilon
            or pdf_rect.y1 > mediabox.height + epsilon
        ):
            raise SignaturePlacementError("La firma queda fuera del area de la pagina")

        return visible_rect, pdf_rect

    def _build_internal_html(
        self,
        participant_nomcom: str,
        participant_rolpro: str,
        opeid: UUID,
        source_hash: str,
        font_size: int,
    ) -> str:
        server_time = self._format_server_time()
        nomcom = html.escape(participant_nomcom.strip())
        rolpro = html.escape(participant_rolpro.strip())
        opeid_text = html.escape(str(opeid))
        hash_prefix = html.escape(source_hash[:16])
        return (
            "<div style='margin:0;padding:0;"
            f"font-family:Helvetica;font-size:{font_size}pt;line-height:1.1;color:#000000;text-align:left;'>"
            "<div style='font-weight:bold;'>FIRMADO ELECTRÓNICAMENTE</div>"
            f"<div>{nomcom}</div>"
            f"<div>{rolpro}</div>"
            f"<div>Fecha: {html.escape(server_time)}</div>"
            f"<div>Operación: {opeid_text}</div>"
            f"<div>Origen SHA-256: {hash_prefix}</div>"
            "</div>"
        )

    @staticmethod
    def _internal_html_fits(inner: fitz.Rect, html_body: str, rotaci: int) -> bool:
        test_doc = fitz.open()
        try:
            test_page = test_doc.new_page(width=max(inner.width, 1), height=max(inner.height, 1))
            spare_height, scale = test_page.insert_htmlbox(
                fitz.Rect(0, 0, max(inner.width, 1), max(inner.height, 1)),
                html_body,
                scale_low=1.0,
                rotate=rotaci,
                overlay=True,
            )
            return spare_height >= 0 and scale >= 0.999
        finally:
            test_doc.close()

    def _stamp_internal(
        self,
        page: fitz.Page,
        box: fitz.Rect,
        participant_nomcom: str,
        participant_rolpro: str,
        opeid: UUID,
        source_hash: str,
        rotaci: int,
    ) -> None:
        padding = 4
        inner = fitz.Rect(
            box.x0 + padding,
            box.y0 + padding,
            box.x1 - padding,
            box.y1 - padding,
        )
        if inner.width <= 0 or inner.height <= 0:
            raise SignaturePlacementError("El area de firma interna es demasiado pequena")

        page.draw_rect(box, color=(0, 0, 0), fill=(1, 1, 1), width=0.6, overlay=True)
        candidate_rotations = (rotaci,) if rotaci in {0, 180} else (rotaci, 0)
        for font_size in _FONT_SIZES:
            html_body = self._build_internal_html(
                participant_nomcom,
                participant_rolpro,
                opeid,
                source_hash,
                font_size,
            )
            for candidate_rotation in candidate_rotations:
                if not self._internal_html_fits(inner, html_body, candidate_rotation):
                    continue
                spare_height, scale = page.insert_htmlbox(
                    inner,
                    html_body,
                    scale_low=1.0,
                    rotate=candidate_rotation,
                    overlay=True,
                )
                if spare_height >= 0 and scale >= 0.999:
                    return

        raise SignaturePlacementError("El contenido de la firma interna no cabe ni a 6 puntos")

    def _fit_image_rect(self, box: fitz.Rect, image_width: int, image_height: int, effective_rotation: int) -> fitz.Rect:
        effective_width = float(image_height if effective_rotation in {90, 270} else image_width)
        effective_height = float(image_width if effective_rotation in {90, 270} else image_height)
        if effective_width <= 0 or effective_height <= 0:
            raise SignaturePlacementError("La imagen manuscrita tiene dimensiones invalidas")

        scale = min(box.width / effective_width, box.height / effective_height)
        if scale <= 0:
            raise SignaturePlacementError("El area de firma manuscrita es invalida")

        draw_width = effective_width * scale
        draw_height = effective_height * scale
        return fitz.Rect(
            box.x0 + (box.width - draw_width) / 2,
            box.y0 + (box.height - draw_height) / 2,
            box.x0 + (box.width + draw_width) / 2,
            box.y0 + (box.height + draw_height) / 2,
        )

    def _stamp_handwritten(
        self,
        page: fitz.Page,
        box: fitz.Rect,
        image_meta: PngValidationResult,
        image_path: Path,
        effective_rotation: int,
    ) -> None:
        target_rect = self._fit_image_rect(box, image_meta.width, image_meta.height, effective_rotation)
        page.insert_image(
            target_rect,
            filename=str(image_path),
            keep_proportion=True,
            rotate=effective_rotation,
            overlay=True,
        )

    def _build_session_factory(self, db: Session) -> sessionmaker:
        bind = db.get_bind()
        if bind is None:
            raise SignaturePayloadError("No fue posible determinar la conexion de base de datos")
        return sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)

    def _load_signature_snapshot(self, db: Session, firid: int) -> _SignatureSnapshot:
        session_factory = self._build_session_factory(db)
        read_db = session_factory()
        try:
            firma = read_db.scalars(select(DocFirma).where(DocFirma.firid == firid)).first()
            if not firma:
                raise SignatureNotFoundError("Intento de firma no encontrado")
            if firma.estado != EstadoDocFirma.INICIADA.value:
                raise SignatureStateError("La firma debe estar en estado INICIADA para generar el PDF")

            participant = read_db.scalars(select(DocPart).where(DocPart.parid == firma.parid)).first()
            if not participant:
                raise SignatureNotFoundError("Participante no encontrado")
            if not participant.nomcom or not participant.rolpro:
                raise SignaturePayloadError("La firma requiere nombre y rol institucional")

            positions = tuple(crud_docfirma.get_positions_by_attempt(read_db, firid))
            if not positions:
                raise SignaturePayloadError("La firma no tiene posiciones congeladas")

            return _SignatureSnapshot(
                firid=firma.firid,
                docid=firma.docid,
                parid=firma.parid,
                opeid=firma.opeid,
                revnum=int(firma.revnum),
                hasori=firma.hasori,
                tipfir=firma.tipfir,
                usrcre=firma.usrcre,
                participant_nomcom=participant.nomcom,
                participant_rolpro=participant.rolpro,
                positions=positions,
            )
        finally:
            read_db.close()

    def _mark_generated_short_tx(self, db: Session, firid: int, expected_revnum: int, hasfin: str, usrmod: str) -> int:
        session_factory = self._build_session_factory(db)
        short_db = session_factory()
        try:
            new_revnum = signature_service.mark_generated(
                db=short_db,
                firid=firid,
                expected_revnum=expected_revnum,
                hasfin=hasfin,
                usrmod=usrmod,
            )
            short_db.commit()
            return new_revnum
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _render_pdf_bytes(
        self,
        snapshot: _SignatureSnapshot,
        source_pdf_path: Path | str,
        signature_image_path: Path | str | None = None,
    ) -> tuple[bytes, int]:
        source_bytes = Path(source_pdf_path).read_bytes()
        source_doc = None
        output_doc = None
        try:
            source_doc = fitz.open(stream=source_bytes, filetype="pdf")
            if not source_doc.is_pdf:
                raise SignaturePayloadError("El archivo fuente no es un PDF valido")

            output_doc = fitz.open()
            output_doc.insert_pdf(source_doc)
            output_doc.set_metadata({})

            if snapshot.tipfir == TipoFirma.MANUSCRITA.value:
                if signature_image_path is None:
                    raise SignaturePayloadError("La firma manuscrita requiere una imagen PNG")
                image_meta = self.image_validator.validate_png(signature_image_path)
            else:
                image_meta = None

            for pos in snapshot.positions:
                page_index = int(pos.pagina) - 1
                if page_index < 0 or page_index >= output_doc.page_count:
                    raise SignaturePlacementError("La posicion apunta a una pagina inexistente")

                page = output_doc[page_index]
                _, insert_rect = self._resolve_visible_rect(page, pos)
                rotation = self._to_rotation(pos.rotaci or 0)
                page_rotation = int(page.rotation or 0)
                effective_rotation = (rotation - page_rotation) % 360
                if snapshot.tipfir == TipoFirma.MANUSCRITA.value:
                    assert image_meta is not None
                    assert signature_image_path is not None
                    self._stamp_handwritten(page, insert_rect, image_meta, Path(signature_image_path), effective_rotation)
                elif snapshot.tipfir == TipoFirma.INTERNA.value:
                    self._stamp_internal(
                        page,
                        insert_rect,
                        snapshot.participant_nomcom,
                        snapshot.participant_rolpro,
                        snapshot.opeid,
                        snapshot.hasori,
                        effective_rotation,
                    )
                else:
                    raise SignaturePayloadError("Tipo de firma no soportado")

            pdf_bytes = output_doc.tobytes(
                garbage=3,
                deflate=True,
                use_objstms=1,
                no_new_id=True,
            )
            return pdf_bytes, output_doc.page_count
        except SignatureError:
            raise
        except Exception as exc:
            raise SignaturePayloadError("No fue posible generar el PDF firmado") from exc
        finally:
            if output_doc is not None:
                output_doc.close()
            if source_doc is not None:
                source_doc.close()

    def generate_signature_pdf_and_mark_generated(
        self,
        db: Session,
        firid: int,
        source_pdf_path: Path | str,
        signature_image_path: Path | str | None = None,
        expected_source_hash: str | None = None,
        usrmod: str | None = None,
    ) -> GeneratedPdfArtifact:
        snapshot = self._load_signature_snapshot(db, firid)
        output_path: Path | None = None
        try:
            source_meta = self.pdf_validator.validate_source_pdf(source_pdf_path)
            expected_hash = (expected_source_hash or snapshot.hasori).lower()
            if source_meta.sha256.lower() != expected_hash:
                raise SignatureIntegrityError("El hash del PDF fuente no coincide con el esperado")

            if snapshot.tipfir == TipoFirma.INTERNA.value and signature_image_path is not None:
                raise SignaturePayloadError("La firma interna no acepta imagen")

            if snapshot.tipfir not in (TipoFirma.MANUSCRITA.value, TipoFirma.INTERNA.value):
                raise SignaturePayloadError("Tipo de firma no soportado")

            pdf_bytes, page_count = self._render_pdf_bytes(
                snapshot=snapshot,
                source_pdf_path=source_pdf_path,
                signature_image_path=signature_image_path,
            )
            generated_hash = hashlib.sha256(pdf_bytes).hexdigest()
            output_path = self.artifact_service.write_bytes(pdf_bytes, prefix=f"fir-{firid}-", suffix=".pdf")

            usrmod_final = usrmod or snapshot.usrcre
            self._mark_generated_short_tx(
                db=db,
                firid=firid,
                expected_revnum=snapshot.revnum,
                hasfin=generated_hash,
                usrmod=usrmod_final,
            )

            return GeneratedPdfArtifact(
                firid=firid,
                opeid=snapshot.opeid,
                path=output_path,
                sha256=generated_hash,
                size_bytes=len(pdf_bytes),
                page_count=page_count,
            )
        except SignatureError:
            if output_path is not None:
                self.artifact_service.cleanup_path(output_path)
            raise
        except Exception as exc:
            if output_path is not None:
                self.artifact_service.cleanup_path(output_path)
            raise SignaturePayloadError("No fue posible generar el PDF firmado") from exc
        finally:
            self.artifact_service.cleanup_path(signature_image_path)

    def generate_signed_pdf(
        self,
        db: Session,
        firid: int,
        source_pdf_path: Path | str,
        signature_image_path: Path | str | None = None,
        expected_source_hash: str | None = None,
        usrmod: str | None = None,
    ) -> GeneratedPdfArtifact:
        return self.generate_signature_pdf_and_mark_generated(
            db=db,
            firid=firid,
            source_pdf_path=source_pdf_path,
            signature_image_path=signature_image_path,
            expected_source_hash=expected_source_hash,
            usrmod=usrmod,
        )

    def generate_pdf_artifact(
        self,
        db: Session,
        firid: int,
        source_pdf_path: Path | str,
        signature_image_path: Path | str | None = None,
        expected_source_hash: str | None = None,
        usrmod: str | None = None,
    ) -> GeneratedPdfArtifact:
        return self.generate_signature_pdf_and_mark_generated(
            db=db,
            firid=firid,
            source_pdf_path=source_pdf_path,
            signature_image_path=signature_image_path,
            expected_source_hash=expected_source_hash,
            usrmod=usrmod,
        )


pdf_signature_service = PdfSignatureService()
