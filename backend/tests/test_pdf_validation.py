from __future__ import annotations

import hashlib
import socket
import struct
import zlib
from pathlib import Path

import pytest

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from app.services.pdf_validation_service import PdfValidationService
from app.services.signature_exceptions import SignaturePayloadError
from app.services.signature_image_service import SignatureImageService


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args, **kwargs):  # noqa: ANN001, ANN002
        raise AssertionError("network blocked")

    monkeypatch.setattr(socket.socket, "connect", blocked, raising=True)
    monkeypatch.setattr(socket, "create_connection", blocked, raising=True)

    try:
        import httpx
    except ImportError:  # pragma: no cover
        httpx = None
    if httpx is not None:
        monkeypatch.setattr(httpx.Client, "request", blocked, raising=True)
        monkeypatch.setattr(httpx.AsyncClient, "request", blocked, raising=True)

    try:
        import requests
    except ImportError:  # pragma: no cover
        requests = None
    if requests is not None:
        monkeypatch.setattr(requests.sessions.Session, "request", blocked, raising=True)


@pytest.fixture(autouse=True)
def _pdf_network_guard(monkeypatch: pytest.MonkeyPatch):
    _block_network(monkeypatch)


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def _png_bytes(width: int, height: int, pixel: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + pixel * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _write_pdf(
    path: Path,
    *,
    page_count: int = 1,
    width: int = 595,
    height: int = 842,
    rotation: int = 0,
    encrypted: bool = False,
    owner_pw: str = "owner",
    user_pw: str = "user",
) -> bytes:
    doc = fitz.open()
    for index in range(page_count):
        page = doc.new_page(width=width, height=height)
        page.insert_text((72, 72), f"Documento de validacion {index + 1}", fontsize=14)
        if rotation:
            page.set_rotation(rotation)
    doc.set_metadata({})

    if encrypted:
        doc.save(
            path,
            garbage=3,
            deflate=True,
            use_objstms=1,
            no_new_id=True,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw=owner_pw,
            user_pw=user_pw,
        )
        pdf_bytes = path.read_bytes()
    else:
        pdf_bytes = doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
        path.write_bytes(pdf_bytes)
    doc.close()
    return pdf_bytes


def test_validate_source_pdf_accepts_real_pdf_and_reports_hash(tmp_path):
    source_path = tmp_path / "source.pdf"
    pdf_bytes = _write_pdf(source_path, rotation=90)

    result = PdfValidationService().validate_source_pdf(source_path)

    assert result.path == source_path
    assert result.size_bytes == len(pdf_bytes)
    assert result.sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert result.page_count == 1
    assert len(result.page_sizes) == 1
    assert result.page_sizes[0][0] > result.page_sizes[0][1]


def test_validate_source_pdf_rejects_empty_file(tmp_path):
    source_path = tmp_path / "empty.pdf"
    source_path.write_bytes(b"")

    with pytest.raises(SignaturePayloadError):
        PdfValidationService().validate_source_pdf(source_path)


def test_validate_source_pdf_rejects_invalid_header(tmp_path):
    source_path = tmp_path / "broken.pdf"
    source_path.write_bytes(b"not a pdf")

    with pytest.raises(SignaturePayloadError):
        PdfValidationService().validate_source_pdf(source_path)

    source_path.unlink()


def test_validate_source_pdf_rejects_corrupt_pdf(tmp_path):
    source_path = tmp_path / "corrupt.pdf"
    source_path.write_bytes(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n")

    with pytest.raises(SignaturePayloadError):
        PdfValidationService().validate_source_pdf(source_path)


def test_validate_source_pdf_rejects_encrypted_pdf(tmp_path):
    source_path = tmp_path / "encrypted.pdf"
    _write_pdf(source_path, encrypted=True)

    with pytest.raises(SignaturePayloadError):
        PdfValidationService().validate_source_pdf(source_path)


def test_validate_source_pdf_rejects_size_exceeded(tmp_path):
    source_path = tmp_path / "large.pdf"
    pdf_bytes = _write_pdf(source_path)

    validator = PdfValidationService(max_pdf_size=len(pdf_bytes) - 1)

    with pytest.raises(SignaturePayloadError):
        validator.validate_source_pdf(source_path)


def test_validate_source_pdf_rejects_page_count_exceeded(tmp_path):
    source_path = tmp_path / "many-pages.pdf"
    _write_pdf(source_path, page_count=2)

    validator = PdfValidationService(max_pdf_pages=1)

    with pytest.raises(SignaturePayloadError):
        validator.validate_source_pdf(source_path)


def test_validate_source_pdf_rejects_dimensions_exceeded(tmp_path):
    source_path = tmp_path / "wide.pdf"
    _write_pdf(source_path, width=6000, height=842)

    validator = PdfValidationService(max_page_width=5000, max_page_height=5000)

    with pytest.raises(SignaturePayloadError):
        validator.validate_source_pdf(source_path)


def test_validate_source_pdf_rejects_symlink(tmp_path):
    target_path = tmp_path / "target.pdf"
    _write_pdf(target_path)
    link_path = tmp_path / "link.pdf"
    link_path.symlink_to(target_path)

    with pytest.raises(SignaturePayloadError):
        PdfValidationService().validate_source_pdf(link_path)


def test_validate_source_pdf_closes_handles_on_error(tmp_path):
    source_path = tmp_path / "corrupt-locked.pdf"
    source_path.write_bytes(b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n")

    with pytest.raises(SignaturePayloadError):
        PdfValidationService().validate_source_pdf(source_path)

    source_path.unlink()
    assert not source_path.exists()


def test_validate_png_accepts_real_png_and_reports_dimensions(tmp_path):
    image_path = tmp_path / "signature.png"
    png_bytes = _png_bytes(48, 24, b"\x1f\x77\xcc\xff")
    image_path.write_bytes(png_bytes)

    result = SignatureImageService().validate_png(image_path)

    assert result.path == image_path
    assert result.size_bytes == len(png_bytes)
    assert result.sha256 == hashlib.sha256(png_bytes).hexdigest()
    assert result.width == 48
    assert result.height == 24


def test_validate_png_rejects_jpeg_renamed(tmp_path):
    image_path = tmp_path / "fake.png"
    image_path.write_bytes(b"\xff\xd8\xff\xe0JPEG")

    with pytest.raises(SignaturePayloadError):
        SignatureImageService().validate_png(image_path)


def test_validate_png_rejects_corrupt_png(tmp_path):
    image_path = tmp_path / "corrupt.png"
    image_path.write_bytes(b"not a png")

    with pytest.raises(SignaturePayloadError):
        SignatureImageService().validate_png(image_path)


def test_validate_png_rejects_white_png(tmp_path):
    image_path = tmp_path / "white.png"
    image_path.write_bytes(_png_bytes(16, 16, b"\xff\xff\xff\xff"))

    with pytest.raises(SignaturePayloadError):
        SignatureImageService().validate_png(image_path)


def test_validate_png_rejects_transparent_png(tmp_path):
    image_path = tmp_path / "transparent.png"
    image_path.write_bytes(_png_bytes(16, 16, b"\x00\x00\x00\x00"))

    with pytest.raises(SignaturePayloadError):
        SignatureImageService().validate_png(image_path)


def test_validate_png_rejects_size_exceeded(tmp_path):
    image_path = tmp_path / "large.png"
    png_bytes = _png_bytes(32, 32, b"\x1f\x77\xcc\xff")
    image_path.write_bytes(png_bytes)

    validator = SignatureImageService(max_png_size=len(png_bytes) - 1)

    with pytest.raises(SignaturePayloadError):
        validator.validate_png(image_path)


def test_validate_png_rejects_dimensions_exceeded(tmp_path):
    image_path = tmp_path / "wide.png"
    image_path.write_bytes(_png_bytes(80, 40, b"\x1f\x77\xcc\xff"))

    validator = SignatureImageService(max_image_width=64, max_image_height=64)

    with pytest.raises(SignaturePayloadError):
        validator.validate_png(image_path)
