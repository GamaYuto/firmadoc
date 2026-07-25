from typing import Optional
from pydantic import BaseModel

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
    def resolve_user(self, usrid: str) -> IdentitySnapshot:
        raise NotImplementedError()

class FakeIdentityResolver(IdentityResolver):
    def resolve_user(self, usrid: str) -> IdentitySnapshot:
        # Mock para pruebas: Acepta cualquier usrid y genera datos consistentes
        if not usrid or not usrid.strip():
            raise IdentityResolutionError("ID de usuario no puede estar vacío")
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

class ProductionIdentityResolver(IdentityResolver):
    def resolve_user(self, usrid: str) -> IdentitySnapshot:
        # Falla por diseño hasta que se implemente un proveedor real
        raise IdentityResolutionError(
            "No se ha inyectado un proveedor de identidad real (Active Directory, LDAP, etc.). "
            "FirmaDoc actualmente no posee un directorio de usuarios para resolver participantes."
        )

# Factory para inyección de dependencias
def get_identity_resolver(is_testing: bool = False) -> IdentityResolver:
    if is_testing:
        return FakeIdentityResolver()
    return ProductionIdentityResolver()
