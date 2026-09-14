from __future__ import annotations

import logging
import os
import hashlib
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest
import respx

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from app.schemas.alfresco import AlfrescoNodeSnapshot
from app.services.alfresco_service import AlfrescoLabClient
from app.services.signature_exceptions import (
    SignaturePayloadError,
    SignatureRecoveryRequiredError,
    SignatureUploadError,
    SignatureVersionConflictError,
)
from app.services.temporary_artifact_service import TemporaryArtifactService


ROOT_URL = "https://alfresco-lab.test"
API_PATH = "/alfresco/api/-default-/public/alfresco/versions/1"
API_URL = f"{ROOT_URL}{API_PATH}"
LOGGER = logging.getLogger(__name__)
LAB_ENV_VARS = (
    "ALFRESCO_BASE_URL",
    "ALFRESCO_API_PATH",
    "ALFRESCO_USERNAME",
    "ALFRESCO_PASSWORD",
    "ALFRESCO_CA_BUNDLE",
    "FIRMADOC_ALFRESCO_EXPECTED_HOST",
    "FIRMADOC_ALFRESCO_TEST_NODE_ID",
    "FIRMADOC_ALFRESCO_TEST_EXPECTED_NAME",
    "FIRMADOC_ALFRESCO_TEST_EXPECTED_PATH",
    "FIRMADOC_ALFRESCO_TEST_EXPECTED_MIMETYPE",
    "FIRMADOC_ALFRESCO_WRITE_ENABLED",
)


@dataclass(frozen=True, slots=True)
class LabConfig:
    base_url: str
    api_path: str
    username: str
    password: str
    ca_bundle: Path
    expected_host: str
    node_id: str
    expected_name: str
    expected_path: str
    expected_mimetype: str
    write_enabled: bool
    connect_timeout: float
    read_timeout: float
    write_timeout: float
    max_download_size: int
    max_history_pages: int
    history_page_size: int


def _parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int) -> int:
    text = (value or "").strip()
    return int(text) if text else default


def _mask_node_id(node_id: str) -> str:
    if len(node_id) <= 8:
        return f"{node_id[:2]}...{node_id[-2:]}"
    return f"{node_id[:6]}...{node_id[-4:]}"


def _read_lab_config(*, allow_write_enabled: bool = False) -> LabConfig | None:
    values = {name: os.getenv(name) for name in LAB_ENV_VARS}
    if not any(values.values()):
        return None

    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.fail(f"Configuracion de laboratorio incompleta: {', '.join(missing)}")

    base_url = values["ALFRESCO_BASE_URL"].strip()
    api_path = values["ALFRESCO_API_PATH"].strip()
    username = values["ALFRESCO_USERNAME"].strip()
    password = values["ALFRESCO_PASSWORD"].strip()
    expected_host = values["FIRMADOC_ALFRESCO_EXPECTED_HOST"].strip()
    node_id = values["FIRMADOC_ALFRESCO_TEST_NODE_ID"].strip()
    expected_name = values["FIRMADOC_ALFRESCO_TEST_EXPECTED_NAME"].strip()
    expected_path = values["FIRMADOC_ALFRESCO_TEST_EXPECTED_PATH"].strip()
    expected_mimetype = values["FIRMADOC_ALFRESCO_TEST_EXPECTED_MIMETYPE"].strip()
    write_enabled = _parse_bool(values["FIRMADOC_ALFRESCO_WRITE_ENABLED"])

    if not allow_write_enabled and write_enabled:
        pytest.fail("FIRMADOC_ALFRESCO_WRITE_ENABLED debe permanecer en false durante esta validacion")

    parsed_base_url = urlparse(base_url)
    if parsed_base_url.scheme.lower() != "https":
        pytest.fail("ALFRESCO_BASE_URL debe usar HTTPS")
    if parsed_base_url.hostname != expected_host:
        pytest.fail("ALFRESCO_BASE_URL no coincide con el host autorizado")
    if expected_host != "alfresco-lab.test":
        pytest.fail("FIRMADOC_ALFRESCO_EXPECTED_HOST debe ser alfresco-lab.test")
    if expected_mimetype.lower() != "application/pdf":
        pytest.fail("FIRMADOC_ALFRESCO_TEST_EXPECTED_MIMETYPE debe ser application/pdf")

    ca_bundle = Path(values["ALFRESCO_CA_BUNDLE"].strip())
    if not ca_bundle.exists():
        pytest.fail("ALFRESCO_CA_BUNDLE no existe")

    return LabConfig(
        base_url=base_url,
        api_path=api_path,
        username=username,
        password=password,
        ca_bundle=ca_bundle,
        expected_host=expected_host,
        node_id=node_id,
        expected_name=expected_name,
        expected_path=expected_path,
        expected_mimetype=expected_mimetype,
        write_enabled=write_enabled,
        connect_timeout=float(_parse_int(os.getenv("ALFRESCO_CONNECT_TIMEOUT"), 5)),
        read_timeout=float(_parse_int(os.getenv("ALFRESCO_READ_TIMEOUT"), 30)),
        write_timeout=float(_parse_int(os.getenv("ALFRESCO_WRITE_TIMEOUT"), 60)),
        max_download_size=_parse_int(os.getenv("ALFRESCO_MAX_DOWNLOAD_SIZE"), 52_428_800),
        max_history_pages=_parse_int(os.getenv("ALFRESCO_MAX_HISTORY_PAGES"), 100),
        history_page_size=_parse_int(os.getenv("ALFRESCO_HISTORY_PAGE_SIZE"), 100),
    )


def _build_lab_client(config: LabConfig) -> AlfrescoLabClient:
    return AlfrescoLabClient(
        base_url=config.base_url,
        api_path=config.api_path,
        username=config.username,
        password=config.password,
        ca_bundle=config.ca_bundle,
        expected_host=config.expected_host,
        connect_timeout=config.connect_timeout,
        read_timeout=config.read_timeout,
        write_timeout=config.write_timeout,
        max_download_size=config.max_download_size,
        max_history_pages=config.max_history_pages,
        history_page_size=config.history_page_size,
    )


def _log_step(step: str, method: str, path_label: str, status_code: int, duration: float) -> None:
    LOGGER.info(
        "alfresco_lab_read step=%s method=%s path=%s status=%s duration=%.3fs",
        step,
        method.upper(),
        path_label,
        status_code,
        duration,
    )


def _log_failure(step: str, method: str, path_label: str, exc: Exception, duration: float) -> None:
    LOGGER.info(
        "alfresco_lab_read step=%s method=%s path=%s exception=%s duration=%.3fs",
        step,
        method.upper(),
        path_label,
        f"{exc.__class__.__name__}: {exc}",
        duration,
    )


def _timed_request_json(
    client: AlfrescoLabClient,
    *,
    step: str,
    method: str,
    url: str,
    path_label: str,
    params: dict[str, str] | None = None,
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        payload, response = client._request_json(method, url, params=params, not_found_conflict=False)
    except Exception as exc:
        _log_failure(step, method, path_label, exc, time.perf_counter() - started)
        raise
    _log_step(step, method, path_label, response.status_code, time.perf_counter() - started)
    return payload


def _extract_group_ids(payload: dict[str, object]) -> list[str]:
    list_payload = payload.get("list")
    if not isinstance(list_payload, dict):
        return []
    entries = list_payload.get("entries")
    if not isinstance(entries, list):
        return []
    group_ids: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        entry = item.get("entry")
        if not isinstance(entry, dict):
            continue
        group_id = entry.get("id")
        if isinstance(group_id, str) and group_id.strip():
            group_ids.append(group_id.strip())
    return group_ids


def _make_pdf_bytes(text: str = "FirmaDoc") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=12)
    doc.set_metadata({})
    data = doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    doc.close()
    return data


def _assert_valid_pdf_artifact(path: Path, *, expected_sha256: str | None = None) -> None:
    if not path.exists():
        raise AssertionError("El archivo PDF esperado no existe")
    data = path.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise AssertionError("El archivo PDF no inicia con %PDF-")
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise AssertionError("El SHA-256 del PDF no coincide con el esperado")
    with fitz.open(path) as document:
        if not document.is_pdf:
            raise AssertionError("El archivo no abre como PDF valido")
        if document.needs_pass:
            raise AssertionError("El PDF no debe estar cifrado")
        if document.page_count <= 0:
            raise AssertionError("El PDF no contiene paginas")


def _lab_configured() -> bool:
    return all(
        os.getenv(name)
        for name in (
            "ALFRESCO_USERNAME",
            "ALFRESCO_PASSWORD",
            "ALFRESCO_CA_BUNDLE",
            "FIRMADOC_ALFRESCO_TEST_NODE_ID",
        )
    )


@pytest.fixture
def alfresco_client() -> AlfrescoLabClient:
    return AlfrescoLabClient(
        base_url=ROOT_URL,
        api_path=API_PATH,
        username="lab_user",
        password="lab_password",
        expected_host="alfresco-lab.test",
        connect_timeout=1,
        read_timeout=1,
        write_timeout=1,
        max_download_size=2 * 1024 * 1024,
        max_history_pages=10,
        history_page_size=5,
    )


def test_client_rejects_http_base_url():
    with pytest.raises(SignatureUploadError):
        AlfrescoLabClient(
            base_url="http://alfresco-lab.test",
            api_path=API_PATH,
            username="lab_user",
            password="lab_password",
            expected_host="alfresco-lab.test",
        )


def test_client_rejects_foreign_host():
    with pytest.raises(SignatureUploadError):
        AlfrescoLabClient(
            base_url="https://evil.example.com",
            api_path=API_PATH,
            username="lab_user",
            password="lab_password",
            expected_host="alfresco-lab.test",
        )


@respx.mock
def test_get_repository_info_success(alfresco_client: AlfrescoLabClient):
    respx.get(f"{ROOT_URL}/alfresco/api/discovery").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "repository": {
                        "id": "repo-1",
                        "edition": "Community",
                        "version": {
                            "major": 26,
                            "minor": 1,
                            "patch": 0,
                            "hotfix": 0,
                            "schema": 8,
                            "label": "26.1.0",
                            "display": "26.1.0",
                        },
                    },
                    "status": {"isReadOnly": False},
                }
            },
        )
    )

    snapshot = alfresco_client.get_repository_info()

    assert snapshot.repository_id == "repo-1"
    assert snapshot.edition == "Community"
    assert snapshot.version_major == 26
    assert snapshot.version_minor == 1
    assert snapshot.version_label == "26.1.0"
    assert snapshot.is_read_only is False


@respx.mock
def test_get_node_success(alfresco_client: AlfrescoLabClient):
    node_id = "node-1"
    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            headers={"etag": '"etag-node-1"'},
            json={
                "entry": {
                    "id": node_id,
                    "name": "documento.pdf",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "modifiedAt": "2026-08-03T12:34:56Z",
                    "content": {"mimeType": "application/pdf", "sizeInBytes": 1234},
                    "properties": {"cm:versionLabel": "1.0"},
                    "path": {
                        "name": "/Company Home/documento.pdf",
                        "elements": [{"name": "Company Home"}, {"name": "documento.pdf"}],
                    },
                }
            },
        )
    )

    snapshot = alfresco_client.get_node(node_id)

    assert snapshot.node_id == node_id
    assert snapshot.name == "documento.pdf"
    assert snapshot.node_type == "cm:content"
    assert snapshot.mime_type == "application/pdf"
    assert snapshot.size_bytes == 1234
    assert snapshot.version_label == "1.0"
    assert snapshot.etag == '"etag-node-1"'
    assert snapshot.path == "/Company Home/documento.pdf"
    assert snapshot.modified_at == datetime(2026, 8, 3, 12, 34, 56, tzinfo=timezone.utc)


@respx.mock
def test_get_node_follows_same_host_redirect(alfresco_client: AlfrescoLabClient):
    node_id = "node-redirect"
    redirected_url = f"{API_URL}/nodes/{node_id}/resolved"
    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(302, headers={"location": redirected_url})
    )
    respx.get(redirected_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "documento.pdf",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "application/pdf", "sizeInBytes": 1},
                }
            },
        )
    )

    snapshot = alfresco_client.get_node(node_id)

    assert snapshot.node_id == node_id


@respx.mock
def test_get_node_rejects_foreign_redirect(alfresco_client: AlfrescoLabClient):
    node_id = "node-redirect-foreign"
    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example.com/other"})
    )

    with pytest.raises(SignatureUploadError):
        alfresco_client.get_node(node_id)


@respx.mock
def test_download_current_content_success(alfresco_client: AlfrescoLabClient, tmp_path: Path):
    node_id = "node-download"
    pdf_bytes = _make_pdf_bytes("Current")
    pdf_path = tmp_path / "downloaded.pdf"

    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "documento.pdf",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "application/pdf", "sizeInBytes": len(pdf_bytes)},
                    "properties": {"cm:versionLabel": "1.0"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=pdf_bytes))

    artifact = alfresco_client.download_current_content(node_id, pdf_path)

    assert artifact.node_id == node_id
    assert artifact.version_id == "1.0"
    assert artifact.path == pdf_path
    assert artifact.size_bytes == len(pdf_bytes)
    assert artifact.sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert artifact.path.exists()
    assert artifact.path.read_bytes() == pdf_bytes


@respx.mock
def test_download_current_content_captures_exact_etag_with_quotes(alfresco_client: AlfrescoLabClient, tmp_path: Path):
    node_id = "node-etag-test"
    pdf_bytes = _make_pdf_bytes("Current ETag")
    pdf_path = tmp_path / "downloaded_etag.pdf"
    expected_etag = '"1786048128957"'

    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "documento.pdf",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "application/pdf", "sizeInBytes": len(pdf_bytes)},
                    "properties": {"cm:versionLabel": "1.0"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"ETag": expected_etag}, content=pdf_bytes)
    )

    artifact = alfresco_client.download_current_content(node_id, pdf_path)

    assert artifact.etag == expected_etag


@respx.mock
def test_download_current_content_rejects_bad_mime(alfresco_client: AlfrescoLabClient, tmp_path: Path):
    node_id = "node-bad-mime"
    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "image.png",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "image/png", "sizeInBytes": 10},
                }
            },
        )
    )

    with pytest.raises(SignaturePayloadError):
        alfresco_client.download_current_content(node_id, tmp_path / "bad.pdf")


@respx.mock
def test_list_versions_success(alfresco_client: AlfrescoLabClient):
    node_id = "node-versions"
    respx.get(f"{API_URL}/nodes/{node_id}/versions").mock(
        return_value=httpx.Response(
            200,
            json={
                "list": {
                    "entries": [
                        {"entry": {"id": "1.1", "versionComment": "FirmaDoc:1:1:ope-1", "createdAt": "2026-08-03T12:00:00Z"}},
                        {"entry": {"id": "1.0", "versionComment": "base", "createdAt": "2026-08-03T11:00:00Z"}},
                    ]
                }
            },
        )
    )

    versions = alfresco_client.list_versions(node_id, skip_count=0, max_items=10)

    assert [version.version_id for version in versions] == ["1.1", "1.0"]
    assert versions[0].comment == "FirmaDoc:1:1:ope-1"


@respx.mock
def test_get_version_success(alfresco_client: AlfrescoLabClient):
    node_id = "node-version"
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": "1.1",
                    "nodeId": node_id,
                    "versionComment": "FirmaDoc:1:1:ope-1",
                    "createdAt": "2026-08-03T12:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )

    version = alfresco_client.get_version(node_id, "1.1")

    assert version.version_id == "1.1"
    assert version.node_id == node_id
    assert version.comment == "FirmaDoc:1:1:ope-1"
    assert version.modifier == "lab_user"


@respx.mock
def test_update_content_as_new_version_success(alfresco_client: AlfrescoLabClient, tmp_path: Path):
    node_id = "node-upload"
    pdf_bytes = _make_pdf_bytes("Upload")
    source_path = tmp_path / "upload.pdf"
    source_path.write_bytes(pdf_bytes)
    comment = "FirmaDoc:1:1:ope-1"

    def _response(request: httpx.Request) -> httpx.Response:
        assert request.url.params["majorVersion"] == "false"
        assert request.url.params["comment"] == comment
        return httpx.Response(
            200,
            headers={"etag": '"etag-upload"'},
            json={
                "entry": {
                    "id": node_id,
                    "properties": {"cm:versionLabel": "1.1"},
                    "versionComment": comment,
                }
            },
        )

    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(side_effect=_response)

    result = alfresco_client.update_content_as_new_version(
        node_id=node_id,
        source_path=source_path,
        major_version=False,
        comment=comment,
    )

    assert result.node_id == node_id
    assert result.version_id == "1.1"
    assert result.status_code == 200
    assert result.etag == '"etag-upload"'
    assert result.remote_message == comment


@respx.mock
def test_update_content_as_new_version_with_precondition_header(alfresco_client: AlfrescoLabClient, tmp_path: Path):
    node_id = "node-upload-precondition"
    pdf_bytes = _make_pdf_bytes("Upload Precondition")
    source_path = tmp_path / "upload_precondition.pdf"
    source_path.write_bytes(pdf_bytes)
    comment = "FirmaDoc:1:1:ope-1"
    expected_etag = '"1786048128957"'

    def _response(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-Match"] == expected_etag
        assert request.url.params["majorVersion"] == "false"
        assert request.url.params["comment"] == comment
        return httpx.Response(
            200,
            headers={"etag": '"etag-new"'},
            json={
                "entry": {
                    "id": node_id,
                    "properties": {"cm:versionLabel": "1.1"},
                    "versionComment": comment,
                }
            },
        )

    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(side_effect=_response)

    result = alfresco_client.update_content_as_new_version(
        node_id=node_id,
        source_path=source_path,
        major_version=False,
        comment=comment,
        precondition=expected_etag,
    )

    assert result.status_code == 200
    assert result.etag == '"etag-new"'


@respx.mock
def test_update_content_as_new_version_precondition_failed_412(alfresco_client: AlfrescoLabClient, tmp_path: Path):
    node_id = "node-conflict-412"
    source_path = tmp_path / "conflict_412.pdf"
    source_path.write_bytes(_make_pdf_bytes("Conflict 412"))
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(412))

    with pytest.raises(SignatureVersionConflictError):
        alfresco_client.update_content_as_new_version(
            node_id=node_id,
            source_path=source_path,
            major_version=False,
            comment="FirmaDoc:1:1:ope-1",
            precondition='"1786048128957"',
        )


@respx.mock
def test_update_content_as_new_version_conflict_409(alfresco_client: AlfrescoLabClient, tmp_path: Path):
    node_id = "node-conflict"
    source_path = tmp_path / "conflict.pdf"
    source_path.write_bytes(_make_pdf_bytes("Conflict"))
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(409))

    with pytest.raises(SignatureVersionConflictError):
        alfresco_client.update_content_as_new_version(node_id, source_path, False, "FirmaDoc:1:1:ope-1")


@respx.mock
def test_update_content_as_new_version_timeout_recovery(alfresco_client: AlfrescoLabClient, tmp_path: Path):
    node_id = "node-timeout"
    source_path = tmp_path / "timeout.pdf"
    source_path.write_bytes(_make_pdf_bytes("Timeout"))
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(SignatureRecoveryRequiredError):
        alfresco_client.update_content_as_new_version(node_id, source_path, False, "FirmaDoc:1:1:ope-1")


@pytest.mark.alfresco_lab_read
def test_alfresco_lab_read_repository_info(caplog, tmp_path: Path):
    config = _read_lab_config()
    if config is None:
        pytest.skip("No real Alfresco lab configuration available in this environment")

    caplog.set_level(logging.INFO, logger=__name__)
    client = _build_lab_client(config)
    try:
        repo_started = time.perf_counter()
        repo = client.get_repository_info()
        _log_step("repository_info", "GET", "/alfresco/api/discovery", 200, time.perf_counter() - repo_started)

        if not repo.repository_id:
            raise AssertionError("El repositorio de Alfresco no devolvio un identificador valido")
        if not repo.version_label:
            raise AssertionError("El repositorio de Alfresco no devolvio una version valida")
        if repo.edition.lower() != "community":
            raise AssertionError(f"Edicion inesperada de Alfresco: {repo.edition}")
        if repo.is_read_only:
            raise AssertionError("El repositorio de laboratorio no debe estar en modo solo lectura")

        me_payload = _timed_request_json(
            client,
            step="current_user",
            method="GET",
            url=f"{client.api_url}/people/-me-",
            path_label="/people/-me-",
        )
        me_entry = me_payload.get("entry")
        if not isinstance(me_entry, dict):
            raise AssertionError("La respuesta de /people/-me- no contiene entry")
        me_id = me_entry.get("id")
        if not isinstance(me_id, str) or not me_id.strip():
            raise AssertionError("La identidad del usuario tecnico es invalida")
        if me_id != config.username:
            raise AssertionError(f"El usuario tecnico autenticado no coincide con la configuracion: {_mask_node_id(me_id)}")
        display_name = me_entry.get("displayName")
        if not isinstance(display_name, str) or not display_name.strip():
            raise AssertionError("La identidad del usuario tecnico no expone displayName")
        if not me_entry.get("enabled"):
            raise AssertionError("El usuario tecnico de laboratorio esta deshabilitado")

        groups_payload = _timed_request_json(
            client,
            step="current_user_groups",
            method="GET",
            url=f"{client.api_url}/people/-me-/groups",
            path_label="/people/-me-/groups",
        )
        group_ids = _extract_group_ids(groups_payload)
        if not group_ids:
            raise AssertionError("La consulta de grupos del usuario tecnico no devolvio miembros")
        if any("ALFRESCO_ADMINISTRATORS" in group_id for group_id in group_ids):
            raise AssertionError("El usuario tecnico pertenece a ALFRESCO_ADMINISTRATORS")

        node_started = time.perf_counter()
        node = client.get_node(config.node_id)
        _log_step("node_metadata", "GET", f"/nodes/{_mask_node_id(config.node_id)}", 200, time.perf_counter() - node_started)
        if node.node_id != config.node_id:
            raise AssertionError(f"El nodeId consultado no coincide con el configurado: {_mask_node_id(node.node_id)}")
        if not node.is_file:
            raise AssertionError("El nodo de prueba no es un archivo")
        if node.name != config.expected_name:
            raise AssertionError("El nombre del nodo de prueba no coincide con la configuracion")
        if node.mime_type != config.expected_mimetype:
            raise AssertionError("El MIME del nodo de prueba no es application/pdf")
        if node.size_bytes <= 0:
            raise AssertionError("El nodo de prueba no tiene tamano valido")
        if node.version_label is None:
            raise AssertionError("El nodo de prueba no expone la version actual")

        path_payload = _timed_request_json(
            client,
            step="node_path",
            method="GET",
            url=f"{client.api_url}/nodes/{config.node_id}",
            path_label=f"/nodes/{_mask_node_id(config.node_id)}?include=path",
            params={"include": "path"},
        )
        path_entry = path_payload.get("entry")
        if not isinstance(path_entry, dict):
            raise AssertionError("La respuesta del nodo no contiene entry")
        path_value = path_entry.get("path")
        if not isinstance(path_value, dict):
            raise AssertionError("La respuesta del nodo no contiene path")
        actual_path = path_value.get("name")
        if actual_path != config.expected_path:
            raise AssertionError("La ruta del nodo de prueba no coincide con la configuracion")

        current_path = tmp_path / "alfresco-lab-current.pdf"
        current_started = time.perf_counter()
        current = client.download_current_content(config.node_id, current_path)
        _log_step("download_current", "GET", f"/nodes/{_mask_node_id(config.node_id)}/content", 200, time.perf_counter() - current_started)
        if current.node_id != config.node_id:
            raise AssertionError("La descarga actual no retorno el nodeId esperado")
        if current.version_id != node.version_label:
            raise AssertionError("La version descargada no coincide con la version actual del nodo")
        if current.size_bytes != node.size_bytes:
            raise AssertionError("El tamano descargado no coincide con el tamano del nodo")
        if len(current.sha256) != 64 or any(ch not in "0123456789abcdef" for ch in current.sha256):
            raise AssertionError("El SHA-256 descargado no tiene formato hexadecimal de 64 caracteres")
        if not current.path.exists():
            raise AssertionError("El PDF descargado no existe en disco")
        current_bytes = current.path.read_bytes()
        if not current_bytes.startswith(b"%PDF-"):
            raise AssertionError("El PDF descargado no inicia con %PDF-")
        if hashlib.sha256(current_bytes).hexdigest() != current.sha256:
            raise AssertionError("El SHA-256 calculado sobre la descarga actual no coincide")
        with fitz.open(current.path) as document:
            if not document.is_pdf:
                raise AssertionError("El PDF descargado no abre como PDF valido")
            if document.needs_pass:
                raise AssertionError("El PDF descargado no debe estar cifrado")
            if document.page_count <= 0:
                raise AssertionError("El PDF descargado no tiene paginas")

        versions_started = time.perf_counter()
        versions = client.list_versions(config.node_id, skip_count=0, max_items=max(10, config.history_page_size))
        _log_step("version_history", "GET", f"/nodes/{_mask_node_id(config.node_id)}/versions", 200, time.perf_counter() - versions_started)
        if not versions:
            raise AssertionError("El historial de versiones del nodo de prueba esta vacio")
        current_version = next((version for version in versions if version.version_id == current.version_id), None)
        if current_version is None:
            raise AssertionError("No se encontro la version actual en el historial del nodo")

        version_path = tmp_path / "alfresco-lab-version.pdf"
        version_started = time.perf_counter()
        version_artifact = client.download_version_content(config.node_id, current_version.version_id, version_path)
        _log_step("download_version", "GET", f"/nodes/{_mask_node_id(config.node_id)}/versions/{current_version.version_id}/content", 200, time.perf_counter() - version_started)
        if version_artifact.version_id != current_version.version_id:
            raise AssertionError("La descarga de la version especifica no devolvio la version solicitada")
        if version_artifact.sha256 != current.sha256:
            raise AssertionError("El hash de la version especifica no coincide con el hash actual")
        if version_artifact.size_bytes != current.size_bytes:
            raise AssertionError("El tamano de la version especifica no coincide con la descarga actual")
        if hashlib.sha256(version_artifact.path.read_bytes()).hexdigest() != version_artifact.sha256:
            raise AssertionError("El SHA-256 calculado sobre la version especifica no coincide")

        total_elapsed = time.perf_counter() - repo_started
        if total_elapsed > 60:
            raise AssertionError(f"La validacion real excedio el timeout total controlado: {total_elapsed:.3f}s")
    finally:
        client.close()

    assert "Authorization" not in caplog.text
    assert config.password not in caplog.text
    assert _mask_node_id(config.node_id) in caplog.text
    assert "alfresco_lab_read" in caplog.text


@pytest.mark.alfresco_lab_write
def test_alfresco_lab_write_publication(tmp_path: Path):
    config = _read_lab_config(allow_write_enabled=True)
    if config is None:
        pytest.skip("No real Alfresco lab configuration available in this environment")

    if not config.write_enabled:
        pytest.skip("FIRMADOC_ALFRESCO_WRITE_ENABLED sigue en false; la escritura real permanece bloqueada")

    client = _build_lab_client(config)
    source_path = tmp_path / "alfresco-lab-write-source.pdf"
    current_path = tmp_path / "alfresco-lab-write-current.pdf"
    version_path = tmp_path / "alfresco-lab-write-version.pdf"
    current_after_path = tmp_path / "alfresco-lab-write-current-after.pdf"
    try:
        repo = client.get_repository_info()
        if repo.is_read_only:
            raise AssertionError("El repositorio de laboratorio no debe estar en modo solo lectura")

        me_payload = _timed_request_json(
            client,
            step="write_current_user",
            method="GET",
            url=f"{client.api_url}/people/-me-",
            path_label="/people/-me-",
        )
        me_entry = me_payload.get("entry")
        if not isinstance(me_entry, dict) or not me_entry.get("enabled"):
            raise AssertionError("El usuario tecnico de laboratorio no esta habilitado")
        me_id = me_entry.get("id")
        if not isinstance(me_id, str) or me_id.strip() != config.username:
            raise AssertionError("El usuario tecnico autenticado no coincide con la configuracion")
        groups_payload = _timed_request_json(
            client,
            step="write_current_user_groups",
            method="GET",
            url=f"{client.api_url}/people/-me-/groups",
            path_label="/people/-me-/groups",
        )
        if any("ALFRESCO_ADMINISTRATORS" in group_id for group_id in _extract_group_ids(groups_payload)):
            raise AssertionError("El usuario tecnico pertenece a ALFRESCO_ADMINISTRATORS")

        node = client.get_node(config.node_id)
        if node.node_id != config.node_id:
            raise AssertionError("El nodeId de prueba no coincide")
        if not node.is_file:
            raise AssertionError("El nodo de prueba no es un archivo")
        if node.name != config.expected_name:
            raise AssertionError("El nombre del nodo de prueba no coincide con la configuracion")
        if node.mime_type != config.expected_mimetype:
            raise AssertionError("El MIME del nodo de prueba no es application/pdf")
        if node.size_bytes <= 0:
            raise AssertionError("El nodo de prueba no tiene tamano valido")
        if node.version_label is None:
            raise AssertionError("El nodo de prueba no expone la version actual")

        path_payload = _timed_request_json(
            client,
            step="write_node_path",
            method="GET",
            url=f"{client.api_url}/nodes/{config.node_id}",
            path_label=f"/nodes/{_mask_node_id(config.node_id)}?include=path",
            params={"include": "path"},
        )
        path_entry = path_payload.get("entry")
        if not isinstance(path_entry, dict):
            raise AssertionError("La respuesta del nodo no contiene entry")
        path_value = path_entry.get("path")
        if not isinstance(path_value, dict):
            raise AssertionError("La respuesta del nodo no contiene path")
        if path_value.get("name") != config.expected_path:
            raise AssertionError("La ruta del nodo de prueba no coincide con la configuracion")

        current_started = time.perf_counter()
        current = client.download_current_content(config.node_id, current_path)
        _log_step("write_download_current", "GET", f"/nodes/{_mask_node_id(config.node_id)}/content", 200, time.perf_counter() - current_started)
        if current.node_id != config.node_id:
            raise AssertionError("La descarga actual no retorno el nodeId esperado")
        if current.version_id != node.version_label:
            raise AssertionError("La version descargada no coincide con la version actual del nodo")
        _assert_valid_pdf_artifact(current.path, expected_sha256=current.sha256)

        source_bytes = _make_pdf_bytes(f"FirmaDoc lab write {uuid4().hex}")
        source_path.write_bytes(source_bytes)
        expected_source_sha = hashlib.sha256(source_bytes).hexdigest()
        comment_opeid = uuid4()
        comment = f"FirmaDoc:0:0:{comment_opeid}"

        upload_started = time.perf_counter()
        upload_result = client.update_content_as_new_version(
            node_id=config.node_id,
            source_path=source_path,
            major_version=False,
            comment=comment,
        )
        _log_step("write_publication", "PUT", f"/nodes/{_mask_node_id(config.node_id)}/content", upload_result.status_code, time.perf_counter() - upload_started)
        if upload_result.status_code not in (200, 201):
            raise AssertionError("La publicacion no retorno un estado exitoso")
        if upload_result.node_id != config.node_id:
            raise AssertionError("El nodeId publicado no coincide con el nodo autorizado")
        if not upload_result.remote_message:
            raise AssertionError("La respuesta remota de publicacion no contiene mensaje")

        updated_node = client.get_node(config.node_id)
        if updated_node.version_label == node.version_label:
            raise AssertionError("La version remota no cambio tras la publicacion")

        versions = client.list_versions(config.node_id, skip_count=0, max_items=max(10, config.history_page_size))
        matching_versions = [version for version in versions if version.comment == comment]
        if len(matching_versions) != 1:
            raise AssertionError("La nueva version no aparece exactamente una vez con el comentario esperado")
        published_version = matching_versions[0]
        if upload_result.version_id and upload_result.version_id != published_version.version_id:
            raise AssertionError("La version devuelta por la publicacion no coincide con el historial")

        published = client.download_version_content(config.node_id, published_version.version_id, version_path)
        if published.sha256 != expected_source_sha:
            raise AssertionError("El hash descargado de la nueva version no coincide con el PDF publicado")
        _assert_valid_pdf_artifact(published.path, expected_sha256=expected_source_sha)

        current_after = client.download_current_content(config.node_id, current_after_path)
        if current_after.version_id != published.version_id:
            raise AssertionError("La descarga actual no apunta a la version recien publicada")
        if current_after.sha256 != expected_source_sha:
            raise AssertionError("La descarga actual no coincide con el hash del PDF publicado")
        _assert_valid_pdf_artifact(current_after.path, expected_sha256=expected_source_sha)
    finally:
        client.close()
        for path in (current_after_path, version_path, current_path, source_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
