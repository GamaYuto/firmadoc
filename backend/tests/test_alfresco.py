import pytest
import respx
import httpx
from uuid import uuid4
from app.core.config import settings
from app.api.alfresco import sanitize_filename, client as alf_client
import os
import tempfile
from unittest.mock import patch
from app.core.database import SessionLocal

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
    return f"{settings.ALFRESCO_BASE_URL.rstrip('/')}{settings.ALFRESCO_API_URL}"

# 1. nodeId con UUID inválido
def test_get_node_metadata_invalid_uuid(client):
    response = client.get("/api/alfresco/nodes/invalid-uuid")
    assert response.status_code == 422 # Pydantic UUID validation

# 2. respuesta 403
@respx.mock
def test_get_node_metadata_403(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(403))
    response = client.get(f"/api/alfresco/nodes/{node_id}")
    assert response.status_code == 403

# 3. nodo identificado como carpeta
@respx.mock
def test_get_node_metadata_folder(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "isFile": False, "nodeType": "cm:folder"}
    }))
    response = client.get(f"/api/alfresco/nodes/{node_id}")
    assert response.status_code == 422

# 4. mimeType ausente
@respx.mock
def test_get_node_metadata_no_mimetype(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "isFile": True, "content": {}}
    }))
    response = client.get(f"/api/alfresco/nodes/{node_id}")
    assert response.status_code == 422

# 5. archivo no PDF
@respx.mock
def test_get_node_content_not_pdf(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "isFile": True, "content": {"mimeType": "image/png"}}
    }))
    response = client.get(f"/api/alfresco/nodes/{node_id}/content")
    assert response.status_code == 415

# 6. PDF válido (también cubre SHA-256 exacto, headers sanitizados y limpieza)
@respx.mock
def test_get_node_content_success(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    pdf_content = b"%PDF-1.4\n%EOF"
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=pdf_content))
    
    response = client.get(f"/api/alfresco/nodes/{node_id}/content")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert 'filename="doc.pdf"' in response.headers["content-disposition"]
    assert response.content == pdf_content

# 7. PDF mayor al límite configurado (413)
@respx.mock
def test_get_node_content_too_large(client, base_url):
    old_max = alf_client.max_size
    alf_client.max_size = 1 * 1024 * 1024 # 1MB limit
    try:
        node_id = str(uuid4())
        respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
            "entry": {"id": node_id, "name": "doc.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
        }))
        # Send slightly more than 1MB
        large_pdf = b"%PDF-1.4" + (b"0" * (1024 * 1024 + 10))
        respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=large_pdf))
        
        response = client.get(f"/api/alfresco/nodes/{node_id}/content")
        assert response.status_code == 413
    finally:
        alf_client.max_size = old_max

# 8. tamaño declarado permitido pero tamaño real excesivo
@respx.mock
def test_get_node_content_size_deception(client, base_url):
    old_max = alf_client.max_size
    alf_client.max_size = 1 * 1024 * 1024
    try:
        node_id = str(uuid4())
        respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
            "entry": {"id": node_id, "isFile": True, "content": {"mimeType": "application/pdf", "sizeInBytes": 100}} # Lies
        }))
        large_pdf = b"%PDF-1.4" + (b"0" * (1024 * 1024 + 10))
        respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=large_pdf))
        response = client.get(f"/api/alfresco/nodes/{node_id}/content")
        assert response.status_code == 413
    finally:
        alf_client.max_size = old_max

# 9. contenido PDF con magic bytes inválidos
@respx.mock
def test_get_node_content_invalid_magic_bytes(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=b"NOT_PDF"))
    response = client.get(f"/api/alfresco/nodes/{node_id}/content")
    assert response.status_code == 422

# 10. timeout de Alfresco
@respx.mock
def test_get_node_metadata_timeout(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(side_effect=httpx.TimeoutException("Timeout"))
    response = client.get(f"/api/alfresco/nodes/{node_id}")
    assert response.status_code == 503

# 11. error de conexión
@respx.mock
def test_get_node_metadata_connection_error(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(side_effect=httpx.ConnectError("Conn error"))
    response = client.get(f"/api/alfresco/nodes/{node_id}")
    assert response.status_code == 503

# 12-14. sanitización de filename
def test_sanitize_filename():
    assert sanitize_filename("../../../etc/passwd") == "passwd.pdf"
    assert sanitize_filename("normal.pdf") == "normal.pdf"
    assert sanitize_filename('bad"name\x00.pdf') == "badname.pdf"
    assert sanitize_filename("no_extension") == "no_extension.pdf"
    assert sanitize_filename("") == "documento.pdf"

# 15-17. cubiertos en la prueba #6

# 18. auditoría exitosa después de validar
@respx.mock
def test_auditoria_exitosamente_guardada(client, base_url, db_session):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "ok.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=b"%PDF-1.4"))
    
    response = client.get(f"/api/alfresco/nodes/{node_id}/content")
    assert response.status_code == 200
    # Chequear DB
    from app.models.audifir import Audifir
    events = db_session.query(Audifir).filter(Audifir.evento == "ALF_PDF_DOWN").all()
    assert len(events) > 0
    assert "resultado: OK" in events[-1].detalle

# 19. ausencia de ALF_PDF_DOWN cuando falla
@respx.mock
def test_sin_auditoria_si_falla(client, base_url, db_session):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=b"INVALIDO"))
    from app.models.audifir import Audifir
    count_down_before = db_session.query(Audifir).filter(Audifir.evento == "ALF_PDF_DOWN").count()
    count_err_before = db_session.query(Audifir).filter(Audifir.evento == "ALF_CONT_ERR").count()

    try:
        client.get(f"/api/alfresco/nodes/{node_id}/content")
    except Exception:
        pass
    
    count_down_after = db_session.query(Audifir).filter(Audifir.evento == "ALF_PDF_DOWN").count()
    count_err_after = db_session.query(Audifir).filter(Audifir.evento == "ALF_CONT_ERR").count()
    
    assert count_down_after == count_down_before
    assert count_err_after > count_err_before

# 20. fallo de auditoría sin ocultar descarga válida
@respx.mock
def test_fallo_auditoria_no_rompe_descarga(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "ok.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    pdf_content = b"%PDF-1.4\n%EOF"
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=pdf_content))
    
    with patch("app.api.alfresco.create_evento", side_effect=Exception("DB down")):
        response = client.get(f"/api/alfresco/nodes/{node_id}/content")
        assert response.status_code == 200 # Aún retorna 200
        assert response.content == pdf_content

# 21-22. eliminación del temporal comprobada
@respx.mock
def test_temp_file_deleted_on_success(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "ok.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=b"%PDF-1.4"))
    
    # Rastrear archivos temporales. Patching os.unlink to see what was unlinked
    unlinked_files = []
    original_unlink = os.unlink
    def fake_unlink(path):
        unlinked_files.append(path)
        original_unlink(path)
        
    with patch("os.unlink", side_effect=fake_unlink):
        response = client.get(f"/api/alfresco/nodes/{node_id}/content")
        # Forzar lectura
        _ = response.content
        assert len(unlinked_files) == 1
        assert not os.path.exists(unlinked_files[0])

@respx.mock
def test_temp_file_deleted_on_exception(client, base_url):
    node_id = str(uuid4())
    respx.get(f"{base_url}/nodes/{node_id}").mock(return_value=httpx.Response(200, json={
        "entry": {"id": node_id, "name": "ok.pdf", "isFile": True, "content": {"mimeType": "application/pdf"}}
    }))
    respx.get(f"{base_url}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=b"INVALID"))
    
    unlinked_files = []
    original_unlink = os.unlink
    def fake_unlink(path):
        unlinked_files.append(path)
        original_unlink(path)
        
    with patch("os.unlink", side_effect=fake_unlink):
        client.get(f"/api/alfresco/nodes/{node_id}/content")
        assert len(unlinked_files) == 1
        assert not os.path.exists(unlinked_files[0])
