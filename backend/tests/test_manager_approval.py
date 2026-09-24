import hashlib
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pymupdf as fitz
import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.models.audifir import Audifir
from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma
from app.models.docpaso import DocPaso
from app.models.sesionqr import EstadoSesionQr, SesionQr
from app.schemas.alfresco import NodeMetadata
import app.services.frontend_signature_service as frontend_module
from app.services.frontend_signature_service import frontend_signature_service
from app.services.manager_approval_service import manager_approval_service
from app.services.pdf_signature_service import pdf_signature_service
from app.services.temporary_artifact_service import TemporaryArtifactService


def _pdf_bytes(label="Documento para Gerencia"):
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), label, fontsize=14)
    content = document.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    document.close()
    return content


@pytest.fixture
def manager_environment(monkeypatch, tmp_path):
    state = {
        "pdf": _pdf_bytes(),
        "version": "1.0",
        "enabled": True,
        "manager": "gerente.lab",
    }

    async def metadata(node_id):
        return NodeMetadata(
            node_id=node_id,
            name="gerencia.pdf",
            node_type="cm:content",
            is_file=True,
            mime_type="application/pdf",
            size_bytes=len(state["pdf"]),
            version_label=state["version"],
        )

    async def download(_node_id):
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        handle.write(state["pdf"])
        handle.close()
        return handle.name, len(state["pdf"]), hashlib.sha256(state["pdf"]).hexdigest()

    async def search(term, max_items=10):
        if state["enabled"] and term.strip().lower() == state["manager"]:
            return [{
                "userName": state["manager"],
                "firstName": "Gerente",
                "lastName": "Laboratorio",
                "displayName": "Gerente Laboratorio",
            }]
        return []

    for client in (
        manager_approval_service.alfresco_client,
        frontend_signature_service.alfresco_client,
    ):
        monkeypatch.setattr(client, "get_node_metadata", metadata)
        monkeypatch.setattr(client, "download_node_content", download)
    monkeypatch.setattr(manager_approval_service.alfresco_client, "search_users", search)
    monkeypatch.setattr(settings, "FIRMADOC_GERENCIA_USER_ID", state["manager"])
    monkeypatch.setattr(settings, "FIRMADOC_GERENCIA_CARGO", "Gerente General")
    artifact_service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    monkeypatch.setattr(pdf_signature_service, "artifact_service", artifact_service)
    monkeypatch.setattr(frontend_module, "temporary_artifact_service", artifact_service)
    return state, artifact_service


def _create_request(client, node_id):
    response = client.post(
        "/api/firma/gerencia/solicitudes",
        json={
            "node_id": str(node_id),
            "page": 1,
            "posx": 72,
            "posy": 110,
            "width": 320,
            "height": 110,
        },
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_manager_approval_authorizes_once_and_preserves_audit(
    client, db_session, manager_environment
):
    _state, artifacts = manager_environment
    created = _create_request(client, uuid4())
    token = created["approval_url"].split("#", 1)[1]

    step = db_session.scalar(select(DocPaso).where(DocPaso.docid == created["docid"]))
    signature = db_session.get(DocFirma, created["firid"])
    assert step.pastip == "APROBAR"
    assert signature.tipfir == "INTERNA"
    assert db_session.get(DocFir, created["docid"]).estado == EstadoDoc.EN_CURSO.value

    forbidden = client.get(
        "/api/firma/gerencia/solicitud",
        headers={"X-FirmaDoc-User": "usuario.ajeno", "X-FirmaDoc-Approval": token},
    )
    assert forbidden.status_code == 403

    detail = client.get(
        "/api/firma/gerencia/solicitud",
        headers={"X-FirmaDoc-User": "gerente.lab", "X-FirmaDoc-Approval": token},
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["manager_name"] == "Gerente Laboratorio"

    requester_status = client.get(
        f"/api/firma/gerencia/solicitudes/{created['firid']}/estado",
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert requester_status.status_code == 200
    assert requester_status.json()["status"] == "PENDIENTE"
    external_status = client.get(
        f"/api/firma/gerencia/solicitudes/{created['firid']}/estado",
        headers={"X-FirmaDoc-User": "usuario.ajeno"},
    )
    assert external_status.status_code == 403

    approved = client.post(
        "/api/firma/gerencia/autorizar",
        json={"token": token},
        headers={"X-FirmaDoc-User": "gerente.lab"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["document_status"] == EstadoDoc.PENDIENTE_PUBLICACION.value

    db_session.expire_all()
    signature = db_session.get(DocFirma, created["firid"])
    session = db_session.scalar(select(SesionQr).where(SesionQr.firid == created["firid"]))
    assert signature.estado == EstadoDocFirma.COMPLETADA.value
    assert signature.hasfin and len(signature.hasfin) == 64
    assert session.estado == EstadoSesionQr.USADO.value
    assert db_session.scalar(
        select(Audifir).where(Audifir.evento == "GER_AUT", Audifir.entid == created["firid"])
    )

    artifact = artifacts.find_latest_path(prefix=f"fir-{created['firid']}-", suffix=".pdf")
    assert artifact is not None
    with fitz.open(artifact) as document:
        text = document[0].get_text()
    assert "AUTORIZADO ELECTR" in text
    assert "Gerente Laboratorio" in text
    assert "Gerente General" in text

    duplicate = client.post(
        "/api/firma/gerencia/autorizar",
        json={"token": token},
        headers={"X-FirmaDoc-User": "gerente.lab"},
    )
    assert duplicate.status_code == 409
    assert len(artifacts.find_matching_paths(prefix=f"fir-{created['firid']}-", suffix=".pdf")) == 1
    reject_after_approval = client.post(
        "/api/firma/gerencia/rechazar",
        json={"token": token, "reason": "Intento tardio"},
        headers={"X-FirmaDoc-User": "gerente.lab"},
    )
    assert reject_after_approval.status_code == 409


def test_double_authorize_concurrently_generates_one_artifact(
    client, db_session, manager_environment
):
    _state, artifacts = manager_environment
    created = _create_request(client, uuid4())
    token = created["approval_url"].split("#", 1)[1]

    def authorize():
        with TestClient(app) as thread_client:
            login = thread_client.post(
                "/api/auth/session",
                json={"user_id": "gerente.lab"},
            )
            assert login.status_code == 200
            return thread_client.post(
                "/api/firma/gerencia/autorizar",
                json={"token": token},
                headers={"X-FirmaDoc-CSRF": login.json()["csrf_token"]},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _index: authorize(), range(2)))

    assert sorted(statuses) == [200, 409]
    assert len(artifacts.find_matching_paths(prefix=f"fir-{created['firid']}-", suffix=".pdf")) == 1
    db_session.expire_all()
    events = db_session.scalars(
        select(Audifir).where(Audifir.evento == "GER_AUT", Audifir.entid == created["firid"])
    ).all()
    assert len(events) == 1


def test_manager_rejection_is_terminal(client, db_session, manager_environment):
    created = _create_request(client, uuid4())
    token = created["approval_url"].split("#", 1)[1]
    rejected = client.post(
        "/api/firma/gerencia/rechazar",
        json={"token": token, "reason": "No cumple criterios"},
        headers={"X-FirmaDoc-User": "gerente.lab"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "RECHAZADO"
    assert "Documento no autorizado por Gerencia" in rejected.json()["message"]
    assert db_session.get(DocFir, created["docid"]).estado == EstadoDoc.RECHAZADO.value
    assert db_session.get(DocFirma, created["firid"]).estado == EstadoDocFirma.CANCELADA.value

    after_reject = client.post(
        "/api/firma/gerencia/autorizar",
        json={"token": token},
        headers={"X-FirmaDoc-User": "gerente.lab"},
    )
    assert after_reject.status_code == 409


def test_manager_configuration_and_disabled_user_fail_closed(
    client, monkeypatch, manager_environment
):
    state, _artifacts = manager_environment
    monkeypatch.setattr(settings, "FIRMADOC_GERENCIA_USER_ID", None)
    missing = client.post(
        "/api/firma/gerencia/solicitudes",
        json={"node_id": str(uuid4()), "page": 1, "posx": 1, "posy": 1, "width": 200, "height": 80},
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert missing.status_code == 503

    monkeypatch.setattr(settings, "FIRMADOC_GERENCIA_USER_ID", "gerente.lab")
    state["enabled"] = False
    disabled = client.post(
        "/api/firma/gerencia/solicitudes",
        json={"node_id": str(uuid4()), "page": 1, "posx": 1, "posy": 1, "width": 200, "height": 80},
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert disabled.status_code == 503


def test_manager_token_expiration(client, db_session, manager_environment):
    created = _create_request(client, uuid4())
    token = created["approval_url"].split("#", 1)[1]
    session = db_session.scalar(select(SesionQr).where(SesionQr.firid == created["firid"]))
    session.fecexp = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    expired = client.get(
        "/api/firma/gerencia/solicitud",
        headers={"X-FirmaDoc-User": "gerente.lab", "X-FirmaDoc-Approval": token},
    )
    assert expired.status_code == 410
    db_session.expire_all()
    assert db_session.get(SesionQr, session.sesid).estado == EstadoSesionQr.EXPIRADO.value


@pytest.mark.parametrize("change", ["version", "hash"])
def test_manager_remote_change_blocks_authorization(
    client, db_session, manager_environment, change
):
    state, _artifacts = manager_environment
    created = _create_request(client, uuid4())
    token = created["approval_url"].split("#", 1)[1]
    if change == "version":
        state["version"] = "1.1"
    else:
        state["pdf"] = _pdf_bytes("Contenido modificado")
    response = client.post(
        "/api/firma/gerencia/autorizar",
        json={"token": token},
        headers={"X-FirmaDoc-User": "gerente.lab"},
    )
    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(DocFirma, created["firid"]).estado == EstadoDocFirma.CONFLICTO.value
    assert db_session.scalar(
        select(Audifir).where(Audifir.evento == "GER_CON", Audifir.entid == created["firid"])
    )


def test_qr_and_manager_tokens_are_not_interchangeable(
    client, manager_environment
):
    manager_created = _create_request(client, uuid4())
    manager_token = manager_created["approval_url"].split("#", 1)[1]
    manager_in_qr = client.get(
        f"/api/firma/movil/sesion/{manager_token}",
        headers={"X-FirmaDoc-User": "gerente.lab"},
    )
    assert manager_in_qr.status_code == 404
    manager_in_qr_confirm = client.post(
        "/api/firma/movil/confirmar",
        json={
            "token": manager_token,
            "png_data_url": "data:image/png;base64," + ("A" * 32),
        },
        headers={"X-FirmaDoc-User": "gerente.lab"},
    )
    assert manager_in_qr_confirm.status_code == 404

    node_id = uuid4()
    prep = client.get(
        f"/api/firma/preparacion/{node_id}",
        headers={"X-FirmaDoc-User": "preparador"},
    ).json()
    payload = {
        "participants": [{"usrid": "firmante", "orden": 1, "obliga": True}],
        "positions": [{
            "pagina": 1, "posx": 72, "posy": 110, "ancho": 220, "alto": 90,
            "rotaci": 0, "orden": 1, "tipfir": "MANUSCRITA", "usrid": "firmante",
        }],
    }
    sent = client.post(
        f"/api/firma/preparacion/{prep['docid']}/guardar-enviar",
        json=payload,
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert sent.status_code == 200, sent.text
    qr = client.post(
        f"/api/firma/firmas/{sent.json()['firid']}/qr",
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert qr.status_code == 200, qr.text
    qr_in_manager = client.get(
        "/api/firma/gerencia/solicitud",
        headers={
            "X-FirmaDoc-User": "gerente.lab",
            "X-FirmaDoc-Approval": qr.json()["token"],
        },
    )
    assert qr_in_manager.status_code == 404
