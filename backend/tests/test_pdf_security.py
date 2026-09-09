from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest

from app.services.pdf_validation_service import PdfValidationService
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


def test_pdf_service_files_do_not_reference_http_clients_or_alfresco():
    repo_root = Path(__file__).resolve().parents[1]
    files = [
        repo_root / "app" / "services" / "pdf_validation_service.py",
        repo_root / "app" / "services" / "signature_image_service.py",
        repo_root / "app" / "services" / "temporary_artifact_service.py",
        repo_root / "app" / "services" / "pdf_signature_service.py",
        repo_root / "app" / "services" / "pdf_service.py",
        repo_root / "app" / "schemas" / "pdf_signature.py",
    ]
    forbidden_lower = ["alfresco", "httpx", "requests", "urllib", "aiohttp", "socket"]
    forbidden_upper = ["POST", "PUT", "DELETE"]

    for file_path in files:
        content = file_path.read_text(encoding="utf-8")
        lowered = content.lower()
        for token in forbidden_lower:
            assert token not in lowered, f"{token} found in {file_path}"
        for token in forbidden_upper:
            assert token not in content, f"{token} found in {file_path}"


def test_pdf_services_run_with_network_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path):
    _block_network(monkeypatch)

    pdf_path = tmp_path / "sample.pdf"
    png_path = tmp_path / "sample.png"

    import struct
    import zlib

    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore[no-redef]

    def chunk(chunk_type: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "Red bloqueada", fontsize=12)
    doc.set_metadata({})
    pdf_bytes = doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    pdf_path.write_bytes(pdf_bytes)
    doc.close()

    header = struct.pack(">IIBBBBB", 16, 16, 8, 6, 0, 0, 0)
    row = b"\x00" + b"\x1f\x77\xcc\xff" * 16
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * 16))
        + chunk(b"IEND", b"")
    )
    png_path.write_bytes(png_bytes)

    pdf_result = PdfValidationService().validate_source_pdf(pdf_path)
    png_result = SignatureImageService().validate_png(png_path)

    assert pdf_result.page_count == 1
    assert png_result.width == 16
    assert png_result.height == 16
