import httpx
from uuid import UUID
import os
import tempfile
import hashlib
from typing import Tuple
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
        self.base_url = f"{settings.ALFRESCO_BASE_URL.rstrip('/')}{settings.ALFRESCO_API_URL}"
        self.auth = (settings.ALFRESCO_USER, settings.ALFRESCO_PASSWORD)
        self.timeout = settings.ALFRESCO_TIMEOUT_SECONDS
        self.verify = settings.ALFRESCO_VERIFY_SSL
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
