import httpx
try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - fallback for older installs
    import fitz  # type: ignore[no-redef]
import ssl
from uuid import UUID
import os
import tempfile
import hashlib
from typing import Tuple
from pathlib import Path
from app.core.config import settings
from app.schemas.alfresco import NodeMetadata
import logging

logger = logging.getLogger(__name__)

class AlfrescoError(Exception): pass
class AlfrescoConnectionError(AlfrescoError): pass
class AlfrescoAuthenticationError(AlfrescoError): pass
class AlfrescoNotFoundError(AlfrescoError): pass
class AlfrescoPermissionError(AlfrescoError): pass
class AlfrescoInvalidContentError(AlfrescoError): pass
class AlfrescoDownloadLimitError(AlfrescoError): pass
class AlfrescoUnsupportedTypeError(AlfrescoError): pass

class AlfrescoClient:
    def __init__(self):
        api_path = settings.ALFRESCO_API_PATH or settings.ALFRESCO_API_URL or "/alfresco/api/-default-/public/alfresco/versions/1"
        username = settings.ALFRESCO_USERNAME or settings.ALFRESCO_USER
        if not username:
            raise AlfrescoAuthenticationError("Usuario de Alfresco no configurado")
        self.base_url = f"{settings.ALFRESCO_BASE_URL.rstrip('/')}{api_path}"
        self.auth = (username, settings.ALFRESCO_PASSWORD)
        self.timeout = settings.ALFRESCO_TIMEOUT_SECONDS

        ca_bundle = getattr(settings, "ALFRESCO_CA_BUNDLE", None)

        if ca_bundle:
            ca_bundle_path = Path(ca_bundle)
            if not ca_bundle_path.exists():
                raise AlfrescoConnectionError("La CA configurada para Alfresco no existe")
            if getattr(settings, "FIRMADOC_LAB_IDENTITY_ENABLED", False):
                ctx = ssl.create_default_context(cafile=str(ca_bundle_path))
                ctx.check_hostname = False
                self.verify = ctx
            else:
                self.verify = str(ca_bundle_path)
        else:
            self.verify = True
        self.max_size = settings.ALFRESCO_MAX_DOWNLOAD_MB * 1024 * 1024


    def _handle_error(self, exc: httpx.HTTPError):
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 401:
                raise AlfrescoAuthenticationError("Error de autenticación con Alfresco")
            elif status == 403:
                raise AlfrescoPermissionError("Permiso denegado en Alfresco")
            elif status == 404:
                raise AlfrescoNotFoundError("Nodo no encontrado en Alfresco")
            elif status == 413:
                raise AlfrescoDownloadLimitError("El archivo excede el tamaño límite permitido")
            else:
                raise AlfrescoError(f"Error HTTP {status} al contactar Alfresco")
        elif isinstance(exc, httpx.TimeoutException):
            raise AlfrescoConnectionError("Timeout al contactar Alfresco")
        else:
            raise AlfrescoConnectionError(f"Error de conexión con Alfresco: {str(exc)}")

    async def get_node_metadata(self, node_id: UUID) -> NodeMetadata:
        url = f"{self.base_url}/nodes/{node_id}"
        async with httpx.AsyncClient(auth=self.auth, timeout=self.timeout, verify=self.verify) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json().get("entry", {})
                
                content = data.get("content", {})
                props = data.get("properties", {})
                
                mod_at = data.get("modifiedAt")
                if not mod_at and props.get("cm:modified"):
                    mod_at = props.get("cm:modified")
                
                mod_by = data.get("modifiedByUser", {}).get("id")
                if not mod_by:
                    mod_by = props.get("cm:modifier")
                
                return NodeMetadata(
                    node_id=data.get("id"),
                    name=data.get("name", ""),
                    node_type=data.get("nodeType", ""),
                    is_file=data.get("isFile", False),
                    mime_type=content.get("mimeType"),
                    size_bytes=content.get("sizeInBytes", 0),
                    modified_at=mod_at,
                    modified_by=mod_by,
                    version_label=props.get("cm:versionLabel"),
                    parent_id=data.get("parentId")
                )
            except httpx.HTTPError as e:
                self._handle_error(e)

    async def download_node_content(self, node_id: UUID) -> Tuple[str, int, str]:
        url = f"{self.base_url}/nodes/{node_id}/content"
        
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        
        downloaded_size = 0
        hasher = hashlib.sha256()
        is_first_chunk = True
        
        client = httpx.AsyncClient(auth=self.auth, timeout=self.timeout, verify=self.verify)
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                
                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if not chunk:
                            continue
                            
                        if is_first_chunk:
                            if not chunk.startswith(b"%PDF-"):
                                raise AlfrescoInvalidContentError("El contenido descargado no es un PDF válido")
                            is_first_chunk = False
                            
                        downloaded_size += len(chunk)
                        if downloaded_size > self.max_size:
                            raise AlfrescoDownloadLimitError(f"El archivo excede el límite de {settings.ALFRESCO_MAX_DOWNLOAD_MB}MB")
                            
                        hasher.update(chunk)
                        f.write(chunk)

            try:
                with open(temp_path, "rb") as pdf_file:
                    pdf_bytes = pdf_file.read()
                with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
                    if not document.is_pdf or document.needs_pass or document.page_count <= 0:
                        raise AlfrescoInvalidContentError("El contenido descargado no es un PDF válido")
            except AlfrescoInvalidContentError:
                raise
            except Exception as exc:
                raise AlfrescoInvalidContentError("El contenido descargado no es un PDF válido") from exc

            final_hash = hasher.hexdigest()
            return temp_path, downloaded_size, final_hash
        except Exception as e:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            
            if isinstance(e, AlfrescoError):
                raise e
            if isinstance(e, httpx.HTTPError):
                self._handle_error(e)
            raise AlfrescoError(f"Error inesperado al descargar: {str(e)}")
        finally:
            await client.aclose()

    async def list_folder_children(self, folder_id: str = "-my-", skip_count: int = 0, max_items: int = 100) -> dict:
        url = f"{self.base_url}/nodes/{folder_id}/children"
        params = {
            "skipCount": skip_count,
            "maxItems": max_items,
            "include": "properties,path",
        }
        async with httpx.AsyncClient(auth=self.auth, timeout=self.timeout, verify=self.verify) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json().get("list", {})
                entries = data.get("entries", [])

                folders = []
                documents = []
                for item in entries:
                    entry = item.get("entry", {})
                    props = entry.get("properties", {})
                    content = entry.get("content", {})
                    info = {
                        "id": entry.get("id"),
                        "name": entry.get("name", ""),
                        "modified_at": entry.get("modifiedAt") or props.get("cm:modified"),
                        "modified_by": entry.get("modifiedByUser", {}).get("displayName") or entry.get("modifiedByUser", {}).get("id") or props.get("cm:modifier"),
                    }
                    if entry.get("isFolder"):
                        folders.append(info)
                    elif entry.get("isFile"):
                        info["size_bytes"] = content.get("sizeInBytes", 0)
                        info["mime_type"] = content.get("mimeType", "")
                        info["version_label"] = props.get("cm:versionLabel") or "1.0"
                        if info["mime_type"] == "application/pdf" or info["name"].lower().endswith(".pdf"):
                            documents.append(info)

                return {
                    "folder_id": folder_id,
                    "folders": sorted(folders, key=lambda x: x["name"].lower()),
                    "documents": sorted(documents, key=lambda x: x["name"].lower()),
                    "pagination": data.get("pagination", {}),
                }
            except httpx.HTTPError as e:
                self._handle_error(e)

    async def search_documents(self, term: str, max_items: int = 50) -> list[dict]:
        term_clean = term.strip()
        if not term_clean:
            return []
        url = f"{self.base_url}/queries/nodes"
        params = {
            "term": term_clean,
            "nodeType": "cm:content",
            "maxItems": max_items,
            "include": "properties,path",
        }
        async with httpx.AsyncClient(auth=self.auth, timeout=self.timeout, verify=self.verify) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json().get("list", {})
                entries = data.get("entries", [])

                results = []

                for item in entries:
                    entry = item.get("entry", {})
                    props = entry.get("properties", {})
                    content = entry.get("content", {})
                    name = entry.get("name", "")
                    mime = content.get("mimeType", "")
                    if mime == "application/pdf" or name.lower().endswith(".pdf"):
                        results.append({
                            "id": entry.get("id"),
                            "name": name,
                            "size_bytes": content.get("sizeInBytes", 0),
                            "mime_type": mime,
                            "version_label": props.get("cm:versionLabel") or "1.0",
                            "modified_at": entry.get("modifiedAt") or props.get("cm:modified"),
                            "modified_by": entry.get("modifiedByUser", {}).get("displayName") or entry.get("modifiedByUser", {}).get("id"),
                            "path": entry.get("path", {}).get("name") if isinstance(entry.get("path"), dict) else None,
                        })
                return results
            except httpx.HTTPError as e:
                self._handle_error(e)
