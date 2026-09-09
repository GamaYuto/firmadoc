from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import pymupdf as fitz

from app.core.config import settings
from app.schemas.pdf_signature import PngValidationResult
from app.services.signature_exceptions import SignaturePayloadError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class SignatureImageService:
    def __init__(
        self,
        max_png_size: Optional[int] = None,
        max_image_width: Optional[int] = None,
        max_image_height: Optional[int] = None,
    ) -> None:
        self.max_png_size = settings.FIRMADOC_MAX_PNG_SIZE if max_png_size is None else max_png_size
        self.max_image_width = settings.FIRMADOC_MAX_IMAGE_WIDTH if max_image_width is None else max_image_width
        self.max_image_height = settings.FIRMADOC_MAX_IMAGE_HEIGHT if max_image_height is None else max_image_height

    @staticmethod
    def _has_visible_content(pix: fitz.Pixmap) -> tuple[bool, bool]:
        samples = pix.samples
        components = pix.n
        if components not in (1, 2, 3, 4):
            raise SignaturePayloadError("La imagen PNG utiliza un formato no soportado")
        if len(samples) % components != 0:
            raise SignaturePayloadError("La imagen PNG no pudo decodificarse correctamente")

        has_opaque_pixel = False
        has_nonwhite_pixel = False
        for offset in range(0, len(samples), components):
            pixel = samples[offset : offset + components]
            if components == 1:
                gray = pixel[0]
                if gray < 255:
                    has_opaque_pixel = True
                    has_nonwhite_pixel = True
                    return has_opaque_pixel, has_nonwhite_pixel
                has_opaque_pixel = True
            elif components == 2:
                gray, alpha = pixel
                if alpha > 0:
                    has_opaque_pixel = True
                    if gray < 255:
                        has_nonwhite_pixel = True
                        return has_opaque_pixel, has_nonwhite_pixel
            elif components == 3:
                red, green, blue = pixel
                has_opaque_pixel = True
                if red < 255 or green < 255 or blue < 255:
                    has_nonwhite_pixel = True
                    return has_opaque_pixel, has_nonwhite_pixel
            else:
                red, green, blue, alpha = pixel
                if alpha > 0:
                    has_opaque_pixel = True
                    if red < 255 or green < 255 or blue < 255:
                        has_nonwhite_pixel = True
                        return has_opaque_pixel, has_nonwhite_pixel

        return has_opaque_pixel, has_nonwhite_pixel

    def validate_png(self, image_path: Path | str) -> PngValidationResult:
        path = Path(image_path)
        if not path.exists():
            raise SignaturePayloadError("La imagen PNG no existe")
        if not path.is_file():
            raise SignaturePayloadError("La imagen PNG no es un archivo regular")
        if path.is_symlink():
            raise SignaturePayloadError("La imagen PNG no puede ser un enlace simbolico")

        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise SignaturePayloadError("La imagen PNG esta vacia")
        if size_bytes > self.max_png_size:
            raise SignaturePayloadError("La imagen PNG supera el tamano maximo permitido")

        data = path.read_bytes()
        if not data.startswith(PNG_SIGNATURE):
            raise SignaturePayloadError("El archivo no es una imagen PNG valida")

        try:
            pix = fitz.Pixmap(str(path))
        except SignaturePayloadError:
            raise
        except Exception as exc:
            raise SignaturePayloadError("La imagen PNG no pudo decodificarse") from exc

        try:
            width = int(pix.width)
            height = int(pix.height)
            if width <= 0 or height <= 0:
                raise SignaturePayloadError("La imagen PNG tiene dimensiones invalidas")
            if width > self.max_image_width or height > self.max_image_height:
                raise SignaturePayloadError("La imagen PNG supera las dimensiones maximas permitidas")

            has_opaque_pixel, has_nonwhite_pixel = self._has_visible_content(pix)
            if not has_opaque_pixel:
                raise SignaturePayloadError("La imagen PNG es totalmente transparente")
            if not has_nonwhite_pixel:
                raise SignaturePayloadError("La imagen PNG no contiene contenido visible")
        finally:
            pix = None

        sha256 = hashlib.sha256(data).hexdigest()
        return PngValidationResult(
            path=path,
            sha256=sha256,
            size_bytes=size_bytes,
            width=width,
            height=height,
        )

    def load_png(self, image_path: Path | str) -> PngValidationResult:
        return self.validate_png(image_path)


signature_image_service = SignatureImageService()
