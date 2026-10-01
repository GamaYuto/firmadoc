import base64
import hashlib
import hmac
import json
import time
from uuid import uuid4
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.database import SessionLocal
import app.core.identity as identity_module
import app.api.auth as auth_module
import app.core.sso as sso_module
from app.models.sso_replay import SsoReplay
from app.core.security import (
    AuthenticatedPrincipal,
    create_session_token,
    decode_session_token,
)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_sso_assertion(**overrides):
    claims = {
        "iss": "alfresco-share",
        "aud": "firmadoc",
        "sub": "pdaza",
        "iat": int(time.time()) - 5,
        "exp": int(time.time()) + 30,
        "jti": uuid4().hex,
        "nid": "11111111-1111-4111-8111-111111111111",
        "ret": "https://alfresco-lab.test/share/page/document-details?nodeRef=workspace://SpacesStore/11111111-1111-4111-8111-111111111111",
    }
    claims.update(overrides)
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True)
    body = _b64url_encode(payload.encode("utf-8"))
    sig = hmac.new(settings.FIRMADOC_SSO_SECRET.encode("utf-8"), f"v1.{body}".encode("utf-8"), hashlib.sha256).digest()
    return f"v1.{body}.{_b64url_encode(sig)}"


def test_session_create_success(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_LAB_IDENTITY_ENABLED", True)
    with TestClient(app) as client:
        response = client.post("/api/auth/session", json={"user_id": "lab_user"})
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "lab_user"
        assert data["is_authenticated"] is True
        assert data["roles"] == ["EMPLEADO"]
        assert settings.FIRMADOC_SESSION_COOKIE_NAME in client.cookies


def test_session_create_rejected_when_lab_identity_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_LAB_IDENTITY_ENABLED", False)
    with TestClient(app) as client:
        response = client.post("/api/auth/session", json={"user_id": "lab_user"})
        assert response.status_code == 403
        assert settings.FIRMADOC_SESSION_COOKIE_NAME not in client.cookies


@pytest.mark.parametrize(
    ("password", "expected_auth"),
    [
        (None, ("firma_service", "service-password")),
        ("", ("lblanco", "")),
    ],
)
def test_alfresco_identity_resolver_uses_configured_api_path_and_auth(
    monkeypatch, password, expected_auth
):
    monkeypatch.setattr(settings, "ALFRESCO_BASE_URL", "https://alfresco.example")
    monkeypatch.setattr(
        settings,
        "ALFRESCO_API_PATH",
        "/alfresco/api/-default-/public/alfresco/versions/1",
    )
    monkeypatch.setattr(settings, "ALFRESCO_API_URL", None)
    monkeypatch.setattr(settings, "ALFRESCO_USER", "firma_service")
    monkeypatch.setattr(settings, "ALFRESCO_USERNAME", None)
    monkeypatch.setattr(settings, "ALFRESCO_PASSWORD", "service-password")

    calls = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"entry": {"firstName": "Luis", "lastName": "Blanco"}}

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *, auth, **kwargs):
            calls["auth"] = auth

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            calls["url"] = url
            return FakeResponse()

    monkeypatch.setattr(identity_module.httpx, "Client", FakeClient)

    snapshot = identity_module.AlfrescoIdentityResolver().resolve_user("lblanco", password)

    assert calls["url"] == (
        "https://alfresco.example/alfresco/api/-default-/public/alfresco/versions/1/people/lblanco"
    )
    assert calls["auth"] == expected_auth
    assert snapshot.nomcom == "Luis Blanco"


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


def test_sso_exchange_valid_assertion_sets_cookie_and_redirect(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ISSUER", "alfresco-share")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_AUDIENCE", "firmadoc")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_CLOCK_SKEW_SECONDS", 60)
    token = _make_sso_assertion()
    with TestClient(app) as client:
        monkeypatch.setattr(sso_module, "_SSO_REPLAY_STORE", sso_module.InMemoryReplayStore())
        response = client.post("/api/auth/sso/exchange", data={"assertion": token}, follow_redirects=False)
        assert response.status_code == 303
        assert settings.FIRMADOC_SESSION_COOKIE_NAME in client.cookies
        assert "/iniciar?" in response.headers["location"]
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user_id"] == "pdaza"


def test_sso_exchange_resolves_identity_without_user_password(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ISSUER", "alfresco-share")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_AUDIENCE", "firmadoc")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_CLOCK_SKEW_SECONDS", 60)
    resolver_calls = []
    monkeypatch.setattr(sso_module, "_SSO_REPLAY_STORE", sso_module.InMemoryReplayStore())

    class CapturingResolver:
        def resolve_user(self, user_id, password=None):
            resolver_calls.append((user_id, password))
            return identity_module.IdentitySnapshot(
                usrid=user_id,
                nomcom="Usuario Share",
                correo="pdaza@example.test",
            )

    monkeypatch.setattr(auth_module, "get_identity_resolver", lambda: CapturingResolver())
    token = _make_sso_assertion()
    with TestClient(app) as client:
        response = client.post("/api/auth/sso/exchange", data={"assertion": token}, follow_redirects=False)

    assert response.status_code == 303
    assert resolver_calls == [("pdaza", None)]


def test_sso_exchange_modified_signature_rejected(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ISSUER", "alfresco-share")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_AUDIENCE", "firmadoc")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_CLOCK_SKEW_SECONDS", 60)
    token = _make_sso_assertion()
    version, payload, signature = token.split(".")
    bad_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    bad = f"{version}.{payload}.{bad_signature}"
    with TestClient(app) as client:
        response = client.post("/api/auth/sso/exchange", data={"assertion": bad}, follow_redirects=False)
        assert response.status_code == 400


def test_sso_exchange_wrong_issuer_or_audience_rejected(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ISSUER", "alfresco-share")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_AUDIENCE", "firmadoc")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_CLOCK_SKEW_SECONDS", 60)
    with TestClient(app) as client:
        wrong_issuer = client.post("/api/auth/sso/exchange", data={"assertion": _make_sso_assertion(iss="otro-emisor")})
        assert wrong_issuer.status_code == 400
        wrong_aud = client.post("/api/auth/sso/exchange", data={"assertion": _make_sso_assertion(aud="otro-aud")})
        assert wrong_aud.status_code == 400


def test_sso_exchange_expired_and_too_old_rejected(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ISSUER", "alfresco-share")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_AUDIENCE", "firmadoc")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_CLOCK_SKEW_SECONDS", 60)
    with TestClient(app) as client:
        expired = client.post("/api/auth/sso/exchange", data={"assertion": _make_sso_assertion(exp=int(time.time()) - 5)})
        assert expired.status_code == 400
        long_ttl = client.post("/api/auth/sso/exchange", data={"assertion": _make_sso_assertion(iat=int(time.time()) - 100, exp=int(time.time()) + 10)})
        assert long_ttl.status_code == 400


def test_sso_exchange_rejects_invalid_jti_or_nodeid_or_return_url(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ISSUER", "alfresco-share")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_AUDIENCE", "firmadoc")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_CLOCK_SKEW_SECONDS", 60)
    with TestClient(app) as client:
        invalid_jti = client.post("/api/auth/sso/exchange", data={"assertion": _make_sso_assertion(jti="tiny")})
        assert invalid_jti.status_code == 400
        invalid_nid = client.post("/api/auth/sso/exchange", data={"assertion": _make_sso_assertion(nid="not-a-uuid")})
        assert invalid_nid.status_code == 400
        external_ret = client.post("/api/auth/sso/exchange", data={"assertion": _make_sso_assertion(ret="https://evil.example/steal")})
        assert external_ret.status_code == 400


def test_sso_exchange_replay_rejected(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ISSUER", "alfresco-share")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_AUDIENCE", "firmadoc")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_CLOCK_SKEW_SECONDS", 60)
    monkeypatch.setattr(sso_module, "_SSO_REPLAY_STORE", sso_module.InMemoryReplayStore())
    token = _make_sso_assertion(jti=uuid4().hex)
    with TestClient(app) as client:
        first = client.post("/api/auth/sso/exchange", data={"assertion": token}, follow_redirects=False)
        assert first.status_code == 303
        second = client.post("/api/auth/sso/exchange", data={"assertion": token}, follow_redirects=False)
        assert second.status_code == 400


def test_sso_exchange_returns_controlled_error_when_replay_store_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", True)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ISSUER", "alfresco-share")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_AUDIENCE", "firmadoc")
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_CLOCK_SKEW_SECONDS", 60)
    class UnavailableReplayStore:
        def consume(self, jti, expires_at):
            raise sso_module.SsoReplayStoreUnavailableError("SQL details must not escape")

    monkeypatch.setattr(sso_module, "_SSO_REPLAY_STORE", UnavailableReplayStore())
    token = _make_sso_assertion()
    with TestClient(app) as client:
        response = client.post("/api/auth/sso/exchange", data={"assertion": token}, follow_redirects=False)
        assert response.status_code == 503
        assert response.json() == {"detail": "Servicio SSO temporalmente no disponible"}
        assert "SQL" not in response.text


def test_sso_replay_model_uses_firmadoc_database_names():
    assert SsoReplay.__tablename__ == "ssojti"
    assert SsoReplay.__mapper__.attrs.jti_hash.columns[0].name == "jtihas"
    assert SsoReplay.__mapper__.attrs.expires_at.columns[0].name == "fecexp"
    assert SsoReplay.__mapper__.attrs.used_at.columns[0].name == "fecuse"


def test_sso_exchange_fails_closed_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "FIRMADOC_SSO_ENABLED", False)
    monkeypatch.setattr(settings, "FIRMADOC_SSO_SECRET", "x" * 40)
    with TestClient(app) as client:
        response = client.post("/api/auth/sso/exchange", data={"assertion": _make_sso_assertion()})
        assert response.status_code == 403
