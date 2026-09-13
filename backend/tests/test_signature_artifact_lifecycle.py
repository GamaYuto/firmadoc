from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from app.core.identity import IdentitySnapshot
from app.crud.crud_docfir import create_documento
from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.docpart import DocPart
from app.models.plantill import Plantill
from app.models.tplcamp import TplCamp
from app.schemas.docfirma import ResultContract, ResultFase, ResultFlag
from app.schemas.flujo import FlujoCreate, PasoCreate
from app.services.document_service import DocumentService
from app.services.flow_service import flow_service
from app.services.pdf_signature_service import PdfSignatureService
from app.services.signature_service import SignatureService
from app.services.step_service import step_service
from app.services.temporary_artifact_service import (
    TemporaryArtifactService,
    is_signature_artifact_protected,
)


def _make_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=12)
    doc.set_metadata({})
    data = doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    doc.close()
    return data


def _seed_document_flow(db, tmp_path: Path) -> dict[str, object]:
    temp_service = TemporaryArtifactService(
        base_dir=tmp_path / "artifacts",
        ttl_minutes=30,
        is_protected=is_signature_artifact_protected,
    )
    pdf_service = PdfSignatureService(artifact_service=temp_service)
    sig_service = SignatureService(artifact_service=temp_service)
    unique_suffix = uuid4().hex[:8]

    source_path = tmp_path / f"source_{unique_suffix}.pdf"
    source_bytes = _make_pdf_bytes(f"Contenido original para prueba {unique_suffix}")
    source_path.write_bytes(source_bytes)
    source_sha = hashlib.sha256(source_bytes).hexdigest()

    plantill_code = f"TPL-LC-{unique_suffix}"
    flow_code = f"F-LC-{unique_suffix}"
    step_code = f"P1-LC-{unique_suffix}"
    participant_user = f"firmante_{unique_suffix}"
    participant_name = f"Firmante Lifecycle {unique_suffix}"
    participant_email = f"firmante_{unique_suffix}@empresa.local"
    node_id = f"node-lc-{unique_suffix}"

    plantill = Plantill(
        tplcod=plantill_code,
        tplnom=f"Plantilla Lifecycle {unique_suffix}",
        tplver=1,
        numpag=1,
        estado="ACTIVA",
        usrcre="admin",
    )
    db.add(plantill)
    db.flush()

    camp = TplCamp(
        tplid=plantill.tplid,
        camcod=f"C1_{unique_suffix}",
        camnom=f"Firma {unique_suffix}",
        camtip="FIRMA",
        pagina=1,
        posx=0.1,
        posy=0.1,
        ancho=0.3,
        alto=0.1,
        orden=1,
        usrcre="admin",
    )
    db.add(camp)
    db.flush()

    flujo = flow_service.create_flow(
        db,
        FlujoCreate(flucod=flow_code, flunom=f"Flujo Lifecycle {unique_suffix}", usrcre="admin"),
    )
    flow_id = flujo.fluid
    flow_service.add_step(
        db,
        flow_id,
        PasoCreate(pascod=step_code, pasnom=f"Paso 1 {unique_suffix}", pastip="FIRMAR", orden=1, rolreq="Firmante"),
        "admin",
    )
    flow_service.activate_flow(db, flow_id, "admin")

    doc = create_documento(
        db=db,
        nodid=node_id,
        docnom=f"documento_{unique_suffix}.pdf",
        tamano=len(source_bytes),
        verini="1.0",
        hasori=source_sha,
        usrcre="admin",
    )
    doc.fluid = flow_id
    doc.tplid = plantill.tplid
    doc.estado = EstadoDoc.EN_CURSO.value
    db.flush()

    pasos = step_service.instantiate_document_steps(db, doc.docid, "admin")
    part = DocPart(
        dpasid=pasos[0].dpasid,
        usrid=participant_user,
        nomcom=participant_name,
        correo=participant_email,
        rolpro="Firmante",
        orden=1,
        obliga=True,
        estado="DISPONIBLE",
        verlock=1,
        usrcre="admin",
    )
    db.add(part)
    db.commit()

    actor = IdentitySnapshot(
        usrid=participant_user,
        nomcom=participant_name,
        correo=participant_email,
        rolpro="Firmante",
    )
    firma = sig_service.reserve_signature_attempt(
        db=db,
        parid=part.parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[
            {
                "pagina": 1,
                "posx": 72,
                "posy": 72,
                "ancho": 220,
                "alto": 100,
                "orden": 1,
                "camid": camp.camid,
            }
        ],
        verori="1.0",
        hasori=source_sha,
        actor=actor,
        expected_participant_verlock=1,
    )
    db.commit()

    generated_artifact = pdf_service.generate_signature_pdf_and_mark_generated(
        db=db,
        firid=firma.firid,
        source_pdf_path=source_path,
    )
    db.commit()

    db.expire_all()
    firma_row = db.get(DocFirma, firma.firid)
    part_row = db.get(DocPart, part.parid)
    doc_row = db.get(DocFir, doc.docid)

    return {
        "temp_service": temp_service,
        "pdf_service": pdf_service,
        "sig_service": sig_service,
        "source_path": source_path,
        "source_bytes": source_bytes,
        "source_sha": source_sha,
        "generated_artifact": generated_artifact,
        "firma": firma_row,
        "part": part_row,
        "doc": doc_row,
        "node_id": node_id,
        "actor": actor,
    }


def _advance_to_pending_publication(db, context: dict[str, object]) -> None:
    now = datetime.now(timezone.utc)
    firma: DocFirma = context["firma"]
    part: DocPart = context["part"]
    doc: DocFir = context["doc"]

    firma.estado = EstadoDocFirma.COMPLETADA.value
    firma.verfin = "1.1"
    firma.hasfin = context["generated_artifact"].sha256
    firma.fecfin = now
    firma.result = ResultContract(
        schema_ver=1,
        fase=ResultFase.FINALIZACION,
        remcod=200,
        remmsg="Firma completada localmente",
        recint=0,
        verchk=True,
        haschk=True,
        flags=[ResultFlag.HASH_MATCH],
    ).model_dump(mode="json")
    part.estado = "COMPLETADO"
    part.fecfin = now
    doc.estado = EstadoDoc.PENDIENTE_PUBLICACION.value
    doc.verfin = None
    doc.hasfir = context["generated_artifact"].sha256
    db.commit()
    db.expire_all()


def test_signature_artifact_young_and_pending_exists(db_session, tmp_path):
    """1. Un artefacto firmado joven en PENDIENTE_PUBLICACION existe y no se elimina."""
    ctx = _seed_document_flow(db_session, tmp_path)
    _advance_to_pending_publication(db_session, ctx)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    firid = ctx["firma"].firid

    artifact_path = temp_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf")
    assert artifact_path is not None
    assert artifact_path.exists()

    # Ejecutar limpieza normal
    temp_service.cleanup_expired()

    assert artifact_path.exists(), "El artefacto joven en PENDIENTE_PUBLICACION debe existir"


def test_signature_artifact_old_and_pending_persists_past_ttl(db_session, tmp_path):
    """2. Un artefacto firmado viejo (>30 min) en PENDIENTE_PUBLICACION sigue existiendo tras cleanup_expired."""
    ctx = _seed_document_flow(db_session, tmp_path)
    _advance_to_pending_publication(db_session, ctx)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    firid = ctx["firma"].firid

    artifact_path = temp_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf")
    assert artifact_path is not None
    assert artifact_path.exists()

    # Simular envejecimiento a 45 minutos en el pasado (TTL es 30 min)
    now = time.time()
    old_time = now - (45 * 60)
    os.utime(artifact_path, (old_time, old_time))
    assert artifact_path.stat().st_mtime < now - (30 * 60)

    # Invocar limpiador general
    temp_service.cleanup_expired()

    # El archivo DEBE seguir existiendo por la protección semántica de PENDIENTE_PUBLICACION
    assert artifact_path.exists(), (
        "El artefacto viejo en PENDIENTE_PUBLICACION DEBE preservarse y no eliminarse por TTL"
    )


def test_signature_artifact_deleted_after_verified_publication(db_session, tmp_path):
    """3. Un artefacto publicado y verificado se elimina definitivamente."""
    ctx = _seed_document_flow(db_session, tmp_path)
    _advance_to_pending_publication(db_session, ctx)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    firid = ctx["firma"].firid

    artifact_path = temp_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf")
    assert artifact_path is not None
    assert artifact_path.exists()

    # Simular publicación verificada exitosa en Alfresco
    doc: DocFir = ctx["doc"]
    doc.estado = EstadoDoc.COMPLETADO.value
    doc.verfin = "1.1"
    db_session.commit()

    # En el flujo normal, AlfrescoService llama a cleanup_path(generated_pdf_path)
    temp_service.cleanup_path(artifact_path)
    assert not artifact_path.exists(), "El artefacto debe eliminarse tras verificarse la publicación"

    # Si se recreara o quedara remanente y venciera el TTL, tampoco estaría protegido
    recreated = temp_service.write_bytes(b"remanente", prefix=f"fir-{firid}-", suffix=".pdf")
    now = time.time()
    os.utime(recreated, (now - 35 * 60, now - 35 * 60))
    temp_service.cleanup_expired()
    assert not recreated.exists(), "Cualquier remanente de documento COMPLETADO debe limpiarse por TTL"


def test_signature_artifact_deleted_on_document_cancellation(db_session, tmp_path):
    """4. Un artefacto de un documento cancelado se elimina de inmediato."""
    ctx = _seed_document_flow(db_session, tmp_path)
    _advance_to_pending_publication(db_session, ctx)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    firid = ctx["firma"].firid
    docid = ctx["doc"].docid

    artifact_path = temp_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf")
    assert artifact_path is not None
    assert artifact_path.exists()

    # Cancelar el proceso documental
    doc_service = DocumentService(alfresco_client=None, artifact_service=temp_service)  # type: ignore[arg-type]
    cancelled_doc = doc_service.cancelar_proceso(
        db=db_session,
        docid=docid,
        motivo="Cancelación explícita de prueba",
        usrmod="admin",
        ip="127.0.0.1",
    )
    assert cancelled_doc.estado == EstadoDoc.CANCELADO.value

    # El artefacto debe haber sido eliminado explícitamente
    assert not artifact_path.exists(), "El artefacto debe eliminarse de inmediato al cancelar el proceso"


def test_signature_artifact_deleted_on_attempt_cancellation(db_session, tmp_path):
    """4b. Un artefacto de un intento de firma cancelado se elimina tras el commit exitoso."""
    ctx = _seed_document_flow(db_session, tmp_path)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    sig_service: SignatureService = ctx["sig_service"]
    firma: DocFirma = ctx["firma"]
    actor: IdentitySnapshot = ctx["actor"]

    artifact_path = temp_service.find_latest_path(prefix=f"fir-{firma.firid}-", suffix=".pdf")
    assert artifact_path is not None
    assert artifact_path.exists()

    # Cancelar el intento activo dentro de la transacción
    sig_service.cancel_signature_attempt(
        db=db_session,
        firid=firma.firid,
        expected_revnum=int(firma.revnum),
        motivo="Firma cancelada por el usuario",
        actor=actor,
    )

    # Antes del commit de la transacción, el artefacto todavía existe físicamente
    assert artifact_path.exists(), "El artefacto debe existir antes del commit de la cancelación"

    # Confirmar transacción
    db_session.commit()

    # Tras el commit exitoso, el propietario de la transacción ejecuta la limpieza física explícita
    sig_service.cleanup_attempt_artifacts(firma.firid)
    assert not artifact_path.exists(), "El artefacto debe eliminarse tras el commit exitoso"


def test_cancellation_preserves_artifact_when_commit_fails_or_rolls_back(db_session, tmp_path):
    """A. Si el commit falla o se hace rollback, el artefacto firmado NO se elimina físicamente."""
    from unittest.mock import patch
    from fastapi import HTTPException

    # 1. Probar en DocumentService.cancelar_proceso cuando commit() falla
    ctx = _seed_document_flow(db_session, tmp_path)
    _advance_to_pending_publication(db_session, ctx)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    firid = ctx["firma"].firid
    docid = ctx["doc"].docid

    artifact_path = temp_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf")
    assert artifact_path is not None and artifact_path.exists()

    doc_service = DocumentService(alfresco_client=None, artifact_service=temp_service)  # type: ignore[arg-type]

    # Simular fallo en db.commit
    with patch.object(db_session, "commit", side_effect=RuntimeError("Fallo simulado de commit en PostgreSQL")):
        with pytest.raises(HTTPException) as exc_info:
            doc_service.cancelar_proceso(
                db=db_session,
                docid=docid,
                motivo="Cancelación que va a fallar en commit",
                usrmod="admin",
                ip="127.0.0.1",
            )
        assert exc_info.value.status_code == 500

    # Demostrar que el artefacto PERMANECE intacto
    assert artifact_path.exists(), (
        "Regla transaccional: Si el commit falla, el PDF firmado NO debe ser eliminado físicamente"
    )

    # 2. Probar en SignatureService.cancel_signature_attempt cuando se hace rollback
    ctx2 = _seed_document_flow(db_session, tmp_path)
    temp_service2: TemporaryArtifactService = ctx2["temp_service"]
    sig_service2: SignatureService = ctx2["sig_service"]
    firma2: DocFirma = ctx2["firma"]
    actor2: IdentitySnapshot = ctx2["actor"]

    artifact_path2 = temp_service2.find_latest_path(prefix=f"fir-{firma2.firid}-", suffix=".pdf")
    assert artifact_path2 is not None and artifact_path2.exists()

    sig_service2.cancel_signature_attempt(
        db=db_session,
        firid=firma2.firid,
        expected_revnum=int(firma2.revnum),
        motivo="Cancelación que sufrirá rollback",
        actor=actor2,
    )

    # Simular aborto/rollback de la transacción
    db_session.rollback()

    # Demostrar que tras rollback el artefacto PERMANECE intacto
    assert artifact_path2.exists(), (
        "Regla transaccional: Si ocurre rollback, el PDF firmado NO debe ser eliminado físicamente"
    )


def test_cancel_attempt_rollback_does_not_cleanup_on_later_unrelated_commit(db_session, tmp_path):
    """B. Demostrar el riesgo: si cancel_signature_attempt registra after_commit y hay rollback,
    un commit posterior no relacionado en LA MISMA Session NO debe borrar el artefacto."""
    ctx = _seed_document_flow(db_session, tmp_path)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    sig_service: SignatureService = ctx["sig_service"]
    firma: DocFirma = ctx["firma"]
    actor: IdentitySnapshot = ctx["actor"]

    artifact_path = temp_service.find_latest_path(prefix=f"fir-{firma.firid}-", suffix=".pdf")
    assert artifact_path is not None and artifact_path.exists()

    # 1. Llamar a cancel_signature_attempt dentro de la transacción
    sig_service.cancel_signature_attempt(
        db=db_session,
        firid=firma.firid,
        expected_revnum=int(firma.revnum),
        motivo="Cancelación que sufrirá rollback",
        actor=actor,
    )

    # 2. Hacer rollback() de la transacción
    db_session.rollback()

    # 3. Comprobar que el archivo existe
    assert artifact_path.exists(), "El archivo debe existir tras el rollback"

    # 4. Realizar una operación DB completamente no relacionada en la misma sesión
    dummy = Plantill(
        tplcod=f"DUMMY_{uuid4().hex[:6]}",
        tplnom="Dummy unrelated",
        tplver=1,
        numpag=1,
        estado="ACTIVA",
        usrcre="admin",
    )
    db_session.add(dummy)

    # 5. Hacer commit() usando LA MISMA Session
    db_session.commit()

    # 6. Comprobar nuevamente que el archivo TODAVÍA existe
    assert artifact_path.exists(), (
        "FALLO DE CALLBACK TARDÍO: El listener after_commit sobrevivió al rollback y eliminó el archivo en un commit posterior no relacionado!"
    )



def test_cleanup_preserves_signature_artifact_when_protection_lookup_fails(db_session, tmp_path):
    """B. Si la consulta a BD falla con excepción, cleanup_expired NUNCA elimina el artefacto fir-*."""
    from unittest.mock import patch
    from sqlalchemy.exc import OperationalError

    temp_service = TemporaryArtifactService(
        base_dir=tmp_path / "artifacts",
        ttl_minutes=30,
        is_protected=is_signature_artifact_protected,
    )

    # Crear artefacto firmado con nombre fir-123-*
    sig_path = temp_service.write_bytes(
        b"contenido firmado critico",
        prefix="fir-123-",
        suffix=".pdf",
    )
    assert sig_path.exists()

    # Envejecer el archivo 50 minutos en el pasado (más de 30 min)
    now = time.time()
    old_time = now - (50 * 60)
    os.utime(sig_path, (old_time, old_time))

    # Simular fallo total de conectividad a PostgreSQL al consultar el estado
    with patch("app.core.database.SessionLocal", side_effect=OperationalError("conexión rechazada", None, None)):
        temp_service.cleanup_expired()

    # Demostrar que el artefacto DEBE conservarse por diseño defensivo
    assert sig_path.exists(), (
        "Fallo seguro: Si la BD no responde, el artefacto firmado DEBE conservarse y no interpretarse como huérfano"
    )


def test_cancelar_proceso_contract_states_allowed_and_rejected(db_session, tmp_path):
    """D. Validación del contrato de cancelación: estados permitidos y estados rechazados."""
    from fastapi import HTTPException

    ctx = _seed_document_flow(db_session, tmp_path)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    doc: DocFir = ctx["doc"]
    doc_service = DocumentService(alfresco_client=None, artifact_service=temp_service)  # type: ignore[arg-type]

    # 1. PENDIENTE_PUBLICACION -> CANCELADO debe ser permitido y limpia el artefacto
    _advance_to_pending_publication(db_session, ctx)
    assert doc.estado == EstadoDoc.PENDIENTE_PUBLICACION.value
    cancelled_pub = doc_service.cancelar_proceso(
        db=db_session,
        docid=doc.docid,
        motivo="Cancelado antes de publicar",
        usrmod=doc.usrcre,
        ip="127.0.0.1",
    )
    assert cancelled_pub.estado == EstadoDoc.CANCELADO.value

    # 3. ERROR_PUBLICACION -> CANCELADO debe ser permitido
    ctx_err = _seed_document_flow(db_session, tmp_path)
    doc_err: DocFir = ctx_err["doc"]
    doc_err.estado = EstadoDoc.ERROR_PUBLICACION.value
    db_session.commit()

    cancelled_err = doc_service.cancelar_proceso(
        db=db_session,
        docid=doc_err.docid,
        motivo="Cancelado tras error de publicacion",
        usrmod=doc_err.usrcre,
        ip="127.0.0.1",
    )
    assert cancelled_err.estado == EstadoDoc.CANCELADO.value

    # 4. COMPLETADO -> CANCELADO debe ser rechazado con 400
    ctx_comp = _seed_document_flow(db_session, tmp_path)
    doc_comp: DocFir = ctx_comp["doc"]
    doc_comp.estado = EstadoDoc.COMPLETADO.value
    db_session.commit()

    with pytest.raises(HTTPException) as exc_comp:
        doc_service.cancelar_proceso(
            db=db_session,
            docid=doc_comp.docid,
            motivo="Intentar cancelar proceso ya completado",
            usrmod=doc_comp.usrcre,
            ip="127.0.0.1",
        )
    assert exc_comp.value.status_code == 400



def test_signature_artifact_orphan_expires_by_ttl(db_session, tmp_path):
    """5. Un artefacto huérfano sin proceso activo en BD expira normalmente por TTL."""
    temp_service = TemporaryArtifactService(
        base_dir=tmp_path / "artifacts",
        ttl_minutes=30,
        is_protected=is_signature_artifact_protected,
    )
    # firid 999999 no existe en la base de datos
    orphan_path = temp_service.write_bytes(
        b"artefacto huerfano",
        prefix="fir-999999-",
        suffix=".pdf",
    )
    assert orphan_path.exists()

    # Envejecer el archivo huérfano 40 minutos
    now = time.time()
    old_time = now - (40 * 60)
    os.utime(orphan_path, (old_time, old_time))

    temp_service.cleanup_expired()

    assert not orphan_path.exists(), "El artefacto huérfano debe ser eliminado al expirar su TTL"


def test_signature_artifact_hash_invariant_under_cleanup(db_session, tmp_path):
    """6. El hash final del PDF protegido no cambia y el artefacto permanece inmutable."""
    ctx = _seed_document_flow(db_session, tmp_path)
    _advance_to_pending_publication(db_session, ctx)
    temp_service: TemporaryArtifactService = ctx["temp_service"]
    firid = ctx["firma"].firid

    artifact_path = temp_service.find_latest_path(prefix=f"fir-{firid}-", suffix=".pdf")
    assert artifact_path is not None
    assert artifact_path.exists()

    # Leer bytes originales y calcular hash
    original_bytes = artifact_path.read_bytes()
    expected_hash = hashlib.sha256(original_bytes).hexdigest()

    # Verificar que coincide con BD
    firma: DocFirma = ctx["firma"]
    doc: DocFir = ctx["doc"]
    assert firma.hasfin == expected_hash
    assert doc.hasfir == expected_hash

    # Envejecer el archivo y ejecutar múltiples rondas de cleanup
    now = time.time()
    for offset_minutes in (35, 60, 120):
        fake_time = now - (offset_minutes * 60)
        os.utime(artifact_path, (fake_time, fake_time))
        temp_service.cleanup_expired()

    # Comprobar inmutabilidad estricta
    assert artifact_path.exists()
    current_bytes = artifact_path.read_bytes()
    current_hash = hashlib.sha256(current_bytes).hexdigest()

    assert current_bytes == original_bytes, "Los bytes del artefacto firmado deben ser idénticos bit a bit"
    assert current_hash == expected_hash, "El hash final (hasfin) no debe alterarse"
    assert current_hash == firma.hasfin
    assert current_hash == doc.hasfir
