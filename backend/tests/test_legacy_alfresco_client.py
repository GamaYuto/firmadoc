from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import UUID

import certifi
import httpx
import pytest
import respx

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from app.services import alfresco_client as legacy_module
from app.services.alfresco_client import AlfrescoClient, AlfrescoInvalidContentError


ROOT_URL = "https://alfresco-lab.test"
API_URL = f"{ROOT_URL}/alfresco/api/-default-/public/alfresco/versions/1"


def _make_pdf_bytes(text: str = "Legacy Alfresco") -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=12)
    document.set_metadata({})
    data = document.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    document.close()
    return data


def _configure_legacy_settings(monkeypatch, tmp_path: Path, *, ca_bundle_path: str | None = None) -> Path:
    ca_bundle = Path(ca_bundle_path) if ca_bundle_path else (tmp_path / "legacy-ca.pem")
    if not ca_bundle_path:
        ca_bundle.write_text(Path(certifi.where()).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_BASE_URL", ROOT_URL, raising=False)
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_API_URL", "/alfresco/api/-default-/public/alfresco/versions/1", raising=False)
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_API_PATH", "/alfresco/api/-default-/public/alfresco/versions/1", raising=False)
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_USER", "legacy_user", raising=False)
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_USERNAME", "legacy_user", raising=False)
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_PASSWORD", "legacy_password", raising=False)
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_TIMEOUT_SECONDS", 5, raising=False)
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_MAX_DOWNLOAD_MB", 2, raising=False)
    monkeypatch.setattr(legacy_module.settings, "ALFRESCO_CA_BUNDLE", str(ca_bundle), raising=False)
    return ca_bundle


def test_legacy_client_uses_ca_bundle(monkeypatch, tmp_path: Path):
    ca_bundle = _configure_legacy_settings(monkeypatch, tmp_path)
    client = AlfrescoClient()

    assert client.verify == str(ca_bundle)
    assert client.verify is not False


@respx.mock
def test_legacy_download_node_content_validates_pdf_bytes(monkeypatch, tmp_path: Path):
    _configure_legacy_settings(monkeypatch, tmp_path, ca_bundle_path=os.getenv("ALFRESCO_CA_BUNDLE"))
    client = AlfrescoClient()
    node_id = UUID("12345678-1234-1234-1234-1234567890ab")
    pdf_bytes = _make_pdf_bytes()
    expected_hash = hashlib.sha256(pdf_bytes).hexdigest()

    respx.get(f"{client.base_url}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, content=pdf_bytes)
    )

    temp_path, downloaded_size, final_hash = asyncio.run(client.download_node_content(node_id))

    assert downloaded_size == len(pdf_bytes)
    assert final_hash == expected_hash
    assert Path(temp_path).exists()
    assert Path(temp_path).read_bytes() == pdf_bytes

    Path(temp_path).unlink(missing_ok=True)


@respx.mock
def test_legacy_download_node_content_rejects_bad_magic_bytes(monkeypatch, tmp_path: Path):
    _configure_legacy_settings(monkeypatch, tmp_path, ca_bundle_path=os.getenv("ALFRESCO_CA_BUNDLE"))
    client = AlfrescoClient()
    node_id = UUID("12345678-1234-1234-1234-1234567890ab")

    respx.get(f"{client.base_url}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, content=b"not-a-pdf")
    )

    with pytest.raises(AlfrescoInvalidContentError):
        asyncio.run(client.download_node_content(node_id))


@respx.mock
def test_legacy_download_node_content_rejects_corrupt_pdf_structure(monkeypatch, tmp_path: Path):
    _configure_legacy_settings(monkeypatch, tmp_path, ca_bundle_path=os.getenv("ALFRESCO_CA_BUNDLE"))
    client = AlfrescoClient()
    node_id = UUID("12345678-1234-1234-1234-1234567890ab")
    temp_path = tmp_path / "legacy-corrupt.pdf"

    def fake_mkstemp(*, suffix: str = ".pdf"):
        fd = os.open(temp_path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        return fd, str(temp_path)

    monkeypatch.setattr(legacy_module.tempfile, "mkstemp", fake_mkstemp)

    respx.get(f"{client.base_url}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4\ncorrupt-payload")
    )

    with pytest.raises(AlfrescoInvalidContentError):
        asyncio.run(client.download_node_content(node_id))

    assert not temp_path.exists()
