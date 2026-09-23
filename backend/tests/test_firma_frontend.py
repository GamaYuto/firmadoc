import base64
import hashlib
import tempfile
from uuid import uuid4

import pytest
from sqlalchemy import select

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from app.models.docpart import DocPart
from app.models.docpaso import DocPaso
from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.firpos import Firpos
from app.models.sesionqr import SesionQr
from app.schemas.alfresco import NodeMetadata
import app.services.frontend_signature_service as frontend_signature_module
import app.api.firma_frontend as firma_frontend_api
from app.services.pdf_signature_service import pdf_signature_service
from app.services.signature_exceptions import SignatureWriteDisabledError
from app.services.temporary_artifact_service import TemporaryArtifactService
from app.services.frontend_signature_service import frontend_signature_service


def _pdf_bytes(page_count: int = 2) -> bytes:
    doc = fitz.open()
    for index in range(page_count):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"Documento sintetico FirmaDoc pagina {index + 1}", fontsize=14)
    data = doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    doc.close()
    return data


def _png_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=120, height=50)
    page.draw_line((10, 30), (100, 20), color=(0, 0, 0), width=3)
    pix = page.get_pixmap(alpha=True)
    data = pix.tobytes("png")
    doc.close()
    return data


@pytest.fixture
def alfresco_mock(monkeypatch):
    node_id = uuid4()
    pdf = _pdf_bytes()
    sha = hashlib.sha256(pdf).hexdigest()

    async def fake_metadata(requested_node_id):
        assert str(requested_node_id) == str(node_id)
        return NodeMetadata(
            node_id=node_id,
            name="sintetico.pdf",
            node_type="cm:content",
            is_file=True,
            mime_type="application/pdf",
            size_bytes=len(pdf),
            version_label="1.0",
        )

    async def fake_download(requested_node_id):
        assert str(requested_node_id) == str(node_id)
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        handle.write(pdf)
        handle.close()
        return handle.name, len(pdf), sha

    monkeypatch.setattr(frontend_signature_service.alfresco_client, "get_node_metadata", fake_metadata)
    monkeypatch.setattr(frontend_signature_service.alfresco_client, "download_node_content", fake_download)
    return {"node_id": node_id, "pdf": pdf, "sha": sha}


@pytest.fixture
def isolated_artifacts(monkeypatch, tmp_path):
    service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    monkeypatch.setattr(frontend_signature_module, "temporary_artifact_service", service)
    monkeypatch.setattr(pdf_signature_service, "artifact_service", service)
    return service


def _pdf_text(pdf_path, page_number: int) -> str:
    with fitz.open(pdf_path) as doc:
        return " ".join(doc[page_number].get_text("text").split())


def _pdf_image_count(pdf_path, page_number: int) -> int:
    with fitz.open(pdf_path) as doc:
        return len(doc[page_number].get_images(full=True))
    
def _pdf_image_hashes(pdf_path, page_number: int) -> set[str]:
    with fitz.open(pdf_path) as doc:
        return {
            hashlib.sha256(
                doc.extract_image(image[0])["image"]
            ).hexdigest()
            for image in doc[page_number].get_images(full=True)
        }


def _pdf_render_hash(pdf_path, page_number: int) -> str:
    with fitz.open(pdf_path) as doc:
        pixmap = doc[page_number].get_pixmap(alpha=False)

        payload = (
            f"{pixmap.width}x{pixmap.height}:"
            f"{pixmap.n}:"
            f"{pixmap.stride}:"
        ).encode("ascii") + pixmap.samples

        return hashlib.sha256(payload).hexdigest()


def _prepare_save_send(client, node_id, tipfir="INTERNA", signer="firmante"):
    headers = {"X-FirmaDoc-User": "preparador"}
    response = client.get(f"/api/firma/preparacion/{node_id}", headers=headers)
    assert response.status_code == 200, response.text
    prep = response.json()
    payload = {
        "participants": [{"usrid": signer, "orden": 1, "obliga": True}],
        "positions": [
            {
                "pagina": 1,
                "posx": 72,
                "posy": 96,
                "ancho": 180,
                "alto": 70,
                "rotaci": 0,
                "orden": 1,
                "tipfir": tipfir,
                "usrid": signer,
            }
        ],
    }
    saved = client.post(f"/api/firma/preparacion/{prep['docid']}/borrador", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    sent = client.post(f"/api/firma/preparacion/{prep['docid']}/enviar", headers=headers)
    assert sent.status_code == 200, sent.text
    return prep, saved.json(), sent.json()


def test_preparador_genera_qr_asignado_a_otro_firmante(client, db_session, alfresco_mock):
    _prep, _saved, sent = _prepare_save_send(
        client,
        alfresco_mock["node_id"],
        tipfir="MANUSCRITA",
        signer="firmante",
    )

    detail = client.get(
        f"/api/firma/firmas/{sent['firid']}",
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert detail.status_code == 200, detail.text

    unrelated = client.get(
        f"/api/firma/firmas/{sent['firid']}",
        headers={"X-FirmaDoc-User": "usuario_ajeno"},
    )
    assert unrelated.status_code == 403

    forbidden_qr = client.post(
        f"/api/firma/firmas/{sent['firid']}/qr",
        headers={"X-FirmaDoc-User": "usuario_ajeno"},
    )
    assert forbidden_qr.status_code == 403

    created = client.post(
        f"/api/firma/firmas/{sent['firid']}/qr",
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert created.status_code == 200, created.text

    sesion = db_session.get(SesionQr, created.json()["sesid"])
    assert sesion is not None
    assert sesion.usrid == "firmante"

    forbidden = client.post(
        "/api/firma/movil/confirmar",
        json={"token": created.json()["token"], "png_data_url": "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii")},
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert forbidden.status_code == 403


def test_conflicto_pendiente_publicacion_permite_cancelar_y_recrear(client, db_session, alfresco_mock):
    _prep, _saved, sent = _prepare_save_send(
        client,
        alfresco_mock["node_id"],
        tipfir="INTERNA",
        signer="preparador",
    )
    completed = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-interna",
        json={"confirm": True},
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["document_status"] == EstadoDoc.PENDIENTE_PUBLICACION.value

    start_payload = {
        "node_id": str(alfresco_mock["node_id"]),
        "signer_user_id": "yo",
        "page": 1,
        "posx": 72,
        "posy": 96,
        "width": 180,
        "height": 70,
    }
    conflict = client.post(
        "/api/firma/iniciar",
        json=start_payload,
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "ACTIVE_PROCESS",
        "message": "Ya existe un proceso activo para este documento",
        "docid": sent["docid"],
        "status": EstadoDoc.PENDIENTE_PUBLICACION.value,
        "firid": sent["firid"],
        "signature_status": EstadoDocFirma.COMPLETADA.value,
    }

    cancelled = client.post(
        f"/api/documentos/{sent['docid']}/cancelar",
        json={"motivo": "Reemplazar proceso anterior"},
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["estado"] == EstadoDoc.CANCELADO.value

    recreated = client.post(
        "/api/firma/iniciar",
        json=start_payload,
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["docid"] != sent["docid"]


def test_cancelar_proceso_invalida_pendientes_y_qr(client, db_session, alfresco_mock):
    _prep, _saved, sent = _prepare_save_send(
        client,
        alfresco_mock["node_id"],
        tipfir="MANUSCRITA",
        signer="firmante",
    )
    created = client.post(
        f"/api/firma/firmas/{sent['firid']}/qr",
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert created.status_code == 200, created.text

    cancelled = client.post(
        f"/api/documentos/{sent['docid']}/cancelar",
        json={"motivo": "Reemplazar proceso anterior"},
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert cancelled.status_code == 200, cancelled.text

    firma = db_session.get(DocFirma, sent["firid"])
    participante = db_session.get(DocPart, sent["parid"])
    paso = db_session.scalar(select(DocPaso).where(DocPaso.docid == sent["docid"]))
    sesion = db_session.get(SesionQr, created.json()["sesid"])

    assert firma.estado == EstadoDocFirma.CANCELADA.value
    assert participante.estado == "CANCELADO"
    assert paso.estado == "CANCELADO"
    assert sesion.estado == "CANCELADO"

    pending = client.get(
        "/api/firma/pendientes",
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert pending.status_code == 200
    assert pending.json()["total"] == 0


def _prepare_save_send_two_signers(
    client,
    node_id,
    *,
    internal_page: int = 2,
    internal_posx: float = 80,
    internal_posy: float = 120,
):
    headers = {"X-FirmaDoc-User": "preparador"}
    response = client.get(f"/api/firma/preparacion/{node_id}", headers=headers)
    assert response.status_code == 200, response.text
    prep = response.json()
    payload = {
        "participants": [
            {"usrid": "firmante", "orden": 1, "obliga": True},
            {"usrid": "interno", "orden": 2, "obliga": True},
        ],
        "positions": [
            {
                "pagina": 1,
                "posx": 72,
                "posy": 96,
                "ancho": 180,
                "alto": 70,
                "rotaci": 0,
                "orden": 1,
                "tipfir": "MANUSCRITA",
                "usrid": "firmante",
            },
            {
                "pagina": internal_page,
                "posx": internal_posx,
                "posy": internal_posy,
                "ancho": 160,
                "alto": 60,
                "rotaci": 0,
                "orden": 2,
                "tipfir": "INTERNA",
                "usrid": "interno",
            },
        ],
    }
    saved = client.post(f"/api/firma/preparacion/{prep['docid']}/borrador", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    sent = client.post(f"/api/firma/preparacion/{prep['docid']}/guardar-enviar", json=payload, headers=headers)
    assert sent.status_code == 200, sent.text
    return prep, saved.json(), sent.json()


def test_preparacion_guarda_borrador_y_congela_firpos(client, db_session, alfresco_mock):
    prep, saved, sent = _prepare_save_send(client, alfresco_mock["node_id"], tipfir="INTERNA")

    assert prep["document_name"] == "sintetico.pdf"
    assert len(prep["pages"]) == 2
    assert saved["positions"][0]["posx"] == 72
    assert sent["status"] == EstadoDocFirma.INICIADA.value

    firma = db_session.get(DocFirma, sent["firid"])
    assert firma is not None
    assert firma.tipfir == TipoFirma.INTERNA.value
    pos = db_session.scalars(select(Firpos).where(Firpos.firid == sent["firid"])).first()
    assert pos is not None
    assert float(pos.posx) == 72
    assert float(pos.alto) == 70


def test_pendiente_solo_lo_ve_el_firmante_asignado(client, alfresco_mock):
    _prep, _saved, sent = _prepare_save_send(client, alfresco_mock["node_id"], tipfir="INTERNA", signer="firmante")

    ok = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": "firmante"})
    assert ok.status_code == 200
    assert ok.json()["total"] == 1
    assert ok.json()["items"][0]["firid"] == sent["firid"]

    other = client.get("/api/firma/firmas/%s" % sent["firid"], headers={"X-FirmaDoc-User": "otro"})
    assert other.status_code == 403


def test_confirmar_firma_interna_genera_pdf_sin_publicar(client, db_session, alfresco_mock):
    _prep, _saved, sent = _prepare_save_send(client, alfresco_mock["node_id"], tipfir="INTERNA", signer="firmante")

    response = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-interna",
        json={"confirm": True},
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == EstadoDocFirma.COMPLETADA.value
    assert data["document_status"] == EstadoDoc.PENDIENTE_PUBLICACION.value
    assert data["alfresco_publication"] == "PENDING"
    assert data["completed_signatures"] == 1
    assert data["total_signatures"] == 1
    assert data["final_version"] == "1.0"
    assert data["final_hash_short"]

    db_session.expire_all()
    firma = db_session.get(DocFirma, sent["firid"])
    assert firma.estado == EstadoDocFirma.COMPLETADA.value
    assert firma.verfin == "1.0"
    assert firma.hasfin is not None
    doc = db_session.get(DocFir, sent["docid"])
    assert doc is not None
    assert doc.estado == EstadoDoc.PENDIENTE_PUBLICACION.value
    assert doc.verfin is None


def test_firma_manuscrita_vacia_rechazada(client, alfresco_mock):
    _prep, _saved, sent = _prepare_save_send(client, alfresco_mock["node_id"], tipfir="MANUSCRITA", signer="firmante")

    response = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-manuscrita",
        json={"png_data_url": "data:image/png;base64,AAAA"},
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert response.status_code == 422


def test_firma_manuscrita_valida_genera_pdf_sin_publicar(client, db_session, alfresco_mock):
    _prep, _saved, sent = _prepare_save_send(client, alfresco_mock["node_id"], tipfir="MANUSCRITA", signer="firmante")
    import base64

    response = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-manuscrita",
        json={"png_data_url": "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii")},
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == EstadoDocFirma.COMPLETADA.value
    assert data["document_status"] == EstadoDoc.PENDIENTE_PUBLICACION.value
    assert data["alfresco_publication"] == "PENDING"
    assert data["completed_signatures"] == 1
    assert data["total_signatures"] == 1
    db_session.expire_all()
    firma = db_session.get(DocFirma, sent["firid"])
    assert firma.estado == EstadoDocFirma.COMPLETADA.value
    assert firma.verfin == "1.0"
    doc = db_session.get(DocFir, sent["docid"])
    assert doc is not None
    assert doc.estado == EstadoDoc.PENDIENTE_PUBLICACION.value
    assert doc.verfin is None


def test_posicion_fuera_de_limites_rechazada(client, alfresco_mock):
    headers = {"X-FirmaDoc-User": "preparador"}
    response = client.get(f"/api/firma/preparacion/{alfresco_mock['node_id']}", headers=headers)
    assert response.status_code == 200
    docid = response.json()["docid"]
    payload = {
        "participants": [{"usrid": "firmante", "orden": 1, "obliga": True}],
        "positions": [
            {
                "pagina": 1,
                "posx": 590,
                "posy": 830,
                "ancho": 100,
                "alto": 50,
                "rotaci": 0,
                "orden": 1,
                "tipfir": "INTERNA",
                "usrid": "firmante",
            }
        ],
    }
    saved = client.post(f"/api/firma/preparacion/{docid}/borrador", json=payload, headers=headers)
    assert saved.status_code == 422


def test_borrador_conserva_dos_posiciones_con_tipos_distintos(client, alfresco_mock):
    headers = {"X-FirmaDoc-User": "preparador"}
    response = client.get(f"/api/firma/preparacion/{alfresco_mock['node_id']}", headers=headers)
    assert response.status_code == 200
    docid = response.json()["docid"]
    payload = {
        "participants": [
            {"usrid": "firmante", "orden": 1, "obliga": True},
            {"usrid": "interno", "orden": 2, "obliga": True},
        ],
        "positions": [
            {
                "pagina": 1,
                "posx": 72,
                "posy": 96,
                "ancho": 180,
                "alto": 70,
                "rotaci": 0,
                "orden": 1,
                "tipfir": "MANUSCRITA",
                "usrid": "firmante",
            },
            {
                "pagina": 2,
                "posx": 80,
                "posy": 120,
                "ancho": 160,
                "alto": 60,
                "rotaci": 0,
                "orden": 2,
                "tipfir": "INTERNA",
                "usrid": "interno",
            },
        ],
    }

    saved = client.post(f"/api/firma/preparacion/{docid}/borrador", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    positions = saved.json()["positions"]
    assert {(pos["usrid"], pos["tipfir"], pos["orden"]) for pos in positions} == {
        ("firmante", "MANUSCRITA", 1),
        ("interno", "INTERNA", 2),
    }


def test_guardar_no_sobrescribe_propiedades_y_eliminacion_persiste(client, alfresco_mock):
    headers = {"X-FirmaDoc-User": "preparador"}
    response = client.get(f"/api/firma/preparacion/{alfresco_mock['node_id']}", headers=headers)
    docid = response.json()["docid"]
    base_payload = {
        "participants": [
            {"usrid": "firmante", "orden": 1, "obliga": True},
            {"usrid": "interno", "orden": 2, "obliga": True},
        ],
        "positions": [
            {
                "pagina": 1,
                "posx": 72,
                "posy": 96,
                "ancho": 180,
                "alto": 70,
                "rotaci": 0,
                "orden": 1,
                "tipfir": "MANUSCRITA",
                "usrid": "firmante",
            },
            {
                "pagina": 2,
                "posx": 90,
                "posy": 140,
                "ancho": 150,
                "alto": 55,
                "rotaci": 0,
                "orden": 2,
                "tipfir": "INTERNA",
                "usrid": "interno",
            },
        ],
    }
    saved = client.post(f"/api/firma/preparacion/{docid}/borrador", json=base_payload, headers=headers)
    assert saved.status_code == 200, saved.text

    trimmed_payload = {
        "participants": [{"usrid": "interno", "orden": 2, "obliga": True}],
        "positions": [base_payload["positions"][1]],
    }
    trimmed = client.post(f"/api/firma/preparacion/{docid}/borrador", json=trimmed_payload, headers=headers)
    assert trimmed.status_code == 200, trimmed.text
    assert trimmed.json()["positions"] == [
        {
            "pagina": 2,
            "posx": 90.0,
            "posy": 140.0,
            "ancho": 150.0,
            "alto": 55.0,
            "rotaci": 0,
            "orden": 2,
            "tipfir": "INTERNA",
            "usrid": "interno",
            "saved": True,
        }
    ]


def test_guardar_y_enviar_crea_pendiente_del_primer_orden(client, alfresco_mock):
    headers = {"X-FirmaDoc-User": "preparador"}
    response = client.get(f"/api/firma/preparacion/{alfresco_mock['node_id']}", headers=headers)
    docid = response.json()["docid"]
    payload = {
        "participants": [
            {"usrid": "firmante", "orden": 1, "obliga": True},
            {"usrid": "interno", "orden": 2, "obliga": True},
        ],
        "positions": [
            {
                "pagina": 1,
                "posx": 72,
                "posy": 96,
                "ancho": 180,
                "alto": 70,
                "rotaci": 0,
                "orden": 1,
                "tipfir": "MANUSCRITA",
                "usrid": "firmante",
            },
            {
                "pagina": 2,
                "posx": 90,
                "posy": 140,
                "ancho": 150,
                "alto": 55,
                "rotaci": 0,
                "orden": 2,
                "tipfir": "INTERNA",
                "usrid": "interno",
            },
        ],
    }
    sent = client.post(f"/api/firma/preparacion/{docid}/guardar-enviar", json=payload, headers=headers)
    assert sent.status_code == 200, sent.text
    assert sent.json()["positions"][0]["usrid"] == "firmante"
    assert sent.json()["positions"][0]["tipfir"] == "MANUSCRITA"

    pending = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": "firmante"})
    assert pending.status_code == 200
    assert pending.json()["total"] == 1


def test_preparacion_sin_identidad_laboratorio_rechazada(client, alfresco_mock):
    response = client.get(f"/api/firma/preparacion/{alfresco_mock['node_id']}")
    assert response.status_code == 401


def test_identidad_incorrecta_no_puede_firmar(client, alfresco_mock):
    _prep, _saved, sent = _prepare_save_send(client, alfresco_mock["node_id"], tipfir="INTERNA", signer="firmante")
    response = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-interna",
        json={"confirm": True},
        headers={"X-FirmaDoc-User": "otro"},
    )
    assert response.status_code == 403


def test_guardar_y_enviar_activacion_secuencial_y_pendientes(client, db_session, alfresco_mock):
    prep, _saved, sent = _prepare_save_send_two_signers(client, alfresco_mock["node_id"])

    firmante = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": " firmante "})
    assert firmante.status_code == 200, firmante.text
    assert firmante.json()["total"] == 1
    assert firmante.json()["items"][0]["parid"] == sent["parid"]

    interno = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": "interno"})
    assert interno.status_code == 200
    assert interno.json()["total"] == 0

    response = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-manuscrita",
        json={"png_data_url": "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii")},
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert response.status_code == 200, response.text

    after_firmante = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": "firmante"})
    assert after_firmante.status_code == 200
    assert after_firmante.json()["total"] == 0

    after_interno = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": "interno"})
    assert after_interno.status_code == 200
    assert after_interno.json()["total"] == 1
    assert after_interno.json()["items"][0]["docid"] == prep["docid"]

    step_id = db_session.scalar(select(DocPart.dpasid).where(DocPart.parid == sent["parid"]))
    partes = db_session.scalars(select(DocPart).where(DocPart.dpasid == step_id)).all()
    assert {part.usrid: part.estado for part in partes}["firmante"] == "COMPLETADO"
    assert {part.usrid: part.estado for part in partes}["interno"] == "DISPONIBLE"

    firmas = db_session.scalars(select(DocFirma).where(DocFirma.docid == prep["docid"]).order_by(DocFirma.firid)).all()
    assert [firma.estado for firma in firmas] == ["COMPLETADA", "INICIADA"]


def test_cierre_local_pendiente_publicacion_y_resultado_pdf(client, db_session, alfresco_mock):
    headers = {"X-FirmaDoc-User": "preparador"}
    prep, _saved, sent = _prepare_save_send_two_signers(client, alfresco_mock["node_id"])

    manuscrita = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-manuscrita",
        json={"png_data_url": "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii")},
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert manuscrita.status_code == 200, manuscrita.text

    second = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": "interno"})
    assert second.status_code == 200
    assert second.json()["total"] == 1
    second_firid = second.json()["items"][0]["firid"]

    cierre = client.post(
        f"/api/firma/firmas/{second_firid}/confirmar-interna",
        json={"confirm": True},
        headers={"X-FirmaDoc-User": "interno"},
    )
    assert cierre.status_code == 200, cierre.text
    cierre_data = cierre.json()
    assert cierre_data["status"] == EstadoDocFirma.COMPLETADA.value
    assert cierre_data["document_status"] == EstadoDoc.PENDIENTE_PUBLICACION.value
    assert cierre_data["alfresco_publication"] == "PENDING"
    assert cierre_data["completed_signatures"] == 2
    assert cierre_data["total_signatures"] == 2
    assert cierre_data["final_version"] == "1.0"

    pdf_ok = client.get(
        f"/api/firma/firmas/{second_firid}/resultado/pdf",
        headers={"X-FirmaDoc-User": "interno"},
    )
    assert pdf_ok.status_code == 200, pdf_ok.text
    assert pdf_ok.headers["content-type"].startswith("application/pdf")
    assert pdf_ok.content.startswith(b"%PDF")
    assert "C:\\\\" not in pdf_ok.headers.get("content-disposition", "")

    pdf_forbidden = client.get(
        f"/api/firma/firmas/{second_firid}/resultado/pdf",
        headers={"X-FirmaDoc-User": "otro"},
    )
    assert pdf_forbidden.status_code == 403

    prep_after = client.get(f"/api/firma/preparacion/{alfresco_mock['node_id']}", headers=headers)
    assert prep_after.status_code == 200
    assert prep_after.json()["status"] == EstadoDoc.PENDIENTE_PUBLICACION.value
    assert prep_after.json()["active_firid"] is None

    save_after = client.post(
        f"/api/firma/preparacion/{prep_after.json()['docid']}/borrador",
        json={
            "participants": [{"usrid": "firmante", "orden": 1, "obliga": True}],
            "positions": [
                {
                    "pagina": 1,
                    "posx": 72,
                    "posy": 96,
                    "ancho": 180,
                    "alto": 70,
                    "rotaci": 0,
                    "orden": 1,
                    "tipfir": "MANUSCRITA",
                    "usrid": "firmante",
                }
            ],
        },
        headers=headers,
    )
    assert save_after.status_code == 409

    send_after = client.post(
        f"/api/firma/preparacion/{prep_after.json()['docid']}/enviar",
        headers=headers,
    )
    assert send_after.status_code == 409

    db_session.expire_all()
    doc = db_session.get(DocFir, prep["docid"])
    assert doc is not None
    assert doc.estado == EstadoDoc.PENDIENTE_PUBLICACION.value
    assert doc.verfin is None

    firmas = db_session.scalars(select(DocFirma).where(DocFirma.docid == prep["docid"]).order_by(DocFirma.firid)).all()
    assert firmas[-1].hasfin is not None
    assert [firma.estado for firma in firmas] == [EstadoDocFirma.COMPLETADA.value, EstadoDocFirma.COMPLETADA.value]


def test_publicar_alfresco_rechaza_firmante_y_bloquea_preparador(client, alfresco_mock, monkeypatch):
    _prep, _saved, sent = _prepare_save_send(client, alfresco_mock["node_id"], tipfir="INTERNA", signer="firmante")
    signed = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-interna",
        json={"confirm": True},
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["can_publish_alfresco"] is False

    preparador_result = client.get(
        f"/api/firma/firmas/{sent['firid']}/resultado",
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert preparador_result.status_code == 200, preparador_result.text
    assert preparador_result.json()["can_publish_alfresco"] is True
    assert preparador_result.json()["alfresco_write_enabled"] is False

    class FakePublicationService:
        def publish_document(self, db, docid, actor_user, iporig=None, user_agent=None):
            if actor_user == "firmante":
                from app.services.signature_exceptions import SignaturePublicationError

                raise SignaturePublicationError(
                    "No autorizado para publicar este documento",
                    code="PUBLICATION_FORBIDDEN",
                    status_code=403,
                    source_version="1.0",
                )
            raise SignatureWriteDisabledError(operation_id="op-test", source_version="1.0")

    monkeypatch.setattr(firma_frontend_api, "AlfrescoService", lambda: FakePublicationService())

    forbidden = client.post(
        f"/api/firma/documentos/{sent['docid']}/publicar",
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "PUBLICATION_FORBIDDEN"

    blocked = client.post(
        f"/api/firma/documentos/{sent['docid']}/publicar",
        headers={"X-FirmaDoc-User": "preparador"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "ALFRESCO_WRITE_DISABLED"
    assert "C:\\\\" not in blocked.text
    assert "lab_password" not in blocked.text


def test_firma_secuencial_reusa_acumulado_y_conserva_ambas_firmas(client, alfresco_mock, isolated_artifacts, monkeypatch):
    _prep, _saved, sent = _prepare_save_send_two_signers(
        client,
        alfresco_mock["node_id"],
        internal_page=1,
        internal_posx=330,
        internal_posy=96,
    )
    
    first_response = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-manuscrita",
        json={"png_data_url": "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii")},
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert first_response.status_code == 200, first_response.text
    assert first_response.json()["message"] == "Firma registrada. Pendiente del siguiente firmante."

    first_artifact = isolated_artifacts.find_latest_path(prefix=f"fir-{sent['firid']}-", suffix=".pdf")
    assert first_artifact is not None
    assert first_artifact.exists()

    with fitz.open(first_artifact) as first_pdf:
        first_page1_text = " ".join(first_pdf[0].get_text("text").split())
        first_page2_text = " ".join(first_pdf[1].get_text("text").split())
        first_page1_images = len(first_pdf[0].get_images(full=True))
        first_page1_image_hashes = _pdf_image_hashes(
            first_artifact,
            0,
        )
        first_page2_render_hash = _pdf_render_hash(
            first_artifact,
            1,
        )

    assert first_page1_images == 1
    assert "FIRMADO ELECTRONICAMENTE" not in first_page1_text

    def _unexpected_download(*args, **kwargs):
        raise AssertionError("La segunda firma no debe descargar nuevamente el original de Alfresco")

    monkeypatch.setattr(frontend_signature_service.alfresco_client, "download_node_content", _unexpected_download)

    second = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": "interno"})
    assert second.status_code == 200, second.text
    assert second.json()["total"] == 1
    second_firid = second.json()["items"][0]["firid"]

    second_response = client.post(
        f"/api/firma/firmas/{second_firid}/confirmar-interna",
        json={"confirm": True},
        headers={"X-FirmaDoc-User": "interno"},
    )
    assert second_response.status_code == 200, second_response.text
    assert second_response.json()["message"] == "Firma registrada. Documento consolidado completado y pendiente de publicacion en Alfresco."
    assert second_response.json()["document_status"] == EstadoDoc.PENDIENTE_PUBLICACION.value

    final_artifact = isolated_artifacts.find_latest_path(prefix=f"fir-{second_firid}-", suffix=".pdf")
    assert final_artifact is not None
    assert final_artifact.exists()
    assert not first_artifact.exists()

    pdf_ok = client.get(
        f"/api/firma/firmas/{second_firid}/resultado/pdf",
        headers={"X-FirmaDoc-User": "interno"},
    )
    assert pdf_ok.status_code == 200, pdf_ok.text
    assert pdf_ok.content.startswith(b"%PDF")

    with fitz.open(final_artifact) as final_pdf:
        final_page1_text = " ".join(final_pdf[0].get_text("text").split())
        final_page2_text = " ".join(final_pdf[1].get_text("text").split())
        final_page1_images = len(final_pdf[0].get_images(full=True))
    final_page1_image_hashes = _pdf_image_hashes(
        final_artifact,
        0,
    )
    final_page2_render_hash = _pdf_render_hash(
        final_artifact,
        1,
    )

    assert final_page1_images == first_page1_images
    assert first_page1_image_hashes.issubset(final_page1_image_hashes)
    assert "FIRMADO ELECTRÓNICAMENTE" in final_page1_text
    assert "Usuario Interno" in final_page1_text
    assert final_page2_text == first_page2_text
    assert final_page2_render_hash == first_page2_render_hash


def test_firma_secuencial_falla_si_no_existe_acumulado_prev(client, alfresco_mock, isolated_artifacts, monkeypatch):
    _prep, _saved, sent = _prepare_save_send_two_signers(client, alfresco_mock["node_id"])

    first_response = client.post(
        f"/api/firma/firmas/{sent['firid']}/confirmar-manuscrita",
        json={"png_data_url": "data:image/png;base64," + base64.b64encode(_png_bytes()).decode("ascii")},
        headers={"X-FirmaDoc-User": "firmante"},
    )
    assert first_response.status_code == 200, first_response.text

    first_artifact = isolated_artifacts.find_latest_path(prefix=f"fir-{sent['firid']}-", suffix=".pdf")
    assert first_artifact is not None
    isolated_artifacts.cleanup_path(first_artifact)
    assert not first_artifact.exists()

    def _unexpected_download(*args, **kwargs):
        raise AssertionError("No debe usarse el original cuando falta el acumulado esperado")

    monkeypatch.setattr(frontend_signature_service.alfresco_client, "download_node_content", _unexpected_download)

    second = client.get("/api/firma/pendientes", headers={"X-FirmaDoc-User": "interno"})
    assert second.status_code == 200, second.text
    second_firid = second.json()["items"][0]["firid"]

    second_response = client.post(
        f"/api/firma/firmas/{second_firid}/confirmar-interna",
        json={"confirm": True},
        headers={"X-FirmaDoc-User": "interno"},
    )
    assert second_response.status_code == 410, second_response.text
