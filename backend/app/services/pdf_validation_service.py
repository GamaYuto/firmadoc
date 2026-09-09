from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Optional

import pymupdf as fitz

from app.core.config import settings
from app.schemas.pdf_signature import PdfValidationResult
from app.services.signature_exceptions import SignaturePayloadError


_PDF_HEADER = b"%PDF-"


class PdfValidationService:
    def __init__(
        self,
        max_pdf_size: Optional[int] = None,
        max_pdf_pages: Optional[int] = None,
        max_page_width: Optional[int] = None,
        max_page_height: Optional[int] = None,
    ) -> None:
        self.max_pdf_size = settings.FIRMADOC_MAX_PDF_SIZE if max_pdf_size is None else max_pdf_size
        self.max_pdf_pages = settings.FIRMADOC_MAX_PDF_PAGES if max_pdf_pages is None else max_pdf_pages
        self.max_page_width = settings.FIRMADOC_MAX_PAGE_WIDTH if max_page_width is None else max_page_width
        self.max_page_height = settings.FIRMADOC_MAX_PAGE_HEIGHT if max_page_height is None else max_page_height

    def validate_source_pdf(self, pdf_path: Path | str) -> PdfValidationResult:
        path = Path(pdf_path)
        if not path.exists():
            raise SignaturePayloadError("El PDF fuente no existe")
        if not path.is_file():
            raise SignaturePayloadError("El PDF fuente no es un archivo regular")
        if path.is_symlink():
            raise SignaturePayloadError("El PDF fuente no puede ser un enlace simbolico")

        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise SignaturePayloadError("El PDF fuente esta vacio")
        if size_bytes > self.max_pdf_size:
            raise SignaturePayloadError("El PDF fuente supera el tamano maximo permitido")

        data = path.read_bytes()
        if not data.startswith(_PDF_HEADER):
            raise SignaturePayloadError("El PDF fuente tiene un encabezado invalido")

        sha256 = hashlib.sha256(data).hexdigest()
        page_sizes: list[tuple[float, float]] = []
        page_count = 0

        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                if not document.is_pdf:
                    raise SignaturePayloadError("El archivo fuente no es un PDF valido")
                if document.needs_pass:
                    raise SignaturePayloadError("El PDF fuente esta cifrado y no puede procesarse")
                if document.is_repaired:
                    raise SignaturePayloadError("El PDF fuente requirio reparacion y fue rechazado")

                page_count = document.page_count
                if page_count <= 0:
                    raise SignaturePayloadError("El PDF fuente no contiene paginas")
                if page_count > self.max_pdf_pages:
                    raise SignaturePayloadError("El PDF fuente supera el numero maximo de paginas")

                for page in document:
                    rect = page.rect
                    width = float(rect.width)
                    height = float(rect.height)
                    if not math.isfinite(width) or not math.isfinite(height):
                        raise SignaturePayloadError("El PDF fuente contiene dimensiones invalidas")
                    if width <= 0 or height <= 0:
                        raise SignaturePayloadError("El PDF fuente contiene una pagina invalida")
                    if width > self.max_page_width or height > self.max_page_height:
                        raise SignaturePayloadError("El PDF fuente supera las dimensiones maximas permitidas")
                    page_sizes.append((width, height))
        except SignaturePayloadError:
            raise
        except Exception as exc:
            raise SignaturePayloadError("No fue posible validar el PDF fuente") from exc

        return PdfValidationResult(
            path=path,
            sha256=sha256,
            size_bytes=size_bytes,
            page_count=page_count,
            page_sizes=tuple(page_sizes),
        )

    def validate_pdf(self, pdf_path: Path | str) -> PdfValidationResult:
        return self.validate_source_pdf(pdf_path)


pdf_validation_service = PdfValidationService()
