from __future__ import annotations

import hashlib
import html
import os
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

from app.core.config import settings
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
from app.services.verification_code_service import encode_verification_code


_BOGOTA = ZoneInfo("America/Bogota")
_ALLOWED_ROTATIONS = {0, 90, 180, 270}
_FONT_SIZES = (9, 8, 7, 6)
_MANAGER_FONT_PROFILES = ((15, 7), (13, 7), (11, 6), (9, 6))


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
    step_type: str
    positions: tuple[Firpos, ...]
    participant_usrid: str = ""


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

    def _server_time(self) -> datetime:
        current = self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=_BOGOTA)
        return current.astimezone(_BOGOTA)

    def _format_server_time(self) -> str:
        return self._server_time().strftime("%Y-%m-%d %H:%M:%S COT")

    def _format_manager_time(self) -> str:
        return self._server_time().strftime("%d/%m/%Y %H:%M COT")

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
        step_type: str = "FIRMAR",
    ) -> str:
        if step_type == "APROBAR":
            return self._build_manager_approval_html(
                participant_nomcom,
                participant_rolpro,
                opeid,
                source_hash,
                signature_font_size=font_size + 6,
                body_font_size=max(6, font_size),
                detail_level="full",
            )
        server_time = self._format_server_time()
        nomcom = html.escape(participant_nomcom.strip())
        rolpro = html.escape(participant_rolpro.strip())
        opeid_text = html.escape(str(opeid))
        hash_prefix = html.escape(source_hash[:16])
        return (
            "<div style='margin:0;padding:0;"
            f"font-family:Helvetica;font-size:{font_size}pt;line-height:1.1;color:#000000;text-align:left;'>"
            "<div style='font-weight:bold;'>FIRMADO ELECTR&#211;NICAMENTE</div>"
            f"<div>{nomcom}</div>"
            f"<div>{rolpro}</div>"
            f"<div>Fecha: {html.escape(server_time)}</div>"
            f"<div>Operaci&#243;n: {opeid_text}</div>"
            f"<div>Origen SHA-256: {hash_prefix}</div>"
            "</div>"
        )

    def _build_manager_approval_html(
        self,
        participant_nomcom: str,
        participant_rolpro: str,
        opeid: UUID,
        source_hash: str,
        *,
        signature_font_size: int,
        body_font_size: int,
        detail_level: str,
    ) -> str:
        clean_name = participant_nomcom.strip()
        nomcom = html.escape(clean_name)
        identity = html.escape(clean_name.upper())
        role = html.escape((participant_rolpro or "Gerencia").strip())
        timestamp = html.escape(self._format_manager_time())
        operation_short = html.escape(str(opeid).replace("-", "")[:12])
        hash_short = html.escape(source_hash[:12])
        lines = [
            f"<div style='font-family:Times-Roman,serif;font-style:italic;font-size:{signature_font_size}pt;line-height:1;color:#123B66;'>{nomcom}</div>",
            f"<div style='font-family:Helvetica;font-size:{body_font_size}pt;line-height:1.05;font-weight:bold;color:#111111;'>{identity}</div>",
        ]
        if detail_level in {"full", "compact"}:
            lines.append(f"<div style='font-family:Helvetica;font-size:{body_font_size}pt;line-height:1.05;color:#111111;'>{role}</div>")
        lines.extend([
            f"<div style='font-family:Helvetica;font-size:{body_font_size}pt;line-height:1.05;color:#111111;'>Autorizado electr&#243;nicamente</div>",
            f"<div style='font-family:Helvetica;font-size:{body_font_size}pt;line-height:1.05;color:#111111;'>{timestamp}</div>",
        ])
        if detail_level == "full":
            lines.append(f"<div style='font-family:Helvetica;font-size:{max(5, body_font_size - 1)}pt;line-height:1.05;color:#333333;'>ID: {operation_short} | SHA: {hash_short}</div>")
        elif detail_level == "compact":
            lines.append(f"<div style='font-family:Helvetica;font-size:{max(5, body_font_size - 1)}pt;line-height:1.05;color:#333333;'>ID: {operation_short}</div>")
        else:
            lines.append(f"<div style='font-family:Helvetica;font-size:{max(5, body_font_size - 1)}pt;line-height:1.05;color:#333333;'>ID: {operation_short}</div>")
        return "<div style='margin:0;padding:0;text-align:left;'>" + "".join(lines) + "</div>"

    def _manager_approval_html_candidates(
        self,
        participant_nomcom: str,
        participant_rolpro: str,
        opeid: UUID,
        source_hash: str,
    ) -> tuple[str, ...]:
        candidates: list[str] = []
        for detail_level in ("full", "compact", "minimal"):
            for signature_size, body_size in _MANAGER_FONT_PROFILES:
                candidates.append(
                    self._build_manager_approval_html(
                        participant_nomcom,
                        participant_rolpro,
                        opeid,
                        source_hash,
                        signature_font_size=signature_size,
                        body_font_size=body_size,
                        detail_level=detail_level,
                    )
                )
        return tuple(candidates)

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
        step_type: str = "FIRMAR",
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

        candidate_rotations = (rotaci,) if rotaci in {0, 180} else (rotaci, 0)
        if step_type == "APROBAR":
            html_candidates = self._manager_approval_html_candidates(
                participant_nomcom,
                participant_rolpro,
                opeid,
                source_hash,
            )
        else:
            page.draw_rect(box, color=(0, 0, 0), fill=(1, 1, 1), width=0.6, overlay=True)
            html_candidates = tuple(
                self._build_internal_html(
                    participant_nomcom,
                    participant_rolpro,
                    opeid,
                    source_hash,
                    font_size,
                    step_type,
                )
                for font_size in _FONT_SIZES
            )
        for html_body in html_candidates:
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

        raise SignaturePlacementError("El contenido de la firma interna no cabe en el area definida")

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

    def _resolve_lblanco_signature_path(self) -> Path:
        configured = (settings.FIRMADOC_LBLANCO_SIGNATURE_PATH or "").strip()
        if configured:
            p = Path(configured)
            if p.exists() and p.is_file():
                return p
            raise SignaturePayloadError("Imagen de firma institucional no disponible")

        candidates = [
            Path("/var/lib/firmadoc/private/firma2.png"),
            Path(os.path.expanduser("~/.firmadoc/private/firma2.png")),
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        raise SignaturePayloadError("Imagen de firma institucional no disponible")

    @staticmethod
    def _lerp_color(c1: tuple[float, float, float], c2: tuple[float, float, float], t: float) -> tuple[float, float, float]:
        t = max(0.0, min(1.0, t))
        return tuple(c1[i] + (c2[i] - c1[i]) * t for i in range(3))

    @staticmethod
    def _cubic_bezier_point(p0: fitz.Point, p1: fitz.Point, p2: fitz.Point, p3: fitz.Point, t: float) -> fitz.Point:
        mt = 1.0 - t
        x = mt ** 3 * p0.x + 3 * mt ** 2 * t * p1.x + 3 * mt * t ** 2 * p2.x + t ** 3 * p3.x
        y = mt ** 3 * p0.y + 3 * mt ** 2 * t * p1.y + 3 * mt * t ** 2 * p2.y + t ** 3 * p3.y
        return fitz.Point(x, y)

    def _draw_institutional_bracket(
        self,
        stamp_page: fitz.Page,
        w: float,
        h: float,
        pad: float,
        arm_len: float,
        bracket_x: float,
        corner_r: float,
        stroke_width: float = 1.6,
    ) -> None:
        # Degradado vertical cyan (#00AFEF) -> azul institucional (#08337B),
        # replicando el linearGradient del componente de diseno oficial.
        cyan = (0.0, 0.686, 0.937)
        navy = (0.031, 0.200, 0.482)
        safe_h = max(h - 2 * pad, 0.1)
        safe_r = min(corner_r, safe_h / 2.1, arm_len / 1.5)
        gradient_span = max(h - 2 * pad, 0.1)

        segments: list[tuple[fitz.Point, fitz.Point]] = []

        def add_line(p0: fitz.Point, p1: fitz.Point, steps: int = 1) -> None:
            steps = max(1, steps)
            for i in range(steps):
                t0 = i / steps
                t1 = (i + 1) / steps
                segments.append((
                    fitz.Point(p0.x + (p1.x - p0.x) * t0, p0.y + (p1.y - p0.y) * t0),
                    fitz.Point(p0.x + (p1.x - p0.x) * t1, p0.y + (p1.y - p0.y) * t1),
                ))

        def add_bezier(p0: fitz.Point, p1: fitz.Point, p2: fitz.Point, p3: fitz.Point, steps: int = 8) -> None:
            prev = p0
            for i in range(1, steps + 1):
                point = self._cubic_bezier_point(p0, p1, p2, p3, i / steps)
                segments.append((prev, point))
                prev = point

        top_right = fitz.Point(bracket_x + arm_len, pad)
        top_corner_start = fitz.Point(bracket_x + safe_r, pad)
        top_corner_c1 = fitz.Point(bracket_x, pad)
        top_corner_end = fitz.Point(bracket_x, pad + safe_r)
        bottom_corner_start = fitz.Point(bracket_x, h - pad - safe_r)
        bottom_corner_c1 = fitz.Point(bracket_x, h - pad)
        bottom_corner_end = fitz.Point(bracket_x + safe_r, h - pad)
        bottom_right = fitz.Point(bracket_x + arm_len, h - pad)

        add_line(top_right, top_corner_start, steps=2)
        add_bezier(top_corner_start, top_corner_c1, top_corner_end, top_corner_end, steps=8)
        add_line(top_corner_end, bottom_corner_start, steps=max(2, int(safe_h / 3)))
        add_bezier(bottom_corner_start, bottom_corner_c1, bottom_corner_end, bottom_corner_end, steps=8)
        add_line(bottom_corner_end, bottom_right, steps=2)

        for p0, p1 in segments:
            mid_y = (p0.y + p1.y) / 2
            t = (mid_y - pad) / gradient_span
            color = self._lerp_color(cyan, navy, t)
            shape = stamp_page.new_shape()
            shape.draw_line(p0, p1)
            shape.finish(color=color, width=stroke_width, lineCap=1, lineJoin=1)
            shape.commit(overlay=True)

    def _draw_verification_code_chip(
        self,
        stamp_page: fitz.Page,
        rect: fitz.Rect,
        verification_code: str,
        font_size: float,
    ) -> None:
        # Caja clara tipo "chip" alrededor del codigo (sin etiqueta de texto),
        # replicando el componente de diseno oficial del sello.
        chip_fill = (0.973, 0.980, 0.988)  # #F8FAFC (slate-50)
        chip_border = (0.945, 0.961, 0.976)  # #F1F5F9 (slate-100)
        pad_x = max(1.0, font_size * 0.35)
        pad_y = max(0.5, min(rect.height * 0.15, font_size * 0.25))
        estimated_text_width = font_size * 0.62 * len(verification_code)
        chip_width = max(1.0, min(rect.width, estimated_text_width + 2 * pad_x))
        chip_height = max(1.0, min(rect.height, font_size * 1.5 + 2 * pad_y))
        chip_y0 = rect.y0 + max(0.0, (rect.height - chip_height) / 2)
        chip_rect = fitz.Rect(rect.x0, chip_y0, rect.x0 + chip_width, chip_y0 + chip_height)

        stamp_page.draw_rect(
            chip_rect,
            color=chip_border,
            fill=chip_fill,
            width=max(0.25, font_size * 0.035),
            radius=0.18,
            overlay=True,
        )
        code_html = (
            "<div style='margin:0;padding:0;font-family:Courier,monospace;font-weight:bold;"
            f"color:#08337B;font-size:{font_size}pt;line-height:1.1;text-align:left;white-space:nowrap;'>"
            f"{html.escape(verification_code)}</div>"
        )
        text_rect = fitz.Rect(chip_rect.x0 + pad_x, chip_rect.y0, chip_rect.x1 - pad_x * 0.3, chip_rect.y1)
        stamp_page.insert_htmlbox(text_rect, code_html, scale_low=0.1, overlay=True)

    def _render_institutional_stamp(
        self,
        page: fitz.Page,
        box: fitz.Rect,
        opeid: UUID,
        signature_image_path: Path,
        effective_rotation: int = 0,
    ) -> None:
        effective_w = box.width if effective_rotation in (0, 180) else box.height
        effective_h = box.height if effective_rotation in (0, 180) else box.width

        if effective_w <= 0 or effective_h <= 0:
            raise SignaturePlacementError("El area de firma debe tener ancho y alto positivos")

        if not signature_image_path.exists() or not signature_image_path.is_file():
            raise SignaturePayloadError("Imagen de firma institucional no disponible")

        try:
            self.image_validator.validate_png(signature_image_path)
        except Exception as exc:
            raise SignaturePayloadError("La imagen de firma institucional es invalida o corrupta") from exc

        verification_code = encode_verification_code(opeid)

        stamp_doc = fitz.open()
        try:
            stamp_page = stamp_doc.new_page(width=effective_w, height=effective_h)
            pad = max(1.0, min(3.0, effective_h * 0.05))
            avail_h = max(1.0, effective_h - 2 * pad)
            corner_r = max(1.0, min(5.0, effective_h * 0.08))
            arm_len = max(3.0, min(14.0, effective_w * 0.08))
            bracket_x = pad + max(0.5, min(1.5, effective_w * 0.01))
            stroke_width = max(0.6, min(1.6, effective_h * 0.025))

            self._draw_institutional_bracket(
                stamp_page, effective_w, effective_h, pad, arm_len, bracket_x, corner_r, stroke_width
            )

            content_left = bracket_x + max(4.0, min(18.0, effective_w * 0.08))
            content_right = effective_w - pad - max(1.0, min(4.0, effective_w * 0.02))
            content_w = max(1.0, content_right - content_left)

            is_wide_flat = (effective_w >= 140.0) and (effective_h <= 35.0) and (effective_w / max(1.0, effective_h) >= 3.5)

            if is_wide_flat:
                # Layout lateral para marcos anchos y bajos
                sig_col_w = min(content_w * 0.52, effective_h * 2.8)
                sig_box = fitz.Rect(content_left, pad + 1.0, content_left + sig_col_w, effective_h - pad - 1.0)
                with fitz.open(signature_image_path) as img_doc:
                    img_page = img_doc[0]
                    img_w = float(img_page.rect.width)
                    img_h = float(img_page.rect.height)
                    if img_w > 0 and img_h > 0 and sig_box.width > 0 and sig_box.height > 0:
                        scale = min(sig_box.width / img_w, sig_box.height / img_h)
                        draw_w = img_w * scale
                        draw_h = img_h * scale
                        target_x0 = sig_box.x0 + (sig_box.width - draw_w) / 2
                        target_y0 = sig_box.y0 + (sig_box.height - draw_h) / 2
                        target_rect = fitz.Rect(target_x0, target_y0, target_x0 + draw_w, target_y0 + draw_h)
                        stamp_page.insert_image(
                            target_rect,
                            filename=str(signature_image_path),
                            keep_proportion=True,
                            overlay=True,
                        )

                div_x = content_left + sig_col_w + 2.0
                div_shape = stamp_page.new_shape()
                div_shape.draw_line(fitz.Point(div_x, pad + 2.0), fitz.Point(div_x, effective_h - pad - 2.0))
                div_shape.finish(color=(0.85, 0.85, 0.85), width=0.5)
                div_shape.commit(overlay=True)

                text_left = div_x + 3.0
                top_h = avail_h * 0.45
                bottom_h = avail_h * 0.45

                top_rect = fitz.Rect(text_left, pad + 1.0, content_right, pad + 1.0 + top_h)
                header_font_size = max(3.5, min(6.5, top_h * 0.65))
                header_html = (
                    f"<div style='margin:0;padding:0;font-family:Helvetica;font-size:{header_font_size}pt;line-height:1;text-align:left;white-space:nowrap;'>"
                    "<span style='color:#00AFEF;'>&#9679;</span> "
                    "<span style='color:#08337B;font-weight:bold;'>VALIDADO POR FIRMADOC</span> "
                    "<span style='color:#696B6D;'>&middot; Cl&iacute;nica del Caribe</span>"
                    "</div>"
                )
                stamp_page.insert_htmlbox(top_rect, header_html, scale_low=0.1, overlay=True)

                code_rect = fitz.Rect(text_left, effective_h - pad - bottom_h - 1.0, content_right, effective_h - pad - 1.0)
                code_font_size = max(3.5, min(6.5, bottom_h * 0.65))
                self._draw_verification_code_chip(stamp_page, code_rect, verification_code, code_font_size)

            else:
                # Layout apilado adaptativo
                show_divider = effective_h >= 35.0
                top_h = max(3.5, min(11.0, avail_h * 0.20))
                bottom_h = max(3.5, min(12.0, avail_h * 0.22))
                gap = max(0.3, min(2.0, avail_h * 0.03)) if show_divider else 0.5

                top_rect = fitz.Rect(content_left, pad + 1.0, content_right, pad + 1.0 + top_h)
                header_font_size = max(3.0, min(6.5, top_h * 0.70))
                dot_font_size = header_font_size + 0.5
                header_html = (
                    f"<div style='margin:0;padding:0;font-family:Helvetica;font-size:{header_font_size}pt;line-height:1;text-align:left;white-space:nowrap;'>"
                    f"<span style='color:#00AFEF;font-size:{dot_font_size}pt;'>&#9679;</span> "
                    "<span style='color:#08337B;font-weight:bold;letter-spacing:0.3px;'>VALIDADO POR FIRMADOC</span> "
                    "<span style='color:#696B6D;'>&middot; Cl&iacute;nica del Caribe</span>"
                    "</div>"
                )
                stamp_page.insert_htmlbox(top_rect, header_html, scale_low=0.1, overlay=True)

                bottom_y = effective_h - pad - bottom_h - (gap if show_divider else 0.0)
                if show_divider:
                    div_shape = stamp_page.new_shape()
                    div_shape.draw_line(fitz.Point(content_left, bottom_y), fitz.Point(content_right, bottom_y))
                    div_shape.finish(color=(0.85, 0.85, 0.85), width=max(0.2, min(0.5, effective_h * 0.008)))
                    div_shape.commit(overlay=True)

                code_rect = fitz.Rect(content_left, bottom_y + (0.5 if show_divider else 0.0), content_right, effective_h - pad)
                code_font_size = max(3.0, min(7.0, bottom_h * 0.65))
                self._draw_verification_code_chip(stamp_page, code_rect, verification_code, code_font_size)

                sig_top = pad + top_h + gap
                sig_bottom = bottom_y - gap
                sig_h = sig_bottom - sig_top
                if sig_h > 0 and content_w > 0:
                    sig_box = fitz.Rect(content_left, sig_top, content_right, sig_bottom)
                    with fitz.open(signature_image_path) as img_doc:
                        img_page = img_doc[0]
                        img_w = float(img_page.rect.width)
                        img_h = float(img_page.rect.height)
                        if img_w > 0 and img_h > 0:
                            scale = min(sig_box.width / img_w, sig_box.height / img_h)
                            draw_w = img_w * scale
                            draw_h = img_h * scale
                            target_x0 = sig_box.x0 + (sig_box.width - draw_w) / 2
                            target_y0 = sig_box.y0 + (sig_box.height - draw_h) / 2
                            target_rect = fitz.Rect(target_x0, target_y0, target_x0 + draw_w, target_y0 + draw_h)
                            stamp_page.insert_image(
                                target_rect,
                                filename=str(signature_image_path),
                                keep_proportion=True,
                                overlay=True,
                            )

            page.show_pdf_page(box, stamp_doc, pno=0, rotate=effective_rotation)
        finally:
            stamp_doc.close()

    _render_manager_institutional_stamp = _render_institutional_stamp

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
            step = participant.docpaso
            if not step:
                raise SignatureNotFoundError("Paso no encontrado")

            positions = tuple(crud_docfirma.get_positions_by_attempt(read_db, firid))
            if not positions:
                raise SignaturePayloadError("La firma no tiene posiciones congeladas")

            participant_usrid = (participant.usrid or "").strip().lower()
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
                step_type=step.pastip,
                positions=positions,
                participant_usrid=participant_usrid,
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
                    self._render_institutional_stamp(
                        page=page,
                        box=insert_rect,
                        opeid=snapshot.opeid,
                        signature_image_path=Path(signature_image_path),
                        effective_rotation=effective_rotation,
                    )
                elif snapshot.tipfir == TipoFirma.INTERNA.value:
                    if snapshot.step_type == "APROBAR" and snapshot.participant_usrid == "lblanco":
                        img_path = self._resolve_lblanco_signature_path()
                        self._render_institutional_stamp(
                            page=page,
                            box=insert_rect,
                            opeid=snapshot.opeid,
                            signature_image_path=img_path,
                            effective_rotation=effective_rotation,
                        )
                    else:
                        self._stamp_internal(
                            page,
                            insert_rect,
                            snapshot.participant_nomcom,
                            snapshot.participant_rolpro,
                            snapshot.opeid,
                            snapshot.hasori,
                            effective_rotation,
                            snapshot.step_type,
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
