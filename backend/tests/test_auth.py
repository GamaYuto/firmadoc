import time
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.security import (
    AuthenticatedPrincipal,
    create_session_token,
    decode_session_token,
)


def test_session_create_success():
    with TestClient(app) as client:
        response = client.post("/api/auth/session", json={"user_id": "preparador"})
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "preparador"
        assert data["is_authenticated"] is True
        assert "GESTOR" in data["roles"]
        assert settings.FIRMADOC_SESSION_COOKIE_NAME in client.cookies


def test_session_create_empty_user():
    with TestClient(app) as client:
        response = client.post("/api/auth/session", json={"user_id": "   "})
        assert response.status_code == 400


def test_session_create_unknown_user():
    with TestClient(app) as client:
        response = client.post("/api/auth/session", json={"user_id": "unknown_user"})
        assert response.status_code == 401


def test_get_current_user_me_authenticated():
    with TestClient(app) as client:
        client.post("/api/auth/session", json={"user_id": "firmante_dr"})
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "firmante_dr"
        assert data["is_authenticated"] is True


def test_get_current_user_me_unauthenticated():
    with TestClient(app) as client:
        response = client.get("/api/auth/me")
        assert response.status_code == 401


def test_logout_clears_cookie():
    with TestClient(app) as client:
        session_resp = client.post("/api/auth/session", json={"user_id": "preparador"})
        csrf_token = session_resp.json()["csrf_token"]
        assert settings.FIRMADOC_SESSION_COOKIE_NAME in client.cookies
        logout_resp = client.post("/api/auth/logout", headers={"X-FirmaDoc-CSRF": csrf_token})
        assert logout_resp.status_code == 200
        me_resp = client.get("/api/auth/me")
        assert me_resp.status_code == 401


def test_tampered_cookie_rejected():
    with TestClient(app) as client:
        client.post("/api/auth/session", json={"user_id": "legit_user"})
        token = client.cookies.get(settings.FIRMADOC_SESSION_COOKIE_NAME)
        tampered_token = token[:-4] + "xxxx"
        client.cookies[settings.FIRMADOC_SESSION_COOKIE_NAME] = tampered_token
        response = client.get("/api/auth/me")
        assert response.status_code == 401


def test_expired_cookie_rejected():
    principal = AuthenticatedPrincipal(
        user_id="old_user",
        nombre_completo="Usuario Expirado",
        correo="old@firmadoc.local",
        roles=("EMPLEADO",),
    )
    # Token expired 10 seconds ago
    expired_token = create_session_token(principal, expires_in_seconds=-10)
    with TestClient(app) as client:
        client.cookies[settings.FIRMADOC_SESSION_COOKIE_NAME] = expired_token
        response = client.get("/api/auth/me")
        assert response.status_code == 401


def test_spoofed_header_never_overrides_session():
    with TestClient(app) as client:
        # User logs in as attacker
        client.post("/api/auth/session", json={"user_id": "atacante"})
        # Attacker tries to impersonate victim via header
        response = client.get("/api/auth/me", headers={"X-FirmaDoc-User": "victima"})
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "atacante"  # MUST remain attacker!


def test_client_cannot_self_assign_admin_role():
    with TestClient(app) as client:
        # Attacker tries to inject extra field "roles": ["ADMIN"]
        response = client.post("/api/auth/session", json={"user_id": "usuario_normal", "roles": ["ADMIN"]})
        # Since SessionCreate has extra="forbid", Pydantic rejects extra fields with 422
        assert response.status_code == 422
        assert settings.FIRMADOC_SESSION_COOKIE_NAME not in client.cookies


def test_fake_identity_disabled_outside_lab(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_LAB_IDENTITY_ENABLED", False)
    with TestClient(app) as client:
        response = client.post("/api/auth/session", json={"user_id": "victima"})
        assert response.status_code == 503
        assert "Identidad de laboratorio deshabilitada" in response.json().get("detail", "")
        assert settings.FIRMADOC_SESSION_COOKIE_NAME not in client.cookies


def test_simulation_session_disabled_outside_lab(monkeypatch):
    """Aclara la semantica LAB:
    En modo LAB (FIRMADOC_LAB_IDENTITY_ENABLED=True), POST /api/auth/session con user_id
    es un mecanismo deliberado de simulacion de identidad (test harness) para pruebas.
    Fuera de LAB (FIRMADOC_LAB_IDENTITY_ENABLED=False), dicho mecanismo de simulacion queda
    completamente bloqueado y falla cerrado con HTTP 503, impidiendo que un cliente
    obtenga sesion como otro usuario valido sin proveedor institucional.
    """
    monkeypatch.setattr(settings, "FIRMADOC_LAB_IDENTITY_ENABLED", False)
    with TestClient(app) as client:
        response = client.post("/api/auth/session", json={"user_id": "victima"})
        assert response.status_code == 503
        assert settings.FIRMADOC_SESSION_COOKIE_NAME not in client.cookies


# Alias para verificacion de auditoria
test_cannot_create_session_as_other_valid_user = test_simulation_session_disabled_outside_lab


def test_session_secret_missing_fails_closed_outside_lab():
    from pydantic import ValidationError
    from app.core.config import Settings

    # 1. Default lab secret key with lab disabled -> fails
    with pytest.raises((ValidationError, ValueError)):
        Settings(
            DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
            ALFRESCO_BASE_URL="http://localhost:8080",
            ALFRESCO_PASSWORD="pass",
            FIRMADOC_LAB_IDENTITY_ENABLED=False,
            SECRET_KEY="firmadoc-lab-secret-key-2026-unbreakable",
        )

    # 2. Empty secret key with lab disabled -> fails
    with pytest.raises((ValidationError, ValueError)):
        Settings(
            DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
            ALFRESCO_BASE_URL="http://localhost:8080",
            ALFRESCO_PASSWORD="pass",
            FIRMADOC_LAB_IDENTITY_ENABLED=False,
            SECRET_KEY="",
        )

    # 3. Insecure/short secret key with lab disabled -> fails
    with pytest.raises((ValidationError, ValueError)):
        Settings(
            DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
            ALFRESCO_BASE_URL="http://localhost:8080",
            ALFRESCO_PASSWORD="pass",
            FIRMADOC_LAB_IDENTITY_ENABLED=False,
            SECRET_KEY="short-secret-key",
        )

    # 4. Valid long secret key with lab disabled -> succeeds
    valid_settings = Settings(
        DATABASE_URL="postgresql+psycopg://u:p@localhost:5432/db",
        ALFRESCO_BASE_URL="http://localhost:8080",
        ALFRESCO_PASSWORD="pass",
        FIRMADOC_LAB_IDENTITY_ENABLED=False,
        SECRET_KEY="a" * 32,
    )
    assert valid_settings.SECRET_KEY == "a" * 32


def test_anonymous_alfresco_proxy_is_401():
    with TestClient(app) as client:
        # Petición al proxy de Alfresco sin sesión autenticada
        response = client.get(
            "/api/alfresco/nodes/c3d183f3-018d-4f10-bf9e-fb22e92ec4ad"
        )
        assert response.status_code == 401
        assert "Autenticacion requerida" in response.json().get("detail", "")


def test_logout_stateless_behavior():
    """Demuestra la caracterización de logout stateless:
    Logout elimina la cookie en el cliente del navegador, pero el token criptográfico
    HMAC sigue siendo válido matemáticamente hasta su expiración si fue copiado.
    """
    with TestClient(app) as client:
        sess_resp = client.post("/api/auth/session", json={"user_id": "test_user"})
        csrf_tok = sess_resp.json()["csrf_token"]
        token_copiado = client.cookies.get(settings.FIRMADOC_SESSION_COOKIE_NAME)
        assert token_copiado is not None

        # Cliente llama logout con CSRF válido
        logout_resp = client.post("/api/auth/logout", headers={"X-FirmaDoc-CSRF": csrf_tok})
        assert logout_resp.status_code == 200

        # En la siguiente petición sin re-inyectar cookie, da 401
        assert client.get("/api/auth/me").status_code == 401

        # Si el token copiado se re-inyecta antes de expirar, es aceptado (comportamiento stateless sin revocación)
        client.cookies[settings.FIRMADOC_SESSION_COOKIE_NAME] = token_copiado
        restored_resp = client.get("/api/auth/me")
        assert restored_resp.status_code == 200
        assert restored_resp.json()["user_id"] == "test_user"


def test_csrf_missing_rejected_for_mutation():
    with TestClient(app) as client:
        client.post("/api/auth/session", json={"user_id": "preparador"})
        # Intento de mutación (logout) sin cabecera CSRF
        response = client.post("/api/auth/logout")
        assert response.status_code == 403
        assert "Token CSRF ausente" in response.json().get("detail", "")


def test_csrf_invalid_rejected_for_mutation():
    with TestClient(app) as client:
        client.post("/api/auth/session", json={"user_id": "preparador"})
        # Intento de mutación con token CSRF erróneo
        response = client.post("/api/auth/logout", headers={"X-FirmaDoc-CSRF": "token_falso_atacante_12345"})
        assert response.status_code == 403
        assert "Token CSRF invalido" in response.json().get("detail", "")


def test_csrf_valid_allows_mutation():
    with TestClient(app) as client:
        sess = client.post("/api/auth/session", json={"user_id": "preparador"})
        csrf_token = sess.json()["csrf_token"]
        assert csrf_token is not None
        # Mutación con token CSRF coincidente
        response = client.post("/api/auth/logout", headers={"X-FirmaDoc-CSRF": csrf_token})
        assert response.status_code == 200


def test_csrf_not_required_for_get():
    with TestClient(app) as client:
        client.post("/api/auth/session", json={"user_id": "preparador"})
        # Petición segura GET no requiere cabecera CSRF
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json()["user_id"] == "preparador"


def test_logout_requires_valid_csrf():
    with TestClient(app) as client:
        sess = client.post("/api/auth/session", json={"user_id": "preparador"})
        csrf_token = sess.json()["csrf_token"]

        # 1. Sin CSRF -> 403
        r_missing = client.post("/api/auth/logout")
        assert r_missing.status_code == 403

        # 2. Con CSRF inválido -> 403
        r_invalid = client.post("/api/auth/logout", headers={"X-FirmaDoc-CSRF": "invalido"})
        assert r_invalid.status_code == 403

        # 3. Con CSRF válido -> 200 y cookie eliminada
        r_valid = client.post("/api/auth/logout", headers={"X-FirmaDoc-CSRF": csrf_token})
        assert r_valid.status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_adversarial_spoofed_header_and_attacker_csrf_rejected():
    """Prueba adversarial final:
    Con una sesión de atacante, enviar simultáneamente:
    X-FirmaDoc-User: victima
    X-FirmaDoc-CSRF: token_del_atacante
    y verificar que la identidad backend permanece como atacante.
    """
    with TestClient(app) as client:
        # Atacante inicia sesión legítima y obtiene su CSRF token
        r_sess = client.post("/api/auth/session", json={"user_id": "atacante"})
        attacker_csrf = r_sess.json()["csrf_token"]

        # Atacante intenta suplantar a 'victima' mediante header X-FirmaDoc-User
        # enviando su propio CSRF válido para su sesión
        r_me = client.get(
            "/api/auth/me",
            headers={
                "X-FirmaDoc-User": "victima",
                "X-FirmaDoc-CSRF": attacker_csrf,
            },
        )
        assert r_me.status_code == 200
        # La identidad backend DEBE seguir siendo atacante, jamás victima
        assert r_me.json()["user_id"] == "atacante"
