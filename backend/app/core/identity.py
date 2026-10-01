from typing import Optional
from pydantic import BaseModel
import httpx
from app.core.config import settings
import os

class IdentitySnapshot(BaseModel):
    usrid: str
    nomcom: str
    correo: str
    rolpro: Optional[str] = None
    origen: Optional[str] = None

class IdentityResolutionError(Exception):
    def __init__(self, message: str):
        super().__init__(message)

class IdentityResolver:
    def resolve_user(self, usrid: str, password: str = None) -> IdentitySnapshot:
        raise NotImplementedError()

class FakeIdentityResolver(IdentityResolver):
    def resolve_user(self, usrid: str, password: str = None) -> IdentitySnapshot:
        if not usrid or not usrid.strip():
            raise IdentityResolutionError("ID de usuario no puede estar vacio")
        canon_id = usrid.strip().lower()
        if canon_id == "unknown_user":
            raise IdentityResolutionError("Usuario no encontrado en el directorio falso")
        
        return IdentitySnapshot(
            usrid=canon_id,
            nomcom=f"Usuario {canon_id.capitalize()}",
            correo=f"{canon_id}@empresa.local",
            rolpro="Empleado",
            origen="FakeDirectory"
        )

class AlfrescoIdentityResolver(IdentityResolver):
    def resolve_user(self, usrid: str, password: str = None) -> IdentitySnapshot:
        canon_id = usrid.strip()
        if not canon_id:
            raise IdentityResolutionError("ID de usuario no puede estar vacio")
        
        api_path = (
            settings.ALFRESCO_API_PATH
            or settings.ALFRESCO_API_URL
            or "/alfresco/api/-default-/public/alfresco/versions/1"
        )
        url = f"{settings.ALFRESCO_BASE_URL.rstrip('/')}{api_path.rstrip('/')}/people/{canon_id}"
        if password is not None:
            auth = (canon_id, password)
        else:
            service_user = settings.ALFRESCO_USERNAME or settings.ALFRESCO_USER
            auth = (service_user, settings.ALFRESCO_PASSWORD) if service_user else None
        
        try:
            with httpx.Client(auth=auth, verify=settings.ALFRESCO_CA_BUNDLE if settings.ALFRESCO_VERIFY_SSL and settings.ALFRESCO_CA_BUNDLE else settings.ALFRESCO_VERIFY_SSL, timeout=settings.ALFRESCO_TIMEOUT_SECONDS) as client:
                resp = client.get(url)
                if resp.status_code in (401, 403):
                    raise IdentityResolutionError("Credenciales invalidas")
                if resp.status_code == 404:
                    raise IdentityResolutionError(f"Usuario {canon_id} no encontrado en Alfresco")
                resp.raise_for_status()
                data = resp.json().get("entry", {})
                
                nomcom = f"{data.get('firstName', '')} {data.get('lastName', '')}".strip() or canon_id
                correo = data.get("email", f"{canon_id}@empresa.local")
                rolpro = data.get("jobTitle") or "Empleado"
                
                return IdentitySnapshot(
                    usrid=canon_id,
                    nomcom=nomcom,
                    correo=correo,
                    rolpro=rolpro,
                    origen="Alfresco"
                )
        except IdentityResolutionError:
            raise
        except Exception as exc:
            raise IdentityResolutionError(f"Error al consultar Alfresco para {canon_id}: {str(exc)}") from exc

class ProductionIdentityResolver(IdentityResolver):
    def resolve_user(self, usrid: str, password: str = None) -> IdentitySnapshot:
        raise IdentityResolutionError(
            "No se ha inyectado un proveedor de identidad real (Active Directory, LDAP, etc.). "
            "FirmaDoc actualmente no posee un directorio de usuarios para resolver participantes."
        )

def get_identity_resolver(is_testing: bool = False) -> IdentityResolver:
    if is_testing or os.getenv("TESTING") == "1":
        return FakeIdentityResolver()
    return AlfrescoIdentityResolver()
