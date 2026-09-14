from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable
from urllib.parse import urljoin, urlparse
from uuid import UUID

import httpx

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - fallback for older installs
    import fitz  # type: ignore[no-redef]

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.identity import IdentitySnapshot
from app.crud.crud_audifir import create_evento_tx
from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma
from app.models.docpart import DocPart
from app.models.docpaso import DocPaso
from app.schemas.alfresco import (
    AlfrescoDownloadedArtifact,
    AlfrescoNodeSnapshot,
    AlfrescoRepositorySnapshot,
    AlfrescoUploadResult,
    AlfrescoVersionSnapshot,
)
from app.schemas.docfirma import ResultContract, ResultFase, ResultFlag
from app.schemas.pdf_signature import PdfValidationResult
from app.services.pdf_validation_service import PdfValidationService, pdf_validation_service
from app.services.signature_exceptions import (
    SignatureArtifactGoneError,
    SignatureConcurrencyError,
    SignatureError,
    SignatureIntegrityError,
    SignatureNotFoundError,
    SignaturePayloadError,
    SignaturePublicationError,
    SignatureRecoveryRequiredError,
    SignatureStateError,
    SignatureUploadError,
    SignatureVersionConflictError,
    SignatureWriteDisabledError,
)
from app.services.signature_service import signature_service
from app.services.temporary_artifact_service import TemporaryArtifactService, temporary_artifact_service


_ALLOWED_HOST = "alfresco-lab.test"
_ALLOWED_SCHEME = "https"
_DEFAULT_DISCOVERY_PATH = "/alfresco/api/discovery"
_DEFAULT_API_PATH = "/alfresco/api/-default-/public/alfresco/versions/1"
_BOGOTA_TZ = timezone.utc
_MAX_REDIRECTS = 5


def _sanitize_text(value: Any, limit: int = 200) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _version_key(version_label: str | None) -> tuple[int, ...]:
    if not version_label:
        return (0,)
    parts = [int(part) for part in re.findall(r"\d+", version_label)]
    return tuple(parts) if parts else (0,)


def _is_version_after(candidate: str | None, origin: str | None) -> bool:
    if not candidate or not origin:
        return False
    return _version_key(candidate) > _version_key(origin)


def _extract_path(entry: dict[str, Any]) -> Optional[str]:
    path_value = entry.get("path")
    if isinstance(path_value, str):
        return path_value
    if isinstance(path_value, dict):
        if isinstance(path_value.get("name"), str) and path_value["name"].strip():
            return path_value["name"]
        elements = path_value.get("elements")
        if isinstance(elements, list):
            names = []
            for element in elements:
                if isinstance(element, dict):
                    name = element.get("name")
                    if isinstance(name, str) and name.strip():
                        names.append(name.strip())
            if names:
                return "/".join(names)
    return None


def _extract_version_id_from_payload(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    entry = payload.get("entry")
    if not isinstance(entry, dict):
        entry = payload
    for key in ("versionId", "version_id"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    properties = entry.get("properties")
    if isinstance(properties, dict):
        for key in ("cm:versionLabel", "versionLabel", "version_id"):
            value = properties.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    version = entry.get("version")
    if isinstance(version, dict):
        for key in ("id", "versionLabel", "label"):
            value = version.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("versionLabel", "id"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_etag(entry: dict[str, Any], response: httpx.Response) -> str | None:
    etag = entry.get("etag")
    if isinstance(etag, str) and etag.strip():
        return etag.strip()
    response_etag = response.headers.get("etag")
    if isinstance(response_etag, str) and response_etag.strip():
        return response_etag.strip()
    return None


def _extract_remote_message(payload: Any, response: httpx.Response) -> str:
    if isinstance(payload, dict):
        entry = payload.get("entry")
        if isinstance(entry, dict):
            for key in ("message", "status", "versionComment", "comment", "detail"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    return _sanitize_text(value)
        for key in ("message", "status", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _sanitize_text(value)
    reason = getattr(response, "reason_phrase", "") or ""
    return _sanitize_text(reason or f"HTTP {response.status_code}")


@dataclass(frozen=True, slots=True)
class _PublicationSnapshot:
    firid: int
    docid: int
    parid: int
    node_id: str
    opeid: UUID
    revnum: int
    verori: str
    hasori: str
    hasfin: str
    docnom: str
    mimtip: str
    participant_nomcom: str
    participant_rolpro: str
    participant_verlock: int
    usrmod: str
    generated_path: Path
    actor_user: str = ""
    iporig: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationOutcome:
    status: str
    operation_id: str
    source_version: str
    publication_status: str
    message: str
    code: str | None = None
    final_version: str | None = None
    final_hash_short: str | None = None


@runtime_checkable
class AlfrescoClientProtocol(Protocol):
    def get_repository_info(self) -> AlfrescoRepositorySnapshot:
        raise NotImplementedError

    def get_node(self, node_id: str) -> AlfrescoNodeSnapshot:
        raise NotImplementedError

    def download_current_content(self, node_id: str, destination_path: Path | str) -> AlfrescoDownloadedArtifact:
        raise NotImplementedError

    def list_versions(self, node_id: str, skip_count: int, max_items: int) -> list[AlfrescoVersionSnapshot]:
        raise NotImplementedError

    def get_version(self, node_id: str, version_id: str) -> AlfrescoVersionSnapshot:
        raise NotImplementedError

    def download_version_content(
        self,
        node_id: str,
        version_id: str,
        destination_path: Path | str,
    ) -> AlfrescoDownloadedArtifact:
        raise NotImplementedError

    def update_content_as_new_version(
        self,
        node_id: str,
        source_path: Path | str,
        major_version: bool,
        comment: str,
        precondition: str | None = None,
    ) -> AlfrescoUploadResult:
        raise NotImplementedError


class AlfrescoLabClient(AlfrescoClientProtocol):
    def __init__(
        self,
        base_url: str | None = None,
        api_path: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ca_bundle: str | Path | None = None,
        expected_host: str | None = None,
        connect_timeout: float | int | None = None,
        read_timeout: float | int | None = None,
        write_timeout: float | int | None = None,
        max_download_size: int | None = None,
        max_history_pages: int | None = None,
        history_page_size: int | None = None,
    ) -> None:
        self.expected_host = expected_host or settings.FIRMADOC_ALFRESCO_EXPECTED_HOST
        raw_base_url = base_url or settings.ALFRESCO_BASE_URL
        self.root_url = self._normalize_base_url(raw_base_url)
        if urlparse(self.root_url).hostname != self.expected_host:
            raise SignatureUploadError("El host de Alfresco no esta autorizado")
        self.api_path = self._normalize_api_path(api_path or settings.ALFRESCO_API_PATH or settings.ALFRESCO_API_URL)
        self.discovery_url = f"{self.root_url}{_DEFAULT_DISCOVERY_PATH}"
        self.api_url = f"{self.root_url}{self.api_path}"

        self.username = username or settings.ALFRESCO_USERNAME or settings.ALFRESCO_USER
        self.password = password or settings.ALFRESCO_PASSWORD
        if not self.username or not self.password:
            raise SignatureUploadError("Credenciales de Alfresco no configuradas")

        ca_bundle_value = ca_bundle if ca_bundle is not None else settings.ALFRESCO_CA_BUNDLE
        if ca_bundle_value:
            ca_bundle_path = Path(ca_bundle_value)
            if not ca_bundle_path.exists():
                raise SignatureUploadError("La CA de Alfresco configurada no existe")
            self.verify: bool | str = str(ca_bundle_path)
        else:
            self.verify = True

        self.connect_timeout = float(connect_timeout if connect_timeout is not None else settings.ALFRESCO_CONNECT_TIMEOUT)
        self.read_timeout = float(read_timeout if read_timeout is not None else settings.ALFRESCO_READ_TIMEOUT)
        self.write_timeout = float(write_timeout if write_timeout is not None else settings.ALFRESCO_WRITE_TIMEOUT)
        self.max_download_size = int(max_download_size if max_download_size is not None else settings.ALFRESCO_MAX_DOWNLOAD_SIZE)
        self.max_history_pages = int(max_history_pages if max_history_pages is not None else settings.ALFRESCO_MAX_HISTORY_PAGES)
        self.history_page_size = int(history_page_size if history_page_size is not None else settings.ALFRESCO_HISTORY_PAGE_SIZE)
        self.timeout = httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.connect_timeout,
        )
        self._client = httpx.Client(
            auth=(self.username, self.password),
            timeout=self.timeout,
            verify=self.verify,
            follow_redirects=False,
        )

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        parsed = urlparse(base_url.strip())
        if parsed.scheme.lower() != _ALLOWED_SCHEME:
            raise SignatureUploadError("Alfresco debe usar HTTPS")
        if not parsed.hostname:
            raise SignatureUploadError("La URL de Alfresco no es valida")
        if parsed.path not in ("", "/"):
            raise SignatureUploadError("La URL de Alfresco no debe incluir ruta base")
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    @staticmethod
    def _normalize_api_path(api_path: str) -> str:
        normalized = api_path.strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized.rstrip("/") or _DEFAULT_API_PATH

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AlfrescoLabClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _is_allowed_absolute_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme.lower() == _ALLOWED_SCHEME and parsed.hostname == self.expected_host

    def _build_request(self, method: str, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, content: Any = None, stream: bool = False) -> httpx.Response:
        current_url = url
        current_params = params
        for _ in range(_MAX_REDIRECTS + 1):
            request = self._client.build_request(method, current_url, params=current_params, headers=headers, content=content)
            response = self._client.send(request, stream=stream)
            if not response.is_redirect:
                return response
            location = response.headers.get("location")
            response.close()
            if not location:
                raise SignatureUploadError("Alfresco devolvio una redireccion sin destino")
            next_url = urljoin(str(request.url), location)
            if not self._is_allowed_absolute_url(next_url):
                raise SignatureUploadError("Alfresco redirigio hacia un host no autorizado")
            if method.upper() not in {"GET", "HEAD"}:
                raise SignatureUploadError("Las redirecciones no estan permitidas para operaciones de escritura")
            current_url = next_url
            current_params = None
        raise SignatureUploadError("Alfresco excedio el limite de redirecciones")

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        content: Any = None,
        not_found_conflict: bool = True,
        allow_redirects: bool = True,
    ) -> tuple[dict[str, Any], httpx.Response]:
        response = self._build_request(method, url, params=params, headers=headers, content=content, stream=False)
        try:
            if response.status_code == 204:
                raise SignaturePayloadError("Alfresco devolvio una respuesta vacia")
            if response.status_code >= 400:
                self._raise_for_status(response.status_code, response, not_found_conflict=not_found_conflict)
            payload = response.json()
            if not isinstance(payload, dict):
                raise SignaturePayloadError("Alfresco devolvio un JSON inesperado")
            return payload, response
        except ValueError as exc:
            raise SignaturePayloadError("Alfresco devolvio un JSON invalido") from exc
        finally:
            response.close()

    def _raise_for_status(self, status_code: int, response: httpx.Response, *, not_found_conflict: bool) -> None:
        message = _sanitize_text(response.text or response.reason_phrase or f"HTTP {status_code}")
        if status_code in (401, 403):
            raise SignatureUploadError(f"Alfresco rechazo la solicitud ({status_code})")
        if status_code == 404:
            if not_found_conflict:
                raise SignatureVersionConflictError("El recurso remoto no existe o cambio durante la operacion")
            raise SignatureUploadError("El recurso remoto no existe")
        if status_code in (409, 412):
            raise SignatureVersionConflictError("Alfresco reporto un conflicto de version")
        if status_code == 413:
            raise SignaturePayloadError("Alfresco rechazo el contenido por tamano")
        if status_code == 429:
            raise SignatureRecoveryRequiredError("Alfresco limito la solicitud y se requiere reconciliacion")
        if 500 <= status_code < 600:
            raise SignatureRecoveryRequiredError("Alfresco no confirmo la operacion y se requiere reconciliacion")
        raise SignatureUploadError(message)

    def _download_pdf(
        self,
        url: str,
        node_id: str,
        version_id: str | None,
        destination_path: Path | str,
        expected_mime_type: str | None,
    ) -> AlfrescoDownloadedArtifact:
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self._build_request(
            "GET",
            url,
            headers={"Accept": "application/pdf"},
            stream=True,
        )
        sha256 = hashlib.sha256()
        size_bytes = 0
        try:
            if response.status_code >= 400:
                response.read()
                self._raise_for_status(response.status_code, response, not_found_conflict=True)

            response_mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if expected_mime_type and response_mime and response_mime != expected_mime_type.lower():
                raise SignaturePayloadError("El contenido remoto no tiene el MIME esperado")

            declared_length_raw = response.headers.get("content-length")
            declared_length = int(declared_length_raw) if declared_length_raw and declared_length_raw.isdigit() else None
            if declared_length is not None and declared_length > self.max_download_size:
                raise SignaturePayloadError("El contenido remoto excede el tamano maximo permitido")

            with destination.open("wb") as target:
                for chunk in response.iter_bytes(8192):
                    if not chunk:
                        continue
                    size_bytes += len(chunk)
                    if size_bytes > self.max_download_size:
                        raise SignaturePayloadError("El contenido remoto excede el tamano maximo permitido")
                    target.write(chunk)
                    sha256.update(chunk)

            if declared_length is not None and size_bytes != declared_length:
                raise SignaturePayloadError("El tamano declarado por Alfresco no coincide con los bytes descargados")
            if size_bytes <= 0:
                raise SignaturePayloadError("Alfresco devolvio un contenido vacio")

            data = destination.read_bytes()
            try:
                with fitz.open(stream=data, filetype="pdf") as document:
                    if not document.is_pdf:
                        raise SignaturePayloadError("El contenido remoto no es un PDF valido")
                    if document.needs_pass:
                        raise SignaturePayloadError("El PDF remoto esta cifrado y no puede procesarse")
                    if document.page_count <= 0:
                        raise SignaturePayloadError("El PDF remoto no contiene paginas")
            except SignatureError:
                raise
            except Exception as exc:
                raise SignaturePayloadError("No fue posible abrir el PDF remoto") from exc

            mime_type = expected_mime_type or "application/pdf"
            raw_etag = response.headers.get("etag")
            etag = raw_etag.strip() if isinstance(raw_etag, str) and raw_etag.strip() else None
            return AlfrescoDownloadedArtifact(
                node_id=node_id,
                version_id=version_id,
                path=destination,
                sha256=sha256.hexdigest(),
                size_bytes=size_bytes,
                mime_type=mime_type,
                etag=etag,
            )
        except SignatureError:
            self._cleanup_path(destination)
            raise
        except Exception as exc:
            self._cleanup_path(destination)
            raise SignaturePayloadError("No fue posible descargar el contenido de Alfresco") from exc
        finally:
            response.close()

    def _cleanup_path(self, path: Path | str | None) -> None:
        if path is None:
            return
        try:
            temporary_artifact_service.cleanup_path(path)
        except Exception:
            pass

    def get_repository_info(self) -> AlfrescoRepositorySnapshot:
        payload, response = self._request_json("GET", self.discovery_url, not_found_conflict=False)
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            entry = payload
        repository = entry.get("repository")
        if not isinstance(repository, dict):
            repository = entry
        version = repository.get("version")
        if not isinstance(version, dict):
            version = entry.get("version") if isinstance(entry.get("version"), dict) else {}
        status = entry.get("status")
        if not isinstance(status, dict):
            status = {}

        major = int(version.get("major") or 0)
        minor = int(version.get("minor") or 0)
        patch = int(version.get("patch") or 0)
        hotfix = int(version.get("hotfix") or 0)
        schema = int(version.get("schema") or 0)
        version_label = _sanitize_text(version.get("label") or version.get("versionLabel") or f"{major}.{minor}.{patch}")
        version_display = _sanitize_text(version.get("display") or version_label)
        repository_id = _sanitize_text(repository.get("id") or entry.get("repositoryId") or self.expected_host)
        edition = _sanitize_text(repository.get("edition") or entry.get("edition") or "unknown")
        is_read_only = bool(status.get("isReadOnly") or status.get("readOnly") or False)

        return AlfrescoRepositorySnapshot(
            repository_id=repository_id,
            edition=edition,
            version_major=major,
            version_minor=minor,
            version_patch=patch,
            version_hotfix=hotfix,
            version_schema=schema,
            version_label=version_label,
            version_display=version_display,
            is_read_only=is_read_only,
        )

    def get_node(self, node_id: str) -> AlfrescoNodeSnapshot:
        payload, response = self._request_json("GET", f"{self.api_url}/nodes/{node_id}", not_found_conflict=True)
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            raise SignaturePayloadError("Alfresco devolvio un nodo invalido")
        content = entry.get("content")
        if not isinstance(content, dict):
            content = {}
        modified_by_user = entry.get("modifiedByUser")
        modified_by = None
        if isinstance(modified_by_user, dict):
            modified_by = modified_by_user.get("id")
        if not isinstance(modified_by, str):
            modified_by = None
        properties = entry.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        version_label = properties.get("cm:versionLabel") or entry.get("versionLabel") or content.get("versionLabel")
        path = _extract_path(entry)

        return AlfrescoNodeSnapshot(
            node_id=_sanitize_text(entry.get("id") or node_id),
            name=_sanitize_text(entry.get("name") or ""),
            node_type=_sanitize_text(entry.get("nodeType") or ""),
            mime_type=_sanitize_text(content.get("mimeType")) or None,
            size_bytes=int(content.get("sizeInBytes") or 0),
            modified_at=_coerce_datetime(entry.get("modifiedAt")),
            version_label=_sanitize_text(version_label) or None,
            etag=_extract_etag(entry, response),
            is_file=bool(entry.get("isFile")),
            path=path,
            modified_by=modified_by,
        )

    def download_current_content(self, node_id: str, destination_path: Path | str) -> AlfrescoDownloadedArtifact:
        node = self.get_node(node_id)
        if not node.is_file:
            raise SignaturePayloadError("El nodo remoto no es un archivo")
        if node.mime_type and node.mime_type.lower() != "application/pdf":
            raise SignaturePayloadError("El nodo remoto no es un PDF")
        return self._download_pdf(
            f"{self.api_url}/nodes/{node_id}/content",
            node_id=node.node_id,
            version_id=node.version_label,
            destination_path=destination_path,
            expected_mime_type=node.mime_type or "application/pdf",
        )

    def list_versions(self, node_id: str, skip_count: int, max_items: int) -> list[AlfrescoVersionSnapshot]:
        payload, response = self._request_json(
            "GET",
            f"{self.api_url}/nodes/{node_id}/versions",
            params={"skipCount": skip_count, "maxItems": max_items},
            not_found_conflict=True,
        )
        list_payload = payload.get("list")
        if not isinstance(list_payload, dict):
            raise SignaturePayloadError("Alfresco devolvio un historial de versiones invalido")
        entries = list_payload.get("entries")
        if not isinstance(entries, list):
            raise SignaturePayloadError("Alfresco devolvio un historial de versiones invalido")

        versions: list[AlfrescoVersionSnapshot] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            entry = item.get("entry")
            if not isinstance(entry, dict):
                continue
            modifier = entry.get("modifiedByUser")
            modifier_id = None
            if isinstance(modifier, dict):
                modifier_id = modifier.get("id")
            if not isinstance(modifier_id, str):
                modifier_id = None
            version_id = entry.get("id") or entry.get("versionLabel")
            if not isinstance(version_id, str) or not version_id.strip():
                continue
            versions.append(
                AlfrescoVersionSnapshot(
                    node_id=_sanitize_text(entry.get("nodeId") or node_id),
                    version_id=version_id.strip(),
                    comment=_sanitize_text(entry.get("versionComment") or entry.get("comment")) or None,
                    created_at=_coerce_datetime(entry.get("createdAt")),
                    modifier=modifier_id,
                    etag=_extract_etag(entry, response),
                )
            )
        return versions

    def get_version(self, node_id: str, version_id: str) -> AlfrescoVersionSnapshot:
        payload, response = self._request_json(
            "GET",
            f"{self.api_url}/nodes/{node_id}/versions/{version_id}",
            not_found_conflict=True,
        )
        entry = payload.get("entry")
        if not isinstance(entry, dict):
            raise SignaturePayloadError("Alfresco devolvio una version invalida")
        modifier = entry.get("modifiedByUser")
        modifier_id = None
        if isinstance(modifier, dict):
            modifier_id = modifier.get("id")
        if not isinstance(modifier_id, str):
            modifier_id = None
        resolved_version_id = entry.get("id") or version_id
        if not isinstance(resolved_version_id, str) or not resolved_version_id.strip():
            raise SignaturePayloadError("Alfresco devolvio una version invalida")
        return AlfrescoVersionSnapshot(
            node_id=_sanitize_text(entry.get("nodeId") or node_id),
            version_id=resolved_version_id.strip(),
            comment=_sanitize_text(entry.get("versionComment") or entry.get("comment")) or None,
            created_at=_coerce_datetime(entry.get("createdAt")),
            modifier=modifier_id,
            etag=_extract_etag(entry, response),
        )

    def download_version_content(
        self,
        node_id: str,
        version_id: str,
        destination_path: Path | str,
    ) -> AlfrescoDownloadedArtifact:
        version = self.get_version(node_id, version_id)
        return self._download_pdf(
            f"{self.api_url}/nodes/{node_id}/versions/{version.version_id}/content",
            node_id=version.node_id,
            version_id=version.version_id,
            destination_path=destination_path,
            expected_mime_type="application/pdf",
        )

    def update_content_as_new_version(
        self,
        node_id: str,
        source_path: Path | str,
        major_version: bool,
        comment: str,
        precondition: str | None = None,
    ) -> AlfrescoUploadResult:
        path = Path(source_path)
        if not path.exists():
            raise SignaturePayloadError("El PDF a publicar no existe")
        if not path.is_file():
            raise SignaturePayloadError("El PDF a publicar no es un archivo regular")
        if path.is_symlink():
            raise SignaturePayloadError("El PDF a publicar no puede ser un enlace simbolico")

        params = {
            "majorVersion": "true" if major_version else "false",
            "comment": comment,
        }
        headers = {"Content-Type": "application/pdf"}
        if precondition:
            headers["If-Match"] = precondition

        try:
            with path.open("rb") as stream:
                response = self._build_request(
                    "PUT",
                    f"{self.api_url}/nodes/{node_id}/content",
                    params=params,
                    headers=headers,
                    content=stream,
                    stream=False,
                )
        except httpx.TimeoutException as exc:
            raise SignatureRecoveryRequiredError("Alfresco no confirmo la publicacion") from exc
        except httpx.TransportError as exc:
            raise SignatureUploadError("Alfresco rechazo la publicacion") from exc

        try:
            if response.status_code >= 400:
                self._raise_for_status(response.status_code, response, not_found_conflict=True)
            payload: dict[str, Any] = {}
            if response.content:
                try:
                    json_payload = response.json()
                except ValueError:
                    json_payload = {}
                if isinstance(json_payload, dict):
                    payload = json_payload
            version_id = _extract_version_id_from_payload(payload)
            remote_message = _extract_remote_message(payload, response)
            return AlfrescoUploadResult(
                node_id=_sanitize_text(node_id),
                version_id=version_id,
                status_code=response.status_code,
                etag=response.headers.get("etag") if isinstance(response.headers.get("etag"), str) else None,
                remote_message=remote_message,
            )
        except SignatureError:
            raise
        except Exception as exc:
            raise SignatureUploadError("Alfresco no confirmo la publicacion") from exc
        finally:
            response.close()


class AlfrescoService:
    def __init__(
        self,
        client: AlfrescoClientProtocol | None = None,
        pdf_validator: PdfValidationService | None = None,
        artifact_service: TemporaryArtifactService | None = None,
        clock: Callable[[], datetime] | None = None,
        write_enabled: bool | None = None,
        test_node_id: str | None = None,
        test_expected_name: str | None = None,
        test_expected_path: str | None = None,
        test_expected_mimetype: str | None = None,
        major_version: bool | None = None,
        reconcile_min_checks: int | None = None,
        reconcile_wait_seconds: int | None = None,
    ) -> None:
        self.client = client or AlfrescoLabClient()
        self.pdf_validator = pdf_validator or pdf_validation_service
        self.artifact_service = artifact_service or temporary_artifact_service
        self.clock = clock or (lambda: datetime.now(_BOGOTA_TZ))
        self.write_enabled = settings.FIRMADOC_ALFRESCO_WRITE_ENABLED if write_enabled is None else write_enabled
        self.test_node_id = test_node_id if test_node_id is not None else settings.FIRMADOC_ALFRESCO_TEST_NODE_ID
        self.test_expected_name = test_expected_name if test_expected_name is not None else settings.FIRMADOC_ALFRESCO_TEST_EXPECTED_NAME
        self.test_expected_path = test_expected_path if test_expected_path is not None else settings.FIRMADOC_ALFRESCO_TEST_EXPECTED_PATH
        self.test_expected_mimetype = test_expected_mimetype if test_expected_mimetype is not None else settings.FIRMADOC_ALFRESCO_TEST_EXPECTED_MIMETYPE
        self.major_version = settings.FIRMADOC_ALFRESCO_MAJOR_VERSION if major_version is None else major_version
        self.reconcile_min_checks = settings.FIRMADOC_RECONCILE_MIN_CHECKS if reconcile_min_checks is None else reconcile_min_checks
        self.reconcile_wait_seconds = settings.FIRMADOC_RECONCILE_WAIT_SECONDS if reconcile_wait_seconds is None else reconcile_wait_seconds

    def _build_session_factory(self, db: Session) -> sessionmaker:
        bind = db.get_bind()
        if bind is None:
            raise SignaturePayloadError("No fue posible determinar la conexion de base de datos")
        return sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)

    def _default_usrmod(self, firma: DocFirma) -> str:
        return (firma.usrmod or firma.usrcre or "system").strip()

    def _build_comment(self, snapshot: _PublicationSnapshot) -> str:
        return f"FirmaDoc:{snapshot.docid}:{snapshot.firid}:{snapshot.opeid}"

    def _short_hash(self, value: str | None) -> str:
        if not value:
            return ""
        return f"{value[:12]}...{value[-8:]}"

    def _publication_detail(self, snapshot: _PublicationSnapshot, detail: str) -> str:
        pieces = [
            detail,
            f"docid={snapshot.docid}",
            f"firid={snapshot.firid}",
            f"operation_id={snapshot.opeid}",
            f"source_version={snapshot.verori}",
            f"source_hash={self._short_hash(snapshot.hasori)}",
            f"final_hash={self._short_hash(snapshot.hasfin)}",
        ]
        if snapshot.user_agent:
            pieces.append(f"user_agent={_sanitize_text(snapshot.user_agent, 120)}")
        return "; ".join(pieces)

    def _record_publication_event(self, db: Session, snapshot: _PublicationSnapshot, evento: str, detail: str) -> None:
        short_db = self._build_session_factory(db)()
        try:
            create_evento_tx(
                db=short_db,
                evento=evento,
                enttip="FIRMA",
                entid=snapshot.firid,
                docid=snapshot.docid,
                usrid=snapshot.actor_user or snapshot.usrmod,
                iporig=snapshot.iporig,
                detalle=_sanitize_text(self._publication_detail(snapshot, detail), 900),
            )
            short_db.commit()
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _raise_publication(
        self,
        snapshot: _PublicationSnapshot,
        message: str,
        *,
        code: str,
        status_code: int = 409,
        publication_status: str = "BLOCKED",
    ) -> None:
        raise SignaturePublicationError(
            message,
            code=code,
            status_code=status_code,
            operation_id=str(snapshot.opeid),
            source_version=snapshot.verori,
            publication_status=publication_status,
        )

    def _load_document_publication_snapshot(
        self,
        db: Session,
        docid: int,
        actor_user: str,
        iporig: str | None,
        user_agent: str | None,
    ) -> _PublicationSnapshot:
        user = actor_user.strip().lower()
        doc = db.scalars(select(DocFir).where(DocFir.docid == docid)).first()
        if not doc:
            raise SignatureNotFoundError("Documento no encontrado")
        if user != (doc.usrcre or "").strip().lower():
            raise SignaturePublicationError(
                "No autorizado para publicar este documento",
                code="PUBLICATION_FORBIDDEN",
                status_code=403,
                source_version=doc.verini,
            )
        if doc.estado != EstadoDoc.PENDIENTE_PUBLICACION.value:
            raise SignaturePublicationError(
                "El documento no esta pendiente de publicacion",
                code="DOCUMENT_NOT_PENDING_PUBLICATION",
                status_code=409,
                source_version=doc.verini,
            )
        if not self.test_node_id:
            raise SignaturePublicationError(
                "No existe un nodo de prueba autorizado para publicar",
                code="TEST_NODE_NOT_CONFIGURED",
                status_code=409,
                source_version=doc.verini,
            )
        if doc.nodid != self.test_node_id:
            raise SignaturePublicationError(
                "El nodo remoto no coincide con el nodo de prueba autorizado",
                code="TEST_NODE_MISMATCH",
                status_code=409,
                source_version=doc.verini,
            )
        if doc.mimtip != "application/pdf":
            raise SignaturePublicationError(
                "El documento no es un PDF",
                code="DOCUMENT_NOT_PDF",
                status_code=422,
                source_version=doc.verini,
            )

        active_publication = db.scalars(
            select(DocFirma)
            .where(
                DocFirma.docid == doc.docid,
                DocFirma.estado.in_(
                    [
                        EstadoDocFirma.SUBIENDO.value,
                        EstadoDocFirma.CARGADA.value,
                        EstadoDocFirma.VERIFICANDO.value,
                    ]
                ),
            )
        ).first()
        if active_publication:
            raise SignaturePublicationError(
                "Ya existe una publicacion recuperable para este documento",
                code="PUBLICATION_ALREADY_ACTIVE",
                status_code=409,
                operation_id=str(active_publication.opeid),
                source_version=active_publication.verori,
                publication_status="RECOVERY_REQUIRED",
            )

        pending_required = db.scalars(
            select(DocPart)
            .join(DocPaso, DocPaso.dpasid == DocPart.dpasid)
            .where(
                DocPaso.docid == doc.docid,
                DocPart.obliga.is_(True),
                DocPart.estado != "COMPLETADO",
            )
        ).first()
        if pending_required:
            raise SignaturePublicationError(
                "Existen firmas obligatorias pendientes",
                code="REQUIRED_SIGNATURES_INCOMPLETE",
                status_code=409,
                source_version=doc.verini,
            )

        firma = db.scalars(
            select(DocFirma)
            .where(DocFirma.docid == doc.docid, DocFirma.estado == EstadoDocFirma.COMPLETADA.value)
            .order_by(DocFirma.secuen.desc(), DocFirma.firid.desc())
        ).first()
        if not firma:
            raise SignaturePublicationError(
                "No existe una firma final completada para publicar",
                code="FINAL_SIGNATURE_NOT_FOUND",
                status_code=409,
                source_version=doc.verini,
            )

        part = db.scalars(select(DocPart).where(DocPart.parid == firma.parid)).first()
        if not part:
            raise SignatureNotFoundError("Participante no encontrado")

        self.artifact_service.cleanup_expired()
        generated_path = self.artifact_service.find_latest_path(prefix=f"fir-{firma.firid}-", suffix=".pdf")
        if generated_path is None:
            raise SignatureArtifactGoneError(
                operation_id=str(firma.opeid),
                source_version=firma.verori,
            )

        return _PublicationSnapshot(
            firid=firma.firid,
            docid=doc.docid,
            parid=firma.parid,
            node_id=doc.nodid,
            opeid=firma.opeid,
            revnum=int(firma.revnum),
            verori=firma.verori,
            hasori=firma.hasori,
            hasfin=firma.hasfin or doc.hasfir or "",
            docnom=doc.docnom,
            mimtip=doc.mimtip,
            participant_nomcom=part.nomcom,
            participant_rolpro=part.rolpro or "",
            participant_verlock=int(part.verlock),
            usrmod=user,
            generated_path=generated_path,
            actor_user=user,
            iporig=iporig,
            user_agent=user_agent,
        )

    def _load_publication_snapshot(self, db: Session, firid: int, expected_revnum: int, expected_participant_verlock: int) -> _PublicationSnapshot:
        session_factory = self._build_session_factory(db)
        read_db = session_factory()
        try:
            firma = read_db.scalars(select(DocFirma).where(DocFirma.firid == firid)).first()
            if not firma:
                raise SignatureNotFoundError("Intento de firma no encontrado")
            if firma.estado != EstadoDocFirma.GENERADA.value:
                raise SignatureStateError("La firma debe estar en estado GENERADA para publicar")
            if int(firma.revnum) != int(expected_revnum):
                raise SignatureConcurrencyError("El revnum del intento cambio antes de publicar")

            part = read_db.scalars(select(DocPart).where(DocPart.parid == firma.parid)).first()
            if not part:
                raise SignatureNotFoundError("Participante no encontrado")
            if int(part.verlock) != int(expected_participant_verlock):
                raise SignatureConcurrencyError("El verlock del participante cambio antes de publicar")

            doc = read_db.scalars(select(DocFir).where(DocFir.docid == firma.docid)).first()
            if not doc:
                raise SignatureNotFoundError("Documento no encontrado")
            if doc.nodid != self.test_node_id:
                raise SignatureUploadError("El nodo remoto no coincide con el nodo de prueba autorizado")
            if doc.mimtip != "application/pdf":
                raise SignaturePayloadError("El documento no es un PDF")

            generated_path = self.artifact_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf")
            if generated_path is None:
                raise SignaturePayloadError("No existe un PDF generado temporal para el intento")

            return _PublicationSnapshot(
                firid=firma.firid,
                docid=firma.docid,
                parid=firma.parid,
                node_id=doc.nodid,
                opeid=firma.opeid,
                revnum=int(firma.revnum),
                verori=firma.verori,
                hasori=firma.hasori,
                hasfin=firma.hasfin or "",
                docnom=doc.docnom,
                mimtip=doc.mimtip,
                participant_nomcom=part.nomcom,
                participant_rolpro=part.rolpro or "",
                participant_verlock=int(part.verlock),
                usrmod=self._default_usrmod(firma),
                generated_path=generated_path,
            )
        finally:
            read_db.close()

    def _ensure_local_pdf_hash(self, snapshot: _PublicationSnapshot) -> PdfValidationResult:
        validation = self.pdf_validator.validate_source_pdf(snapshot.generated_path)
        if not snapshot.hasfin:
            raise SignaturePayloadError("No existe hash final para el PDF generado")
        if validation.sha256.lower() != snapshot.hasfin.lower():
            raise SignatureIntegrityError("El hash del PDF generado no coincide con hasfin")
        return validation

    def _validate_remote_node(self, node: AlfrescoNodeSnapshot, snapshot: _PublicationSnapshot) -> None:
        if node.node_id != snapshot.node_id:
            raise SignatureUploadError("El nodo remoto no coincide con el documento autorizado")
        if not node.is_file:
            raise SignatureUploadError("El nodo remoto no es un archivo")
        if node.mime_type and node.mime_type.lower() != self.test_expected_mimetype.lower():
            raise SignatureUploadError("El nodo remoto no tiene el MIME autorizado")
        if self.test_expected_name and node.name != self.test_expected_name:
            raise SignatureUploadError("El nombre remoto no coincide con el nodo de prueba autorizado")
        if self.test_expected_path and node.path and node.path != self.test_expected_path:
            raise SignatureUploadError("La ruta remota no coincide con el nodo de prueba autorizado")

    def _record_recovery_event(self, db: Session, snapshot: _PublicationSnapshot, detail: str) -> None:
        short_db = self._build_session_factory(db)()
        try:
            create_evento_tx(
                db=short_db,
                evento="FIR_RECO",
                enttip="FIRMA",
                entid=snapshot.firid,
                docid=snapshot.docid,
                usrid=snapshot.usrmod,
                detalle=_sanitize_text(detail, 500),
            )
            short_db.commit()
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _cleanup_path(self, path: Path | str | None) -> None:
        if path is None:
            return
        try:
            self.artifact_service.cleanup_path(path)
        except Exception:
            pass

    def _publish_current_version(self, snapshot: _PublicationSnapshot, validation: PdfValidationResult) -> tuple[AlfrescoNodeSnapshot, AlfrescoDownloadedArtifact]:
        remote_node = self.client.get_node(snapshot.node_id)
        self._validate_remote_node(remote_node, snapshot)

        current_download_path = self.artifact_service.create_path(prefix=f"alfresco-{snapshot.firid}-current-", suffix=".pdf")
        current_artifact = self.client.download_current_content(snapshot.node_id, current_download_path)
        return remote_node, current_artifact

    def _run_publication_preflight(
        self,
        db: Session,
        snapshot: _PublicationSnapshot,
    ) -> tuple[PdfValidationResult, AlfrescoNodeSnapshot, AlfrescoDownloadedArtifact]:
        self._record_publication_event(db, snapshot, "PUBLICATION_PREFLIGHT_STARTED", "Preflight de publicacion iniciado")
        try:
            validation = self._ensure_local_pdf_hash(snapshot)
        except SignatureIntegrityError:
            self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", "Hash local final no coincide con hasfin")
            self._raise_publication(snapshot, "El hash local final no coincide con el registrado", code="LOCAL_HASH_MISMATCH")
        except SignaturePayloadError as exc:
            self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", f"PDF local invalido: {exc.message}")
            self._raise_publication(snapshot, "El PDF acumulativo no es valido", code="LOCAL_PDF_INVALID", status_code=422)

        remote_node, current_artifact = self._publish_current_version(snapshot, validation)
        if remote_node.version_label != snapshot.verori:
            self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", f"Version remota {remote_node.version_label} difiere de {snapshot.verori}")
            self._cleanup_path(current_artifact.path)
            self._raise_publication(snapshot, "La version remota cambio desde el inicio del proceso", code="REMOTE_VERSION_CONFLICT")
        if current_artifact.sha256.lower() != snapshot.hasori.lower():
            self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", "Hash remoto actual no coincide con hasori")
            self._cleanup_path(current_artifact.path)
            self._raise_publication(snapshot, "El contenido remoto cambio desde el inicio del proceso", code="REMOTE_HASH_CONFLICT")

        self._record_publication_event(db, snapshot, "PUBLICATION_PREFLIGHT_OK", "Preflight de publicacion validado")
        return validation, remote_node, current_artifact

    def _mark_conflict(
        self,
        db: Session,
        snapshot: _PublicationSnapshot,
        expected_revnum: int,
        errcod: str,
        result_data: ResultContract | None = None,
    ) -> int:
        short_db = self._build_session_factory(db)()
        try:
            new_rev = signature_service.mark_signature_conflict(
                db=short_db,
                firid=snapshot.firid,
                expected_revnum=expected_revnum,
                errcod=errcod,
                usrmod=snapshot.usrmod,
                result_data=result_data,
            )
            short_db.commit()
            return new_rev
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _mark_upload_started(self, db: Session, snapshot: _PublicationSnapshot, expected_revnum: int) -> int:
        short_db = self._build_session_factory(db)()
        try:
            new_rev = signature_service.mark_upload_started(
                db=short_db,
                firid=snapshot.firid,
                expected_revnum=expected_revnum,
                usrmod=snapshot.usrmod,
            )
            short_db.commit()
            return new_rev
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _mark_uploaded(self, db: Session, snapshot: _PublicationSnapshot, expected_revnum: int, upload_result: AlfrescoUploadResult) -> int:
        short_db = self._build_session_factory(db)()
        try:
            result_data = ResultContract(
                schema_ver=1,
                fase=ResultFase.PUBLICACION,
                remcod=upload_result.status_code,
                remmsg=upload_result.remote_message,
                recint=0,
                verchk=False,
                haschk=False,
                flags=[ResultFlag.ALFRESCO_OK],
            )
            new_rev = signature_service.mark_uploaded(
                db=short_db,
                firid=snapshot.firid,
                expected_revnum=expected_revnum,
                verfin=upload_result.version_id or "",
                result_data=result_data,
                usrmod=snapshot.usrmod,
            )
            short_db.commit()
            return new_rev
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _mark_verification_started(self, db: Session, snapshot: _PublicationSnapshot, expected_revnum: int) -> int:
        short_db = self._build_session_factory(db)()
        try:
            new_rev = signature_service.mark_verification_started(
                db=short_db,
                firid=snapshot.firid,
                expected_revnum=expected_revnum,
                usrmod=snapshot.usrmod,
            )
            short_db.commit()
            return new_rev
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _persist_verification_evidence(self, db: Session, snapshot: _PublicationSnapshot, expected_revnum: int, upload_result: AlfrescoUploadResult) -> int:
        short_db = self._build_session_factory(db)()
        try:
            new_rev = signature_service.persist_verification_evidence(
                db=short_db,
                firid=snapshot.firid,
                expected_revnum=expected_revnum,
                remcod=upload_result.status_code,
                remmsg=upload_result.remote_message,
                flags=[ResultFlag.ALFRESCO_OK, ResultFlag.HASH_MATCH],
                usrmod=snapshot.usrmod,
            )
            short_db.commit()
            return new_rev
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _finalize_verified_signature(
        self,
        db: Session,
        snapshot: _PublicationSnapshot,
        expected_revnum: int,
    ) -> None:
        short_db = self._build_session_factory(db)()
        try:
            actor = getattr(snapshot, "actor", None)
            if actor is None:
                actor = IdentitySnapshot(
                    usrid=snapshot.usrmod,
                    nomcom=snapshot.participant_nomcom,
                    correo=f"{snapshot.usrmod}@example.invalid",
                    rolpro=snapshot.participant_rolpro or None,
                )
            signature_service.finalize_verified_signature(
                db=short_db,
                firid=snapshot.firid,
                expected_revnum=expected_revnum,
                expected_participant_verlock=snapshot.participant_verlock,
                actor=actor,
            )
            short_db.commit()
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _resolve_uploaded_version(
        self,
        snapshot: _PublicationSnapshot,
        upload_result: AlfrescoUploadResult,
    ) -> AlfrescoVersionSnapshot:
        if upload_result.version_id:
            try:
                direct_version = self.client.get_version(snapshot.node_id, upload_result.version_id)
                if direct_version.comment == self._build_comment(snapshot) and _is_version_after(direct_version.version_id, snapshot.verori):
                    return direct_version
            except SignatureVersionConflictError:
                pass

        current_node = self.client.get_node(snapshot.node_id)
        if current_node.version_label:
            try:
                current_version = self.client.get_version(snapshot.node_id, current_node.version_label)
                if current_version.comment == self._build_comment(snapshot) and _is_version_after(current_version.version_id, snapshot.verori):
                    return current_version
            except SignatureVersionConflictError:
                pass

        collected: list[AlfrescoVersionSnapshot] = []
        seen: set[str] = set()
        skip_count = 0
        page_size = int(getattr(self.client, "history_page_size", settings.ALFRESCO_HISTORY_PAGE_SIZE))
        max_pages = max(int(getattr(self.client, "max_history_pages", settings.ALFRESCO_MAX_HISTORY_PAGES)), int(self.reconcile_min_checks))
        for _ in range(max_pages):
            page = self.client.list_versions(snapshot.node_id, skip_count=skip_count, max_items=page_size)
            if not page:
                break
            for version in page:
                if version.version_id in seen:
                    continue
                seen.add(version.version_id)
                if version.comment == self._build_comment(snapshot) and _is_version_after(version.version_id, snapshot.verori):
                    collected.append(version)
            if len(page) < page_size:
                break
            skip_count += len(page)

        if len(collected) == 1:
            return collected[0]
        if len(collected) > 1:
            raise SignatureVersionConflictError("Se encontraron varias versiones remotas con el mismo comentario")
        raise SignatureRecoveryRequiredError("No fue posible reconciliar la version remota creada")

    def _reconcile_state_after_match(
        self,
        db: Session,
        snapshot: _PublicationSnapshot,
        version: AlfrescoVersionSnapshot,
        version_artifact: AlfrescoDownloadedArtifact,
    ) -> int:
        short_db = self._build_session_factory(db)()
        try:
            firma = short_db.scalars(select(DocFirma).where(DocFirma.firid == snapshot.firid)).first()
            if not firma:
                raise SignatureNotFoundError("Intento de firma no encontrado")
            if firma.estado == EstadoDocFirma.COMPLETADA.value:
                return int(firma.revnum)
            current_revnum = int(firma.revnum)
            if firma.estado == EstadoDocFirma.SUBIENDO.value:
                current_revnum = signature_service.mark_uploaded(
                    db=short_db,
                    firid=snapshot.firid,
                    expected_revnum=current_revnum,
                    verfin=version.version_id,
                    result_data=ResultContract(
                        schema_ver=1,
                        fase=ResultFase.PUBLICACION,
                        remcod=200,
                        remmsg="Reconciled",
                        recint=0,
                        verchk=False,
                        haschk=False,
                        flags=[ResultFlag.ALFRESCO_OK],
                    ),
                    usrmod=snapshot.usrmod,
                )
            if firma.estado in (EstadoDocFirma.SUBIENDO.value, EstadoDocFirma.CARGADA.value):
                if firma.estado != EstadoDocFirma.CARGADA.value:
                    short_db.commit()
                    short_db.close()
                    short_db = self._build_session_factory(db)()
                    firma = short_db.scalars(select(DocFirma).where(DocFirma.firid == snapshot.firid)).first()
                    if not firma:
                        raise SignatureNotFoundError("Intento de firma no encontrado")
                    current_revnum = int(firma.revnum)
                current_revnum = signature_service.mark_verification_started(
                    db=short_db,
                    firid=snapshot.firid,
                    expected_revnum=current_revnum,
                    usrmod=snapshot.usrmod,
                )
            current_revnum = self._persist_verification_evidence(db, snapshot, current_revnum, AlfrescoUploadResult(
                node_id=snapshot.node_id,
                version_id=version.version_id,
                status_code=200,
                etag=version_artifact.path.name,
                remote_message="Reconciled",
            ))
            self._finalize_verified_signature(db, snapshot, current_revnum)
            self._record_recovery_event(db, snapshot, f"Reconcilied remote version {version.version_id}")
            return current_revnum
        except Exception:
            short_db.rollback()
            raise
        finally:
            short_db.close()

    def _upload_and_verify_snapshot(
        self,
        db: Session,
        snapshot: _PublicationSnapshot,
        *,
        cleanup_generated_on_success: bool,
        precondition: str | None = None,
    ) -> AlfrescoUploadResult:
        if not precondition:
            raise SignatureConcurrencyError("No fue posible obtener la precondicion ETag del nodo remoto")

        generated_pdf_path: Path | None = snapshot.generated_path
        version_pdf_path: Path | None = None
        conflict_recorded = False
        conflict_revnum = snapshot.revnum
        completed = False
        try:
            upload_revnum = self._mark_upload_started(db, snapshot, snapshot.revnum)
            conflict_revnum = upload_revnum
            self._record_publication_event(db, snapshot, "PUBLICATION_STARTED", "Publicacion enviada a Alfresco")
            upload_result = self.client.update_content_as_new_version(
                node_id=snapshot.node_id,
                source_path=snapshot.generated_path,
                major_version=self.major_version,
                comment=self._build_comment(snapshot),
                precondition=precondition,
            )

            if upload_result.status_code in (401, 403):
                raise SignatureUploadError("Alfresco rechazo la publicacion")

            if upload_result.status_code in (409, 412):
                self._mark_conflict(
                    db,
                    snapshot,
                    upload_revnum,
                    "REMOTE_VERSION_CONFLICT",
                    ResultContract(
                        schema_ver=1,
                        fase=ResultFase.PUBLICACION,
                        remcod=upload_result.status_code,
                        remmsg=upload_result.remote_message,
                        recint=0,
                        verchk=False,
                        haschk=False,
                        flags=[ResultFlag.VERSION_CONFLICT],
                    ),
                )
                conflict_recorded = True
                self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", "Alfresco reporto conflicto durante el PUT")
                raise SignatureVersionConflictError("Alfresco reporto un conflicto de version durante la publicacion")

            if upload_result.status_code == 429:
                raise SignatureRecoveryRequiredError("Se requiere reconciliacion despues de la limitacion de Alfresco")

            if upload_result.status_code >= 500:
                raise SignatureRecoveryRequiredError("Se requiere reconciliacion despues de un fallo remoto")

            resolved_version = self._resolve_uploaded_version(snapshot, upload_result)
            upload_revnum = self._mark_uploaded(db, snapshot, upload_revnum, AlfrescoUploadResult(
                node_id=upload_result.node_id,
                version_id=resolved_version.version_id,
                status_code=upload_result.status_code,
                etag=upload_result.etag,
                remote_message=upload_result.remote_message,
            ))
            upload_revnum = self._mark_verification_started(db, snapshot, upload_revnum)

            version_pdf_path = self.artifact_service.create_path(prefix=f"alfresco-{snapshot.firid}-version-", suffix=".pdf")
            version_artifact = self.client.download_version_content(snapshot.node_id, resolved_version.version_id, version_pdf_path)

            if version_artifact.sha256.lower() != snapshot.hasfin.lower():
                self._mark_conflict(
                    db,
                    snapshot,
                    upload_revnum,
                    "REMOTE_HASH_MISMATCH",
                    ResultContract(
                        schema_ver=1,
                        fase=ResultFase.VERIFICACION,
                        remcod=upload_result.status_code,
                        remmsg=upload_result.remote_message,
                        recint=0,
                        verchk=False,
                        haschk=False,
                        flags=[ResultFlag.ALFRESCO_OK, ResultFlag.HASH_MISMATCH],
                    ),
                )
                conflict_recorded = True
                raise SignatureIntegrityError("El hash remoto no coincide con hasfin")

            upload_revnum = self._persist_verification_evidence(db, snapshot, upload_revnum, AlfrescoUploadResult(
                node_id=upload_result.node_id,
                version_id=resolved_version.version_id,
                status_code=upload_result.status_code,
                etag=upload_result.etag,
                remote_message=upload_result.remote_message,
            ))
            self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_VERIFIED", f"Version remota verificada {resolved_version.version_id}")
            self._finalize_verified_signature(db, snapshot, upload_revnum)
            self._record_publication_event(db, snapshot, "PUBLICATION_COMPLETED", f"Publicacion completada {resolved_version.version_id}")
            completed = True
            return AlfrescoUploadResult(
                node_id=upload_result.node_id,
                version_id=resolved_version.version_id,
                status_code=upload_result.status_code,
                etag=upload_result.etag,
                remote_message=upload_result.remote_message,
            )
        except SignatureRecoveryRequiredError as exc:
            if isinstance(exc.__cause__, httpx.TimeoutException):
                self._record_publication_event(db, snapshot, "PUBLICATION_TIMEOUT", "Timeout durante la publicacion")
            self._record_publication_event(db, snapshot, "PUBLICATION_RECONCILIATION_REQUIRED", "Se requiere reconciliacion de la publicacion")
            self._record_recovery_event(db, snapshot, "Se requiere reconciliacion de la publicacion")
            raise
        except httpx.TimeoutException:
            self._record_publication_event(db, snapshot, "PUBLICATION_TIMEOUT", "Timeout durante la publicacion")
            self._record_recovery_event(db, snapshot, "Se requiere reconciliacion por timeout")
            raise SignatureRecoveryRequiredError("Se requiere reconciliacion despues de timeout")
        except SignatureVersionConflictError:
            self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", "Conflicto remoto durante la publicacion")
            if not conflict_recorded:
                try:
                    self._mark_conflict(
                        db,
                        snapshot,
                        conflict_revnum,
                        "REMOTE_VERSION_CONFLICT",
                        ResultContract(
                            schema_ver=1,
                            fase=ResultFase.PUBLICACION,
                            remcod=409,
                            remmsg="Version conflict",
                            recint=0,
                            verchk=False,
                            haschk=False,
                            flags=[ResultFlag.VERSION_CONFLICT],
                        ),
                    )
                except Exception:
                    pass
            raise
        except SignatureConcurrencyError:
            raise
        finally:
            if completed and cleanup_generated_on_success:
                self._cleanup_path(generated_pdf_path)
            self._cleanup_path(version_pdf_path)

    def publish_generated_signature(
        self,
        db: Session,
        firid: int,
        expected_revnum: int,
        expected_participant_verlock: int,
    ) -> AlfrescoUploadResult:
        if not self.test_node_id:
            raise SignatureUploadError("No existe un nodo de prueba autorizado para publicar")

        snapshot = self._load_publication_snapshot(db, firid, expected_revnum, expected_participant_verlock)
        current_pdf_path: Path | None = None
        try:
            _validation, remote_node, current_artifact = self._run_publication_preflight(db, snapshot)
            current_pdf_path = current_artifact.path
            precondition = current_artifact.etag or remote_node.etag
            if not self.write_enabled:
                self._record_publication_event(db, snapshot, "PUBLICATION_BLOCKED_WRITE_DISABLED", "Publicacion bloqueada por interruptor")
                raise SignatureWriteDisabledError(operation_id=str(snapshot.opeid), source_version=snapshot.verori)
            if not precondition:
                self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", "Falta precondicion ETag remota")
                raise SignatureConcurrencyError("No fue posible obtener la precondicion ETag del nodo remoto")
            return self._upload_and_verify_snapshot(db, snapshot, cleanup_generated_on_success=True, precondition=precondition)
        finally:
            self._cleanup_path(current_pdf_path)

    def publish_document(
        self,
        db: Session,
        docid: int,
        actor_user: str,
        iporig: str | None = None,
        user_agent: str | None = None,
    ) -> PublicationOutcome:
        snapshot = self._load_document_publication_snapshot(db, docid, actor_user, iporig, user_agent)
        current_pdf_path: Path | None = None
        try:
            _validation, remote_node, current_artifact = self._run_publication_preflight(db, snapshot)
            current_pdf_path = current_artifact.path
            precondition = current_artifact.etag or remote_node.etag
            if not self.write_enabled:
                self._record_publication_event(db, snapshot, "PUBLICATION_BLOCKED_WRITE_DISABLED", "Publicacion bloqueada por interruptor")
                raise SignatureWriteDisabledError(operation_id=str(snapshot.opeid), source_version=snapshot.verori)
            if not precondition:
                self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", "Falta precondicion ETag remota")
                self._raise_publication(snapshot, "No fue posible obtener la precondicion ETag del nodo remoto", code="REMOTE_PRECONDITION_MISSING")

            upload = self._upload_and_verify_snapshot(db, snapshot, cleanup_generated_on_success=True, precondition=precondition)
            return PublicationOutcome(
                status="PUBLISHED",
                operation_id=str(snapshot.opeid),
                source_version=snapshot.verori,
                publication_status="PUBLISHED",
                message="Publicacion verificada en Alfresco",
                final_version=upload.version_id,
                final_hash_short=self._short_hash(snapshot.hasfin),
            )
        except SignatureWriteDisabledError:
            raise
        except SignaturePublicationError:
            raise
        except SignatureConcurrencyError as exc:
            self._raise_publication(snapshot, str(exc), code="REMOTE_PRECONDITION_MISSING")
        except SignatureVersionConflictError as exc:
            self._raise_publication(snapshot, str(exc), code="REMOTE_VERSION_CONFLICT")
        except SignatureIntegrityError as exc:
            self._raise_publication(snapshot, str(exc), code="REMOTE_HASH_CONFLICT")
        except SignatureRecoveryRequiredError as exc:
            raise SignaturePublicationError(
                str(exc),
                code="PUBLICATION_RECONCILIATION_REQUIRED",
                status_code=409,
                operation_id=str(snapshot.opeid),
                source_version=snapshot.verori,
                publication_status="RECOVERY_REQUIRED",
            ) from exc
        except SignaturePayloadError as exc:
            raise SignaturePublicationError(
                str(exc),
                code="PUBLICATION_PAYLOAD_INVALID",
                status_code=422,
                operation_id=str(snapshot.opeid),
                source_version=snapshot.verori,
            ) from exc
        except SignatureUploadError as exc:
            raise SignaturePublicationError(
                str(exc),
                code="ALFRESCO_REMOTE_ERROR",
                status_code=409,
                operation_id=str(snapshot.opeid),
                source_version=snapshot.verori,
            ) from exc
        finally:
            self._cleanup_path(current_pdf_path)

    def reconcile_signature_attempt(self, db: Session, firid: int) -> AlfrescoVersionSnapshot | None:
        session_factory = self._build_session_factory(db)
        read_db = session_factory()
        try:
            firma = read_db.scalars(select(DocFirma).where(DocFirma.firid == firid)).first()
            if not firma:
                raise SignatureNotFoundError("Intento de firma no encontrado")
            if firma.estado == EstadoDocFirma.COMPLETADA.value:
                return None

            part = read_db.scalars(select(DocPart).where(DocPart.parid == firma.parid)).first()
            doc = read_db.scalars(select(DocFir).where(DocFir.docid == firma.docid)).first()
            if not part or not doc:
                raise SignatureNotFoundError("No fue posible reconstruir el contexto de reconciliacion")

            snapshot = _PublicationSnapshot(
                firid=firma.firid,
                docid=firma.docid,
                parid=firma.parid,
                node_id=doc.nodid,
                opeid=firma.opeid,
                revnum=int(firma.revnum),
                verori=firma.verori,
                hasori=firma.hasori,
                hasfin=firma.hasfin or "",
                docnom=doc.docnom,
                mimtip=doc.mimtip,
                participant_nomcom=part.nomcom,
                participant_rolpro=part.rolpro or "",
                participant_verlock=int(part.verlock),
                usrmod=self._default_usrmod(firma),
                generated_path=self.artifact_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf") or (self.artifact_service.ensure_base_dir() / f"missing-{firid}.pdf"),
            )
        finally:
            read_db.close()

        if not self.test_node_id or snapshot.node_id != self.test_node_id:
            raise SignatureUploadError("El nodo remoto no coincide con el nodo de prueba autorizado")
        if not snapshot.hasfin:
            raise SignaturePayloadError("No existe hash final para reconciliar")

        current_revnum = snapshot.revnum
        current_version: AlfrescoVersionSnapshot | None = None
        version_pdf_path: Path | None = None
        reconciled = False
        try:
            dummy_upload = AlfrescoUploadResult(
                node_id=snapshot.node_id,
                version_id=None,
                status_code=200,
                etag=None,
                remote_message="Recovery",
            )

            if firma.estado == EstadoDocFirma.SUBIENDO.value:
                current_version = self._resolve_uploaded_version(snapshot, dummy_upload)
                current_revnum = self._mark_uploaded(
                    db,
                    snapshot,
                    current_revnum,
                    AlfrescoUploadResult(
                        node_id=snapshot.node_id,
                        version_id=current_version.version_id,
                        status_code=200,
                        etag=None,
                        remote_message="Reconciled",
                    ),
                )
                current_revnum = self._mark_verification_started(db, snapshot, current_revnum)
            elif firma.estado == EstadoDocFirma.CARGADA.value:
                if firma.verfin:
                    current_version = self.client.get_version(snapshot.node_id, firma.verfin)
                else:
                    current_version = self._resolve_uploaded_version(snapshot, dummy_upload)
                current_revnum = self._mark_verification_started(db, snapshot, current_revnum)
            elif firma.estado == EstadoDocFirma.VERIFICANDO.value:
                if firma.verfin:
                    current_version = self.client.get_version(snapshot.node_id, firma.verfin)
                else:
                    current_version = self._resolve_uploaded_version(snapshot, dummy_upload)
            else:
                raise SignatureStateError(f"No es posible reconciliar la firma desde el estado {firma.estado}")

            if current_version is None:
                raise SignatureRecoveryRequiredError("No fue posible localizar la version remota")

            version_pdf_path = self.artifact_service.create_path(prefix=f"alfresco-{snapshot.firid}-reco-", suffix=".pdf")
            version_artifact = self.client.download_version_content(snapshot.node_id, current_version.version_id, version_pdf_path)
            if version_artifact.sha256.lower() != snapshot.hasfin.lower():
                self._mark_conflict(
                    db,
                    snapshot,
                    current_revnum,
                    "REMOTE_HASH_MISMATCH",
                    ResultContract(
                        schema_ver=1,
                        fase=ResultFase.RECONCILIACION,
                        remcod=200,
                        remmsg="Hash mismatch",
                        recint=0,
                        verchk=False,
                        haschk=False,
                        flags=[ResultFlag.ALFRESCO_OK, ResultFlag.HASH_MISMATCH],
                    ),
                )
                raise SignatureIntegrityError("El hash remoto no coincide con hasfin")

            current_revnum = self._persist_verification_evidence(
                db,
                snapshot,
                current_revnum,
                AlfrescoUploadResult(
                    node_id=snapshot.node_id,
                    version_id=current_version.version_id,
                    status_code=200,
                    etag=None,
                    remote_message="Reconciled",
                ),
            )
            self._finalize_verified_signature(db, snapshot, current_revnum)
            self._record_publication_event(db, snapshot, "PUBLICATION_RECONCILED", f"Reconciliacion completada {current_version.version_id}")
            self._record_recovery_event(db, snapshot, f"Reconciled remote version {current_version.version_id}")
            reconciled = True
            return current_version
        except SignatureVersionConflictError:
            self._mark_conflict(
                db,
                snapshot,
                current_revnum,
                "DUPLICATE_REMOTE_VERSION",
                ResultContract(
                    schema_ver=1,
                    fase=ResultFase.RECONCILIACION,
                    remcod=409,
                    remmsg="Duplicate remote version",
                    recint=0,
                    verchk=False,
                    haschk=False,
                    flags=[ResultFlag.VERSION_CONFLICT],
                ),
            )
            self._record_publication_event(db, snapshot, "PUBLICATION_REMOTE_CONFLICT", "Se detecto duplicidad de versiones remotas")
            self._record_recovery_event(db, snapshot, "Se detecto duplicidad de versiones remotas")
            raise
        except SignatureRecoveryRequiredError:
            self._record_publication_event(db, snapshot, "PUBLICATION_RECONCILIATION_REQUIRED", "No se encontro una version remota unica para reconciliar")
            self._record_recovery_event(db, snapshot, "Se requiere mas reconciliacion")
            raise
        finally:
            if reconciled:
                self._cleanup_path(snapshot.generated_path)
            self._cleanup_path(version_pdf_path)


alfresco_client_protocol = AlfrescoClientProtocol
