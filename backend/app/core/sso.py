from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from sqlalchemy import delete, inspect

from app.core.database import SessionLocal
from app.models.sso_replay import SsoReplay


class SsoAssertionError(ValueError):
    pass


class SsoReplayStoreUnavailableError(RuntimeError):
    pass


class ReplayStore:
    def consume(self, jti: str, expires_at: int) -> None:
        raise NotImplementedError


class InMemoryReplayStore(ReplayStore):
    def __init__(self) -> None:
        self._used: dict[str, int] = {}
        self._lock = threading.Lock()

    def consume(self, jti: str, expires_at: int) -> None:
        key = hashlib.sha256(jti.encode("utf-8")).hexdigest()
        now = int(time.time())
        with self._lock:
            expired_keys = [item for item, exp in self._used.items() if exp <= now]
            for item in expired_keys:
                self._used.pop(item, None)
            if key in self._used:
                raise SsoAssertionError("Este SSO ya fue usado")
            self._used[key] = expires_at


class DatabaseReplayStore(ReplayStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def consume(self, jti: str, expires_at: int) -> None:
        if not isinstance(jti, str) or not jti.strip():
            raise SsoAssertionError("jti SSO inválido")

        digest = hashlib.sha256(jti.encode("utf-8")).hexdigest()
        expires_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        now_utc = datetime.now(timezone.utc)

        with self._lock:
            db = SessionLocal()
            try:
                if not inspect(db.bind).has_table(SsoReplay.__tablename__):
                    raise SsoReplayStoreUnavailableError("El almacenamiento de replay SSO no está disponible")
                db.execute(delete(SsoReplay).where(SsoReplay.expires_at <= now_utc))
                existing = db.get(SsoReplay, digest)
                if existing is not None:
                    raise SsoAssertionError("Este SSO ya fue usado")

                db.add(SsoReplay(jti_hash=digest, expires_at=expires_dt, used_at=now_utc))
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()


_SSO_REPLAY_STORE = DatabaseReplayStore()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    if not isinstance(data, str) or not data:
        raise SsoAssertionError("Aserción SSO mal formada")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", data):
        raise SsoAssertionError("Aserción SSO con contenido inválido")
    padded = data + ("=" * ((4 - len(data) % 4) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:  # pragma: no cover - defensive guard
        raise SsoAssertionError("Aserción SSO con base64url inválido") from exc


def build_sso_assertion(claims: Mapping[str, Any], secret: str) -> str:
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise SsoAssertionError("Secreto SSO inválido")
    payload_json = json.dumps(claims, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    body = _b64url_encode(payload_json.encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), f"v1.{body}".encode("utf-8"), hashlib.sha256).digest()
    return f"v1.{body}.{_b64url_encode(signature)}"


def validate_return_url(return_url: str, allowed_origins: Sequence[str]) -> str:
    if not return_url or not isinstance(return_url, str):
        raise SsoAssertionError("returnUrl ausente")
    parsed = urlparse(return_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise SsoAssertionError("returnUrl inválida")
    normalized_allowed = {
        origin.strip().rstrip("/").lower()
        for origin in allowed_origins
        if isinstance(origin, str) and origin.strip()
    }
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    if origin not in normalized_allowed:
        raise SsoAssertionError("returnUrl no permitida")
    return return_url


def verify_sso_assertion(
    assertion: str,
    *,
    secret: str,
    issuer: str,
    audience: str,
    allowed_return_origins: Sequence[str],
    max_age_seconds: int = 60,
    clock_skew_seconds: int = 60,
    replay_store: ReplayStore | None = None,
) -> dict[str, Any]:
    if not assertion or not isinstance(assertion, str):
        raise SsoAssertionError("Aserción SSO vacía")
    parts = assertion.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise SsoAssertionError("Formato de aserción SSO inválido")

    version, payload_segment, sig_segment = parts
    if not payload_segment or not sig_segment:
        raise SsoAssertionError("Aserción SSO incompleta")

    expected_sig = hmac.new(
        secret.encode("utf-8"),
        f"{version}.{payload_segment}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    actual_sig = _b64url_decode(sig_segment)
    if not hmac.compare_digest(actual_sig, expected_sig):
        raise SsoAssertionError("Firma SSO inválida")

    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive guard
        raise SsoAssertionError("JSON SSO inválido") from exc
    if not isinstance(payload, dict):
        raise SsoAssertionError("Payload SSO inválido")

    if payload.get("iss") != issuer:
        raise SsoAssertionError("Issuer SSO inválido")
    if payload.get("aud") != audience:
        raise SsoAssertionError("Audience SSO inválida")

    sub = str(payload.get("sub", "")).strip()
    if not sub or not re.fullmatch(r"[A-Za-z0-9._@-]+", sub):
        raise SsoAssertionError("Usuario SSO inválido")

    try:
        iat = int(payload.get("iat"))
        exp = int(payload.get("exp"))
    except (TypeError, ValueError):
        raise SsoAssertionError("Claims temporales SSO inválidas")

    now = int(time.time())
    if abs(now - iat) > max(0, clock_skew_seconds):
        raise SsoAssertionError("Reloj SSO fuera de tolerancia")
    if iat > now + max(0, clock_skew_seconds):
        raise SsoAssertionError("iat futuro fuera de tolerancia")
    if exp <= now:
        raise SsoAssertionError("Aserción SSO caducada")
    if exp - iat > max_age_seconds + clock_skew_seconds:
        raise SsoAssertionError("TTL SSO superior al máximo permitido")

    jti = str(payload.get("jti", "")).strip()
    if not jti or len(jti) < 16 or len(jti) > 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", jti):
        raise SsoAssertionError("jti SSO inválido")

    nid = str(payload.get("nid", "")).strip()
    try:
        uuid.UUID(nid)
    except ValueError as exc:
        raise SsoAssertionError("nodeId SSO inválido") from exc

    ret = str(payload.get("ret", "")).strip()
    validate_return_url(ret, allowed_return_origins)

    store = replay_store or _SSO_REPLAY_STORE
    store.consume(jti, exp)
    return payload
