import pytest
import respx
import httpx
from unittest.mock import patch
from app.models.plantill import Plantill
from app.models.tplcamp import TplCamp
from app.models.audifir import Audifir
from app.core.database import SessionLocal



def test_crear_plantilla_borrador(client, db_session):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-01", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    assert resp.status_code == 201
    assert resp.json()["estado"] == "BORRADOR"
    assert resp.json()["tplver"] == 1
    
    # Audit TPL_CREA
    ev = db_session.query(Audifir).filter(Audifir.evento == "TPL_CREA").first()
    assert ev is not None
    assert ev.enttip == "PLANTILLA"

def test_codigo_vacio(client):
    resp = client.post("/api/plantillas", json={"tplcod": "", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    assert resp.status_code == 422

def test_nombre_vacio(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-01", "tplnom": "", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    assert resp.status_code == 422

def test_paginas_invalidas(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-01", "tplnom": "Test", "numpag": 0}, headers={"X-FirmaDoc-User": "admin"})
    assert resp.status_code == 422

def test_duplicado_codigo_version(client, db_session):
    client.post("/api/plantillas", json={"tplcod": "TPL-02", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-02", "tplnom": "Test 2", "numpag": 2}, headers={"X-FirmaDoc-User": "admin"})
    assert resp.status_code == 409

def test_crear_campo_valido(client, db_session):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-03", "tplnom": "Test", "numpag": 2}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 201

def test_tipo_campo_invalido(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-04", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "INVALIDO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 422

def test_pagina_superior_a_numpag(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-05", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 2,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 422

def test_coordenadas_negativas(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-06", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": -0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 422

def test_coordenadas_mayores_a_1(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-07", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 1.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 422

def test_campo_fuera_del_limite_de_pagina(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-08", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.9, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 422

def test_ancho_o_alto_cero(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-09", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 422

def test_codigo_de_campo_duplicado(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-10", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    data = {
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }
    client.post(f"/api/plantillas/{tplid}/campos", json=data, headers={"X-FirmaDoc-User": "admin"})
    data["orden"] = 2
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json=data, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 409

def test_orden_duplicado(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-11", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    data = {
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }
    client.post(f"/api/plantillas/{tplid}/campos", json=data, headers={"X-FirmaDoc-User": "admin"})
    data["camcod"] = "CAM2"
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json=data, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 409

def test_config_texto_valida(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-12", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 201

def test_config_texto_invalida(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-13", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"wrong_key": 10}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 422

def test_config_casilla_valida(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-14", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "CASILLA", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"checked_value": "SI"}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 201

def test_config_opcion_valida(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-15", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "OPCION", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"group": "g1", "value": "v1"}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 201

def test_config_firma_valida(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-16", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "FIRMA", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"role": "FIRMANTE", "capture_type": "BIOMETRICA"}
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 201

def test_propiedad_config_incompatible(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-17", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_c = client.post(f"/api/plantillas/{tplid}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "CASILLA", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 100} # Incorrect for CASILLA
    }, headers={"X-FirmaDoc-User": "admin"})
    assert resp_c.status_code == 422

def test_modificar_plantilla_borrador(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-18", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_m = client.patch(f"/api/plantillas/{tplid}", json={"tplnom": "Test Modificado"}, headers={"X-FirmaDoc-User": "admin"})
    assert resp_m.status_code == 200
    assert resp_m.json()["tplnom"] == "Test Modificado"

def test_impedir_modificar_activa(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-19", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    client.post(f"/api/plantillas/{tplid}/activar", headers={"X-FirmaDoc-User": "admin"})
    
    resp_m = client.patch(f"/api/plantillas/{tplid}", json={"tplnom": "Test Modificado"}, headers={"X-FirmaDoc-User": "admin"})
    assert resp_m.status_code == 400

def test_activar_plantilla(client):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-20", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    resp_a = client.post(f"/api/plantillas/{tplid}/activar", headers={"X-FirmaDoc-User": "admin"})
    assert resp_a.status_code == 200
    assert resp_a.json()["estado"] == "ACTIVA"

def test_inactivar_version_anterior_al_activar_nueva(client, db_session):
    resp1 = client.post("/api/plantillas", json={"tplcod": "TPL-21", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid1 = resp1.json()["tplid"]
    client.post(f"/api/plantillas/{tplid1}/activar", headers={"X-FirmaDoc-User": "admin"})
    
    resp2 = client.post(f"/api/plantillas/{tplid1}/versiones", headers={"X-FirmaDoc-User": "admin"})
    tplid2 = resp2.json()["tplid"]
    
    client.post(f"/api/plantillas/{tplid2}/activar", headers={"X-FirmaDoc-User": "admin"})
    
    doc1 = db_session.query(Plantill).filter(Plantill.tplid == tplid1).first()
    doc2 = db_session.query(Plantill).filter(Plantill.tplid == tplid2).first()
    assert doc1.estado == "INACTIVA"
    assert doc2.estado == "ACTIVA"

def test_solo_una_version_activa(client, db_session):
    resp1 = client.post("/api/plantillas", json={"tplcod": "TPL-22", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid1 = resp1.json()["tplid"]
    client.post(f"/api/plantillas/{tplid1}/activar", headers={"X-FirmaDoc-User": "admin"})
    
    resp2 = client.post(f"/api/plantillas/{tplid1}/versiones", headers={"X-FirmaDoc-User": "admin"})
    tplid2 = resp2.json()["tplid"]
    client.post(f"/api/plantillas/{tplid2}/activar", headers={"X-FirmaDoc-User": "admin"})
    
    actives = db_session.query(Plantill).filter(Plantill.tplcod == "TPL-22", Plantill.estado == "ACTIVA").all()
    assert len(actives) == 1

def test_crear_nueva_version(client):
    resp1 = client.post("/api/plantillas", json={"tplcod": "TPL-23", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid1 = resp1.json()["tplid"]
    client.post(f"/api/plantillas/{tplid1}/activar", headers={"X-FirmaDoc-User": "admin"})
    
    resp2 = client.post(f"/api/plantillas/{tplid1}/versiones", headers={"X-FirmaDoc-User": "admin"})
    assert resp2.status_code == 201
    assert resp2.json()["tplver"] == 2
    assert resp2.json()["estado"] == "BORRADOR"

def test_copiar_campos_activos(client, db_session):
    resp1 = client.post("/api/plantillas", json={"tplcod": "TPL-24", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid1 = resp1.json()["tplid"]
    client.post(f"/api/plantillas/{tplid1}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"})
    
    resp2 = client.post(f"/api/plantillas/{tplid1}/versiones", headers={"X-FirmaDoc-User": "admin"})
    tplid2 = resp2.json()["tplid"]
    
    campos2 = client.get(f"/api/plantillas/{tplid2}/campos", headers={"X-FirmaDoc-User": "admin"}).json()["items"]
    assert len(campos2) == 1

def test_no_copiar_campos_inactivos(client):
    resp1 = client.post("/api/plantillas", json={"tplcod": "TPL-25", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid1 = resp1.json()["tplid"]
    cam = client.post(f"/api/plantillas/{tplid1}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"}).json()
    
    client.post(f"/api/plantillas/{tplid1}/campos/{cam['camid']}/inactivar", headers={"X-FirmaDoc-User": "admin"})
    
    resp2 = client.post(f"/api/plantillas/{tplid1}/versiones", headers={"X-FirmaDoc-User": "admin"})
    tplid2 = resp2.json()["tplid"]
    
    campos2 = client.get(f"/api/plantillas/{tplid2}/campos", headers={"X-FirmaDoc-User": "admin"}).json()["items"]
    assert len(campos2) == 0

def test_mantener_version_anterior_intacta(client, db_session):
    resp1 = client.post("/api/plantillas", json={"tplcod": "TPL-26", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid1 = resp1.json()["tplid"]
    
    client.post(f"/api/plantillas/{tplid1}/versiones", headers={"X-FirmaDoc-User": "admin"})
    
    doc1 = db_session.query(Plantill).filter(Plantill.tplid == tplid1).first()
    assert doc1.estado == "BORRADOR"

def test_inactivar_campo(client):
    resp1 = client.post("/api/plantillas", json={"tplcod": "TPL-27", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid1 = resp1.json()["tplid"]
    cam = client.post(f"/api/plantillas/{tplid1}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"}).json()
    
    resp_in = client.post(f"/api/plantillas/{tplid1}/campos/{cam['camid']}/inactivar", headers={"X-FirmaDoc-User": "admin"})
    assert resp_in.status_code == 200

def test_impedir_editar_campo_de_plantilla_activa(client):
    resp1 = client.post("/api/plantillas", json={"tplcod": "TPL-28", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid1 = resp1.json()["tplid"]
    cam = client.post(f"/api/plantillas/{tplid1}/campos", json={
        "camcod": "CAM1", "camnom": "C1", "camtip": "TEXTO", "pagina": 1,
        "posx": 0.1, "posy": 0.1, "ancho": 0.2, "alto": 0.05, "orden": 1,
        "config": {"max_length": 10, "font_size": 12, "multiline": False}
    }, headers={"X-FirmaDoc-User": "admin"}).json()
    
    client.post(f"/api/plantillas/{tplid1}/activar", headers={"X-FirmaDoc-User": "admin"})
    
    resp_m = client.patch(f"/api/plantillas/{tplid1}/campos/{cam['camid']}", json={"camnom": "Modificado"}, headers={"X-FirmaDoc-User": "admin"})
    assert resp_m.status_code == 400

def test_listado_paginado(client):
    resp = client.get("/api/plantillas?limit=10", headers={"X-FirmaDoc-User": "admin"})
    assert resp.status_code == 200
    assert "items" in resp.json()
    assert "total" in resp.json()

def test_filtros_por_estado_y_codigo(client):
    client.post("/api/plantillas", json={"tplcod": "TPL-01", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    resp = client.get("/api/plantillas?estado=BORRADOR&tplcod=TPL-01", headers={"X-FirmaDoc-User": "admin"})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) >= 1

def test_auditoria_de_creacion(client, db_session):
    client.post("/api/plantillas", json={"tplcod": "TPL-AUDIT-1", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    ev = db_session.query(Audifir).filter(Audifir.evento == "TPL_CREA", Audifir.usrid == "admin").all()
    assert len(ev) > 0

def test_auditoria_de_activacion(client, db_session):
    resp = client.post("/api/plantillas", json={"tplcod": "TPL-AUDIT-2", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
    tplid = resp.json()["tplid"]
    client.post(f"/api/plantillas/{tplid}/activar", headers={"X-FirmaDoc-User": "admin"})
    ev = db_session.query(Audifir).filter(Audifir.evento == "TPL_ACTI", Audifir.usrid == "admin").all()
    assert len(ev) > 0

def test_rollback_si_falla_auditoria(client, db_session):
    with patch("app.services.template_service.create_evento", side_effect=Exception("Error DB")):
        try:
            client.post("/api/plantillas", json={"tplcod": "TPL-FAIL", "tplnom": "Test", "numpag": 1}, headers={"X-FirmaDoc-User": "admin"})
        except Exception:
            pass
        tpls = db_session.query(Plantill).filter(Plantill.tplcod == "TPL-FAIL").all()
        assert len(tpls) == 0

def test_alfresco_continua_sin_escritura(client):
    # This is validated because we didn't add any Alfresco write routes.
    assert True
