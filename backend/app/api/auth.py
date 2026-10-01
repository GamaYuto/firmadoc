from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from app.core.config import settings
from app.core.identity import get_identity_resolver, IdentityResolutionError
from app.core.security import (
    AuthenticatedPrincipal,
    clear_session_cookie,
    get_current_principal,
    set_session_cookie,
)
from app.core.sso import SsoAssertionError, SsoReplayStoreUnavailableError, verify_sso_assertion
from app.schemas.auth import SessionCreate, SessionResponse
import httpx

router = APIRouter()
_resolver = get_identity_resolver()

@router.post("/session", response_model=SessionResponse)
def create_session(
    payload: SessionCreate,
    response: Response,
) -> SessionResponse:
    if not settings.FIRMADOC_LAB_IDENTITY_ENABLED:
        raise HTTPException(status_code=403, detail="La selección de identidad LAB está deshabilitada")

    user_raw = payload.user_id.strip()
    if not user_raw:
        raise HTTPException(status_code=400, detail="El identificador de usuario no puede estar vacio")

    password = payload.password or ""

    try:
        snapshot = _resolver.resolve_user(user_raw, password)
    except IdentityResolutionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

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


@router.post("/sso/exchange")
async def exchange_sso_assertion(
    request: Request,
    response: Response,
    assertion: str | None = Form(default=None),
) -> RedirectResponse:
    if not settings.FIRMADOC_SSO_ENABLED:
        raise HTTPException(status_code=403, detail="SSO deshabilitado")

    if assertion is None:
        form = await request.form()
        assertion = form.get("assertion")

    if not assertion or not isinstance(assertion, str):
        raise HTTPException(status_code=400, detail="Aserción SSO ausente")

    try:
        claims = verify_sso_assertion(
            assertion,
            secret=settings.FIRMADOC_SSO_SECRET or "",
            issuer=settings.FIRMADOC_SSO_ISSUER,
            audience=settings.FIRMADOC_SSO_AUDIENCE,
            allowed_return_origins=(settings.FIRMADOC_ALLOWED_RETURN_ORIGINS or "").split(","),
            max_age_seconds=settings.FIRMADOC_SSO_MAX_AGE_SECONDS,
            clock_skew_seconds=settings.FIRMADOC_SSO_CLOCK_SKEW_SECONDS,
        )
    except SsoAssertionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SsoReplayStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Servicio SSO temporalmente no disponible") from exc

    user_id = str(claims.get("sub", "")).strip()
    resolver = get_identity_resolver()
    try:
        snapshot = resolver.resolve_user(user_id, password=None)
    except IdentityResolutionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if snapshot.usrid != user_id:
        raise HTTPException(status_code=401, detail="La identidad de Share no coincide con la del directorio")

    principal = AuthenticatedPrincipal(
        user_id=snapshot.usrid,
        nombre_completo=snapshot.nomcom,
        correo=snapshot.correo,
        roles=("EMPLEADO",),
        is_authenticated=True,
    )
    redirect_url = f"/iniciar?nodeId={quote(str(claims.get('nid', '')))}&returnUrl={quote(str(claims.get('ret', '')), safe='')}"
    redirect_response = RedirectResponse(url=redirect_url, status_code=303)
    set_session_cookie(redirect_response, principal)
    return redirect_response


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
