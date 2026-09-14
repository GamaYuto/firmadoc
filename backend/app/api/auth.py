from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from app.core.config import settings
from app.core.identity import FakeIdentityResolver, IdentityResolutionError
from app.core.security import (
    AuthenticatedPrincipal,
    clear_session_cookie,
    get_current_principal,
    set_session_cookie,
)
from app.schemas.auth import SessionCreate, SessionResponse

router = APIRouter()
_resolver = FakeIdentityResolver()


@router.post("/session", response_model=SessionResponse)
def create_session(
    payload: SessionCreate,
    response: Response,
) -> SessionResponse:
    """Mecanismo de simulacion de identidad exclusivo para entorno de laboratorio/pruebas.
    No es un mecanismo de autenticacion corporativo. En produccion falla cerrado (503).
    """
    if not settings.FIRMADOC_LAB_IDENTITY_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Identidad de laboratorio deshabilitada; configure proveedor de identidad corporativo",
        )

    user_raw = payload.user_id.strip()
    if not user_raw:
        raise HTTPException(status_code=400, detail="El identificador de usuario no puede estar vacio")

    try:
        snapshot = _resolver.resolve_user(user_raw)
    except IdentityResolutionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    u_low = snapshot.usrid.lower()
    if "admin" in u_low:
        roles = ("ADMIN", "GESTOR")
    elif "gestor" in u_low or "preparador" in u_low:
        roles = ("GESTOR",)
    else:
        roles = ("EMPLEADO",)

    principal = AuthenticatedPrincipal(
        user_id=snapshot.usrid,
        nombre_completo=snapshot.nomcom,
        correo=snapshot.correo,
        roles=roles,
        is_authenticated=True,
    )

    set_session_cookie(response, principal)

    return SessionResponse(
        user_id=principal.user_id,
        nombre_completo=principal.nombre_completo,
        correo=principal.correo,
        roles=list(principal.roles),
        is_authenticated=True,
        csrf_token=principal.csrf_token,
    )


@router.post("/logout")
def logout(
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> dict:
    clear_session_cookie(response)
    return {"message": "Sesion finalizada"}


@router.get("/me", response_model=SessionResponse)
def get_current_user_info(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> SessionResponse:
    return SessionResponse(
        user_id=principal.user_id,
        nombre_completo=principal.nombre_completo,
        correo=principal.correo,
        roles=list(principal.roles),
        is_authenticated=principal.is_authenticated,
        csrf_token=principal.csrf_token,
    )
