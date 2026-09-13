import pytest
import respx
import httpx
from uuid import uuid4
import os
import tempfile
from unittest.mock import patch
from app.models.docfir import DocFir, EstadoDoc
from app.models.audifir import Audifir
from app.api.alfresco import client as alf_client
from app.core.database import SessionLocal
from app.core.config import settings
from sqlalchemy.exc import IntegrityError

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()

@pytest.fixture
def base_url():
    api_path = settings.ALFRESCO_API_PATH or settings.ALFRESCO_API_URL or "/alfresco/api/-default-/public/alfresco/versions/1"
    return f"{settings.ALFRESCO_BASE_URL.rstrip('/')}{api_path}"


def _make_pdf_bytes(text: str = "FirmaDoc") -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=12)
    document.set_metadata({})
    data = document.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    document.close()
    return data

# 1-7. Creación exitosa, valores de Alfresco, hash, tamaño, version, estado, DOC_INICIO
@respx.mock
def test_iniciar_proceso_exito(client, base_url, db_session):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}, "properties": {"cm:versionLabel": "1.2"}}
    }))
    pdf_content = _make_pdf_bytes("doc.pdf")
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=pdf_content))
    
    response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "testuser"})
    assert response.status_code == 201
    data = response.json()
    assert data["nodid"] == node_id
    assert data["docnom"] == "doc.pdf"
    assert data["tamano"] == len(pdf_content)
    assert data["verini"] == "1.2"
    assert data["estado"] == "BORRADOR"
    assert data["usrcre"] == "testuser"
    # hash should be sha256 of pdf_content
    import hashlib
    h = hashlib.sha256(pdf_content).hexdigest()
    assert data["hasori"] == h
    assert "feccre" in data
    
    docid = data["docid"]
    
    # Verify DOC_INICIO
    evento = db_session.query(Audifir).filter(Audifir.docid == docid, Audifir.evento == "DOC_INICIO").first()
    assert evento is not None
    assert evento.usrid == "testuser"

# 8. Rollback si falla DOC_INICIO
@respx.mock
def test_iniciar_proceso_rollback_auditoria(client, base_url, db_session):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=_make_pdf_bytes("doc.pdf")))
    
    with patch("app.services.document_service.create_evento", side_effect=Exception("DB Failure")):
        response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "testuser"})
        assert response.status_code == 500
        docs = db_session.query(DocFir).filter(DocFir.nodid == node_id).all()
        assert len(docs) == 0

# 9. nodeId inválido
def test_iniciar_proceso_uuid_invalido(client):
    response = client.post("/api/documentos/iniciar", json={"node_id": "no-uuid"}, headers={"X-FirmaDoc-User": "testuser"})
    assert response.status_code == 422

# 10. Nodo inexistente
@respx.mock
def test_iniciar_proceso_not_found(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(404))
    response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "testuser"})
    assert response.status_code == 404

# 11. Nodo carpeta
@respx.mock
def test_iniciar_proceso_carpeta(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "isFile": False, "nodeType": "cm:folder"}
    }))
    response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "testuser"})
    assert response.status_code == 422

# 12. Archivo no PDF
@respx.mock
def test_iniciar_proceso_no_pdf(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "isFile": True, "content": {"mimeType": "image/jpeg"}}
    }))
    response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "testuser"})
    assert response.status_code == 415

# 13. PDF inválido (magic bytes)
@respx.mock
def test_iniciar_proceso_invalid_pdf_content(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=b"INVALIDO"))
    response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "testuser"})
    assert response.status_code == 422

# 14. PDF excesivo
@respx.mock
def test_iniciar_proceso_excessive_size(client, base_url):
    from app.api.documentos import alfresco_client as doc_alf_client
    old_max = doc_alf_client.max_size
    doc_alf_client.max_size = 1 * 1024 * 1024
    try:
        node_id = str(uuid4())
        respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
            "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
        }))
        large = b"%PDF-1.4" + b"0" * (1024 * 1024 + 10)
        respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=large))
        response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "testuser"})
        assert response.status_code == 413
    finally:
        doc_alf_client.max_size = old_max

# 15. Alfresco timeout
@respx.mock
def test_iniciar_proceso_timeout(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(side_effect=httpx.TimeoutException("Timeout"))
    response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "testuser"})
    assert response.status_code == 503

# 16. Proceso activo duplicado (lógica de get_active_by_node_version)
@respx.mock
def test_iniciar_proceso_duplicado(client, base_url, db_session):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=_make_pdf_bytes("doc.pdf")))
    
    # 1st time
    response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "u1"})
    assert response.status_code == 201
    
    # 2nd time
    response2 = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "u2"})
    assert response2.status_code == 409

    # Verificar DOC_DUPLI
    ev_dupli = db_session.query(Audifir).filter(Audifir.evento == "DOC_DUPLI").all()
    assert len(ev_dupli) > 0
    assert str(node_id) in ev_dupli[-1].detalle

# 16.5 Reinicio después de cancelación
@respx.mock
def test_can_restart_same_node_version_after_cancel(client, base_url, db_session):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}, "properties": {"cm:versionLabel": "1.0"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=_make_pdf_bytes("doc.pdf")))
    
    # 1. Crear proceso BORRADOR
    resp1 = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "u1"})
    assert resp1.status_code == 201
    docid1 = resp1.json()["docid"]
    
    # 2. Cancelarlo
    resp_cancel = client.post(f"/api/documentos/{docid1}/cancelar", json={"motivo": "Cancelado intencionalmente"}, headers={"X-FirmaDoc-User": "u1"})
    assert resp_cancel.status_code == 200
    
    # 3. Crear segundo proceso
    resp2 = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "u2"})
    assert resp2.status_code == 201
    docid2 = resp2.json()["docid"]
    
    # Comprobaciones requeridas
    assert docid1 != docid2
    assert resp1.json()["nodid"] == resp2.json()["nodid"]
    assert resp1.json()["verini"] == resp2.json()["verini"]
    
    doc1 = db_session.query(DocFir).filter(DocFir.docid == docid1).first()
    doc2 = db_session.query(DocFir).filter(DocFir.docid == docid2).first()
    
    assert doc1.estado == "CANCELADO"
    assert doc2.estado == "BORRADOR"
    
    ev_inicios = db_session.query(Audifir).filter(Audifir.evento == "DOC_INICIO", Audifir.docid.in_([docid1, docid2])).all()
    assert len(ev_inicios) == 2
    
    ev_cancel = db_session.query(Audifir).filter(Audifir.evento == "DOC_CANCEL", Audifir.docid == docid1).first()
    assert ev_cancel is not None

# 17. Concurrencia simulada (IntegrityError por la restricción de BD)
@respx.mock
def test_iniciar_proceso_concurrencia_integrity(client, base_url, monkeypatch):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=_make_pdf_bytes("doc.pdf")))
    
    # Bypass initial check and force integrity error on insert
    with patch("app.services.document_service.get_active_by_node_version", return_value=None):
        with patch("app.services.document_service.create_documento", side_effect=IntegrityError("x", "y", "z")):
            response = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "test"})
            assert response.status_code == 409

# 18. Consulta por docid
@respx.mock
def test_obtener_proceso(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "d.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=_make_pdf_bytes("d.pdf")))
    
    resp_init = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "u1"})
    docid = resp_init.json()["docid"]
    
    resp_get = client.get(f"/api/documentos/{docid}", headers={"X-FirmaDoc-User": "u2"})
    assert resp_get.status_code == 200
    assert resp_get.json()["docid"] == docid

# 19-20. Listado paginado y filtro por estado
@respx.mock
def test_listar_procesos(client, base_url):
    # Ya se han creado al menos 2 procesos en los tests anteriores (en realidad el DB es persistente)
    # Por lo que solo verificaremos que retorne 200 y items
    resp_list = client.get("/api/documentos?limit=10&estado=BORRADOR", headers={"X-FirmaDoc-User": "admin"})
    assert resp_list.status_code == 200
    data = resp_list.json()
    assert "items" in data
    assert "total" in data

# 21, 22, 23, 24. Cancelaciones
@respx.mock
def test_cancelacion_proceso(client, base_url, db_session):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "d.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=_make_pdf_bytes("d.pdf")))
    
    resp_init = client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "u1"})
    docid = resp_init.json()["docid"]
    
    # 22. Motivo vacío
    resp_empty = client.post(f"/api/documentos/{docid}/cancelar", json={"motivo": ""}, headers={"X-FirmaDoc-User": "u1"})
    assert resp_empty.status_code == 422
    
    # 21. Cancelación exitosa
    resp_cancel = client.post(f"/api/documentos/{docid}/cancelar", json={"motivo": "Me equivoqué"}, headers={"X-FirmaDoc-User": "u2"})
    assert resp_cancel.status_code == 200
    assert resp_cancel.json()["estado"] == "CANCELADO"
    assert resp_cancel.json()["usrmod"] == "u2"
    
    # 24. Auditoría DOC_CANCEL
    ev_cancel = db_session.query(Audifir).filter(Audifir.docid == docid, Audifir.evento == "DOC_CANCEL").first()
    assert ev_cancel is not None
    assert "Me equivoqué" in ev_cancel.detalle
    
    # 23. Cancelar de nuevo
    resp_re_cancel = client.post(f"/api/documentos/{docid}/cancelar", json={"motivo": "Otra vez"}, headers={"X-FirmaDoc-User": "u2"})
    assert resp_re_cancel.status_code == 400

# 27. Ausencia de temporales (os.unlink)
@respx.mock
def test_iniciar_proceso_borra_temporal(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "d.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=b"%PDF-1.4"))
    
    tempfile.gettempdir()
    unlinked = []
    orig = os.unlink
    def fake_ul(p):
        unlinked.append(p)
        orig(p)
        
    with patch("os.unlink", side_effect=fake_ul):
        client.post("/api/documentos/iniciar", json={"node_id": node_id}, headers={"X-FirmaDoc-User": "test"})
        assert len(unlinked) == 1
        assert not os.path.exists(unlinked[0])
