from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.models.docfirma import DocFirma, EstadoDocFirma
from app.services.alfresco_service import AlfrescoService
from app.services.signature_exceptions import SignatureIntegrityError, SignatureRecoveryRequiredError, SignatureVersionConflictError

from tests.test_signature_publication import (
    API_URL,
    ROOT_URL,
    _event_count,
    _publication_service,
    _seed_publication_context,
)


def _reconcile_service(temp_service, node_id: str) -> AlfrescoService:
    return _publication_service(temp_service, node_id)


def _mark_subiendo(db_session, firid: int) -> DocFirma:
    firma = db_session.get(DocFirma, firid)
    assert firma is not None
    firma.estado = EstadoDocFirma.SUBIENDO.value
    db_session.commit()
    db_session.expire_all()
    refreshed = db_session.get(DocFirma, firid)
    assert refreshed is not None
    return refreshed


@respx.mock
def test_reconcile_signature_attempt_success(db_session, tmp_path: Path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _reconcile_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    generated_bytes = context["generated_artifact"].path.read_bytes()
    comment = context["comment"]

    firma = _mark_subiendo(db_session, context["firma"].firid)

    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "source.pdf",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "application/pdf", "sizeInBytes": len(context["source_bytes"])},
                    "properties": {"cm:versionLabel": "1.1"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": "1.1",
                    "nodeId": node_id,
                    "versionComment": comment,
                    "createdAt": "2026-08-03T12:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1/content").mock(
        return_value=httpx.Response(200, content=generated_bytes)
    )

    version = service.reconcile_signature_attempt(db_session, firma.firid)

    db_session.expire_all()
    refreshed = db_session.get(DocFirma, firma.firid)
    assert refreshed is not None
    assert version is not None
    assert version.version_id == "1.1"
    assert refreshed.estado == EstadoDocFirma.COMPLETADA.value
    assert refreshed.verfin == "1.1"
    assert refreshed.hasfin == context["generated_artifact"].sha256
    assert _event_count(db_session, firma.firid, "FIR_RECO") == 1
    assert _event_count(db_session, firma.firid, "FIR_COMP") == 1
    assert context["generated_artifact"].path.exists() is False


@respx.mock
def test_reconcile_signature_attempt_duplicate_version_conflict(db_session, tmp_path: Path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _reconcile_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]

    firma = _mark_subiendo(db_session, context["firma"].firid)

    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "source.pdf",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "application/pdf", "sizeInBytes": len(context["source_bytes"])},
                    "properties": {"cm:versionLabel": "1.0"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.0").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": "1.0",
                    "nodeId": node_id,
                    "versionComment": "base",
                    "createdAt": "2026-08-03T11:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions").mock(
        return_value=httpx.Response(
            200,
            json={
                "list": {
                    "entries": [
                        {"entry": {"id": "1.1", "nodeId": node_id, "versionComment": context["comment"], "createdAt": "2026-08-03T12:00:00Z"}},
                        {"entry": {"id": "1.2", "nodeId": node_id, "versionComment": context["comment"], "createdAt": "2026-08-03T12:10:00Z"}},
                    ]
                }
            },
        )
    )

    with pytest.raises(SignatureVersionConflictError):
        service.reconcile_signature_attempt(db_session, firma.firid)

    db_session.expire_all()
    refreshed = db_session.get(DocFirma, firma.firid)
    assert refreshed is not None
    assert refreshed.estado == EstadoDocFirma.CONFLICTO.value
    assert refreshed.errcod == "DUPLICATE_REMOTE_VERSION"
    assert _event_count(db_session, firma.firid, "FIR_CONF") == 1
    assert _event_count(db_session, firma.firid, "FIR_RECO") == 1
    assert _event_count(db_session, firma.firid, "FIR_COMP") == 0


@respx.mock
def test_reconcile_signature_attempt_zero_match_keeps_subiendo(db_session, tmp_path: Path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _reconcile_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]

    firma = _mark_subiendo(db_session, context["firma"].firid)

    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "source.pdf",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "application/pdf", "sizeInBytes": len(context["source_bytes"])},
                    "properties": {"cm:versionLabel": "1.0"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.0").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": "1.0",
                    "nodeId": node_id,
                    "versionComment": "base",
                    "createdAt": "2026-08-03T11:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions").mock(
        return_value=httpx.Response(200, json={"list": {"entries": []}})
    )

    with pytest.raises(SignatureRecoveryRequiredError):
        service.reconcile_signature_attempt(db_session, firma.firid)

    db_session.expire_all()
    refreshed = db_session.get(DocFirma, firma.firid)
    assert refreshed is not None
    assert refreshed.estado == EstadoDocFirma.SUBIENDO.value
    assert _event_count(db_session, firma.firid, "FIR_RECO") == 1
    assert _event_count(db_session, firma.firid, "FIR_CONF") == 0
    assert _event_count(db_session, firma.firid, "FIR_COMP") == 0
