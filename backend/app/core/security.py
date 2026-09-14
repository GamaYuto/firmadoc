from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Callable, Optional, Sequence
from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, Request, Response
from app.core.config import settings


class AuthenticatedPrincipal(BaseModel):
    user_id: str
    nombre_completo: str
    correo: str
    roles: tuple[str, ...] = Field(default_factory=tuple)
    is_authenticated: bool = True
    csrf_token: Optional[str] = None

    def has_role(self, role: str) -> bool:
        r_upper = role.upper()
        return any(r.upper() == r_upper for r in self.roles)

    @property
    def is_admin(self) -> bool:
        return self.has_role("ADMIN") or self.has_role("ADMINISTRADOR")

    @property
    def is_gestor(self) -> bool:
        return self.is_admin or self.has_role("GESTOR")


def create_session_token(
    principal: AuthenticatedPrincipal,
    expires_in_seconds: Optional[int] = None,
    secret_key: Optional[str] = None,
) -> str:
    key = (secret_key or settings.SECRET_KEY).encode("utf-8")
    ttl = expires_in_seconds if expires_in_seconds is not None else (settings.FIRMADOC_SESSION_EXPIRE_MINUTES * 60)
    exp = int(time.time()) + ttl
    if not principal.csrf_token:
        principal.csrf_token = secrets.token_urlsafe(32)
    payload = {
        "uid": principal.user_id.strip().lower(),
        "nom": principal.nombre_completo,
        "cor": principal.correo,
        "rol": list(principal.roles),
        "csrf": principal.csrf_token,
        "exp": exp,
    }
    raw_payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(raw_payload).decode("utf-8").rstrip("=")
    sig = hmac.new(key, b64_payload.encode("utf-8"), hashlib.sha256).digest()
    b64_sig = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{b64_payload}.{b64_sig}"


def decode_session_token(token: str, secret_key: Optional[str] = None) -> Optional[AuthenticatedPrincipal]:
    if not token or "." not in token:
        return None
    try:
        parts = token.split(".", 1)
        if len(parts) != 2:
            return None
        b64_payload, b64_sig = parts
        key = (secret_key or settings.SECRET_KEY).encode("utf-8")
        expected_sig = hmac.new(key, b64_payload.encode("utf-8"), hashlib.sha256).digest()

        rem = len(b64_sig) % 4
        padded_sig = b64_sig + ("=" * (4 - rem) if rem else "")
        sig = base64.urlsafe_b64decode(padded_sig.encode("utf-8"))

        if not hmac.compare_digest(sig, expected_sig):
            return None

        rem_p = len(b64_payload) % 4
        padded_p = b64_payload + ("=" * (4 - rem_p) if rem_p else "")
        raw_payload = base64.urlsafe_b64decode(padded_p.encode("utf-8"))
        payload = json.loads(raw_payload.decode("utf-8"))

        if int(payload.get("exp", 0)) < int(time.time()):
            return None

        uid = str(payload.get("uid", "")).strip().lower()
        if not uid:
            return None

        return AuthenticatedPrincipal(
            user_id=uid,
            nombre_completo=payload.get("nom", uid),
            correo=payload.get("cor", f"{uid}@firmadoc.local"),
            roles=tuple(str(r) for r in payload.get("rol", ())),
            is_authenticated=True,
            csrf_token=payload.get("csrf"),
        )
    except Exception:
        return None


def set_session_cookie(response: Response, principal: AuthenticatedPrincipal) -> str:
    token = create_session_token(principal)
    response.set_cookie(
        key=settings.FIRMADOC_SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.FIRMADOC_SESSION_EXPIRE_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=settings.FIRMADOC_SESSION_COOKIE_SECURE,
        path="/",
    )
    return token


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.FIRMADOC_SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.FIRMADOC_SESSION_COOKIE_SECURE,
    )


def get_current_principal(
    request: Request,
) -> AuthenticatedPrincipal:
    token: Optional[str] = request.cookies.get(settings.FIRMADOC_SESSION_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Autenticacion requerida. Inicie sesion para continuar.",
        )

    principal = decode_session_token(token)
    if not principal:
        raise HTTPException(
            status_code=401,
            detail="Sesion invalida o expirada. Inicie sesion nuevamente.",
        )

    # Verificación de CSRF para métodos mutables
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        csrf_header = request.headers.get("X-FirmaDoc-CSRF")
        if not csrf_header:
            raise HTTPException(status_code=403, detail="Token CSRF ausente")
        if not principal.csrf_token or not hmac.compare_digest(csrf_header, principal.csrf_token):
            raise HTTPException(status_code=403, detail="Token CSRF invalido")

    return principal


def require_roles(*allowed_roles: str) -> Callable[[AuthenticatedPrincipal], AuthenticatedPrincipal]:
    def role_checker(principal: AuthenticatedPrincipal = Depends(get_current_principal)) -> AuthenticatedPrincipal:
        if principal.is_admin:
            return principal
        for role in allowed_roles:
            if principal.has_role(role):
                return principal
        raise HTTPException(status_code=403, detail="Permisos insuficientes para esta operacion")

    return role_checker
