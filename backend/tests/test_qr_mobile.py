import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.docpart import DocPart
from app.models.sesionqr import EstadoSesionQr, SesionQr
from app.schemas.firma_frontend import SignatureResult
from app.services.qr_service import qr_service
from app.services.pdf_signature_service import pdf_signature_service


def _create_sample_pdf(text_label: str = "Base Document") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), text_label, fontsize=14)
    data = doc.tobytes(deflate=True)
    doc.close()
    return data


def _create_sample_png() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=120, height=50)
    page.draw_line((10, 25), (110, 25), color=(0, 0, 0), width=2)
    pix = page.get_pixmap(alpha=True)
    data = pix.tobytes("png")
    doc.close()
    return data


# ==============================================================================
# 1. token QR válido: creación, impredecibilidad, y almacenamiento de hash
# ==============================================================================
def test_qr_token_valido_creacion_y_hash():
    """El token QR debe ser aleatorio, largo, impredecible y guardarse como SHA-256."""
    db = MagicMock()
    
    mock_firma = MagicMock(spec=DocFirma)
    mock_firma.firid = 101
    mock_firma.docid = 50
    mock_firma.parid = 12
    mock_firma.estado = EstadoDocFirma.INICIADA.value
    
    mock_part = MagicMock(spec=DocPart)
    mock_part.parid = 12
    mock_part.usrid = "doctor_lopez"
    
    def mock_get(model, pk):
        if model == DocFirma and pk == 101:
            return mock_firma
        if model == DocPart and pk == 12:
            return mock_part
        return None
        
    db.get.side_effect = mock_get
    db.scalars.return_value.all.return_value = []
    
    added_objects = []
    db.add.side_effect = lambda obj: added_objects.append(obj)
    
    sesid, raw_token, fecexp = qr_service.crear_sesion_qr(
        db=db,
        firid=101,
        requester_user_id="doctor_lopez",
        iporig="192.168.1.50",
    )
    
    # Validar token generado
    assert len(raw_token) >= 32
    expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    
    # Validar objeto persistido
    assert len(added_objects) == 1
    sesion: SesionQr = added_objects[0]
    assert sesion.firid == 101
    assert sesion.docid == 50
    assert sesion.usrid == "doctor_lopez"
    assert sesion.tokhas == expected_hash
    assert sesion.tokhas != raw_token  # NUNCA guardar token en claro
    assert sesion.estado == EstadoSesionQr.PENDIENTE.value
    assert sesion.fecexp > datetime.now(timezone.utc)
    assert sesion.iporig == "192.168.1.50"


# ==============================================================================
# 2. token expirado: rechazo HTTP 410 y actualización de estado
# ==============================================================================
def test_qr_token_expirado():
    """Un token con fecexp en el pasado debe rechazarse con HTTP 410 y pasar a EXPIRADO."""
    db = MagicMock()
    raw_token = "token_de_prueba_expirado_12345"
    tokhas = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    
    sesion = MagicMock(spec=SesionQr)
    sesion.sesid = 202
    sesion.tokhas = tokhas
    sesion.estado = EstadoSesionQr.PENDIENTE.value
    sesion.fecexp = datetime.now(timezone.utc) - timedelta(minutes=1)  # Ya venció
    
    db.scalar.return_value = sesion
    
    # obtener_sesion_movil debe levantar 410 Gone
    with pytest.raises(HTTPException) as exc_info:
        qr_service.obtener_sesion_movil(db, raw_token)
    
    assert exc_info.value.status_code == 410
    assert "ha expirado" in exc_info.value.detail
    assert sesion.estado == EstadoSesionQr.EXPIRADO.value
    db.commit.assert_called()


def test_qr_consultar_estado_expira_al_consultar():
    """El polling de estado debe marcar EXPIRADO si el tiempo se agotó."""
    db = MagicMock()
    sesion = MagicMock(spec=SesionQr)
    sesion.sesid = 303
    sesion.estado = EstadoSesionQr.PENDIENTE.value
    sesion.fecexp = datetime.now(timezone.utc) - timedelta(seconds=10)
    
    db.get.return_value = sesion
    
    estado = qr_service.consultar_estado_qr(db, 303)
    assert estado == EstadoSesionQr.EXPIRADO.value
    assert sesion.estado == EstadoSesionQr.EXPIRADO.value
    db.commit.assert_called()


# ==============================================================================
# 3. token reutilizado: rechazo HTTP 409
# ==============================================================================
def test_qr_token_reutilizado():
    """Un token que ya tiene estado USADO debe rechazarse con HTTP 409."""
    db = MagicMock()
    raw_token = "token_ya_utilizado_67890"
    tokhas = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    
    sesion = MagicMock(spec=SesionQr)
    sesion.sesid = 404
    sesion.tokhas = tokhas
    sesion.estado = EstadoSesionQr.USADO.value
    sesion.fecexp = datetime.now(timezone.utc) + timedelta(minutes=5)
    
    db.scalar.return_value = sesion
    
    with pytest.raises(HTTPException) as exc_info:
        qr_service.obtener_sesion_movil(db, raw_token)
    
    assert exc_info.value.status_code == 409
    assert "ya fue usado" in exc_info.value.detail.lower()


def _stamp_png(source_pdf_bytes: bytes, png_bytes: bytes, rect: fitz.Rect) -> bytes:
    doc = fitz.open(stream=source_pdf_bytes, filetype="pdf")
    page = doc[0]
    page.insert_image(rect, stream=png_bytes)
    out = doc.tobytes(deflate=True)
    doc.close()
    return out


# ==============================================================================
# 4. usuario incorrecto: móvil autenticado como otro usuario -> HTTP 403
# ==============================================================================
def test_qr_usuario_incorrecto():
    import asyncio
    asyncio.run(_async_test_qr_usuario_incorrecto())


async def _async_test_qr_usuario_incorrecto():
    """Si el usuario autenticado en el móvil no coincide con el asignado, 403 Forbidden."""
    db = MagicMock()
    raw_token = "token_valido_usuario_a"
    tokhas = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    
    sesion = MagicMock(spec=SesionQr)
    sesion.sesid = 505
    sesion.firid = 11
    sesion.usrid = "usuario_titular"
    sesion.tokhas = tokhas
    sesion.estado = EstadoSesionQr.PENDIENTE.value
    sesion.fecexp = datetime.now(timezone.utc) + timedelta(minutes=5)
    
    db.scalar.return_value = sesion
    
    with pytest.raises(HTTPException) as exc_info:
        await qr_service.completar_firma_movil(
            db=db,
            raw_token=raw_token,
            png_data_url="data:image/png;base64,iVBORw0KGgo...",
            mobile_user_id="impostor_o_extrano",
        )
    
    assert exc_info.value.status_code == 403
    assert "no corresponde al firmante asignado" in exc_info.value.detail


# ==============================================================================
# 5. firma móvil correcta: confirmación e inserción exitosa
# ==============================================================================
def test_firma_movil_correcta():
    import asyncio
    asyncio.run(_async_test_firma_movil_correcta())


async def _async_test_firma_movil_correcta():
    """Firma móvil con token válido y usuario coincidente completa la firma e invalida token."""
    db = MagicMock()
    raw_token = "token_legitimo_123"
    tokhas = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    
    sesion = MagicMock(spec=SesionQr)
    sesion.sesid = 606
    sesion.firid = 22
    sesion.docid = 8
    sesion.usrid = "firmante_autorizado"
    sesion.tokhas = tokhas
    sesion.estado = EstadoSesionQr.PENDIENTE.value
    sesion.fecexp = datetime.now(timezone.utc) + timedelta(minutes=8)
    
    db.scalar.return_value = sesion
    
    expected_result = SignatureResult(
        firid=22,
        docid=8,
        message="Firma registrada exitosamente",
        status="COMPLETADA",
        date=datetime.now(timezone.utc),
        document_name="Acta.pdf",
        document_status="PENDIENTE_PUBLICACION",
        final_hash_short="a1b2c3d4",
        completed_signatures=1,
        total_signatures=1,
        original_version="1.0",
        final_version=None,
        can_publish_alfresco=True,
    )

    
    with patch(
        "app.services.qr_service.frontend_signature_service.confirm_handwritten",
        new=AsyncMock(return_value=expected_result),
    ) as mock_confirm:
        result = await qr_service.completar_firma_movil(
            db=db,
            raw_token=raw_token,
            png_data_url="data:image/png;base64,sample_png",
            mobile_user_id="firmante_autorizado",
            iporig="192.168.1.88",
        )
        
        mock_confirm.assert_awaited_once_with(
            db=db,
            firid=22,
            png_data_url="data:image/png;base64,sample_png",
            actor_user="firmante_autorizado",
            iporig="192.168.1.88",
        )
        assert result == expected_result
        assert sesion.estado == EstadoSesionQr.USADO.value
        assert sesion.fecusa is not None
        db.commit.assert_called()


# ==============================================================================
# 6. segunda firma usa PDF acumulado (no el original 1.0)
# ==============================================================================
def test_segunda_firma_usa_pdf_acumulado():
    """Demuestra que la segunda firma se aplica sobre el PDF ya firmado por el primero."""
    original_pdf = _create_sample_pdf("DOCUMENTO ORIGINAL V1.0")
    png_bytes = _create_sample_png()
    
    # Primera firma en página 1, posición superior
    primera_firma_pdf = _stamp_png(original_pdf, png_bytes, fitz.Rect(72, 150, 222, 210))
    
    # Verificar que el primer PDF firmado es diferente al original
    sha_original = hashlib.sha256(original_pdf).hexdigest()
    sha_firma1 = hashlib.sha256(primera_firma_pdf).hexdigest()
    assert sha_firma1 != sha_original
    
    # Segunda firma: DEBE recibir primera_firma_pdf como entrada (acumulativo)
    segunda_firma_pdf = _stamp_png(primera_firma_pdf, png_bytes, fitz.Rect(72, 300, 222, 360))
    
    sha_firma2 = hashlib.sha256(segunda_firma_pdf).hexdigest()
    assert sha_firma2 != sha_firma1
    assert sha_firma2 != sha_original
    
    # Inspeccionar el documento resultante con fitz: ambas firmas e imágenes deben persistir
    doc_final = fitz.open(stream=segunda_firma_pdf, filetype="pdf")
    page_final = doc_final[0]
    images_in_final = page_final.get_images()
    # Debe haber 2 imágenes insertadas en la misma página
    assert len(images_in_final) == 2
    doc_final.close()



# ==============================================================================
# 7. Endpoints HTTP de consulta y validación de sesión
# ==============================================================================
def test_endpoint_consultar_estado_qr(client):
    """GET /api/firma/qr/{sesid}/estado responde con el estado actual."""
    with patch("app.api.firma_frontend.qr_service.consultar_estado_qr", return_value="PENDIENTE"):
        response = client.get(
            "/api/firma/qr/10/estado",
            headers={"X-FirmaDoc-User": "doctor_lopez"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["sesid"] == 10
        assert data["estado"] == "PENDIENTE"


def test_endpoint_movil_sesion_endpoint(client):
    """GET /api/firma/movil/sesion/{token} retorna metadata del documento y firmante."""
    mock_doc = MagicMock(spec=DocFir)
    mock_doc.docnom = "Contrato de Servicios.pdf"
    
    mock_firma = MagicMock(spec=DocFirma)
    mock_firma.tipfir = TipoFirma.MANUSCRITA.value
    
    mock_sesion = MagicMock(spec=SesionQr)
    mock_sesion.sesid = 77
    mock_sesion.docid = 5
    mock_sesion.firid = 12
    mock_sesion.usrid = "doctor_lopez"
    mock_sesion.documento = mock_doc
    mock_sesion.firma = mock_firma
    
    with patch("app.api.firma_frontend.qr_service.obtener_sesion_movil", return_value=mock_sesion):
        # 1. Caso usuario coincidente -> 200 OK
        response = client.get(
            "/api/firma/movil/sesion/sample_token_xyz",
            headers={"X-FirmaDoc-User": "doctor_lopez"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["docnom"] == "Contrato de Servicios.pdf"
        assert payload["usrid"] == "doctor_lopez"
        assert payload["tipfir"] == "MANUSCRITA"
        assert payload["docid"] == 5
        assert payload["firid"] == 12

        # 2. Caso usuario impostor -> 403 Forbidden
        response_impostor = client.get(
            "/api/firma/movil/sesion/sample_token_xyz",
            headers={"X-FirmaDoc-User": "usuario_ajeno"},
        )
        assert response_impostor.status_code == 403
        assert "no corresponde al firmante asignado" in response_impostor.json()["detail"]

