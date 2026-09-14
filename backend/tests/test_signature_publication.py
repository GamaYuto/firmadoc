from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from app.core.identity import IdentitySnapshot
from app.crud.crud_docfir import create_documento
from app.models.audifir import Audifir
from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.docpart import DocPart
from app.models.plantill import Plantill
from app.models.tplcamp import TplCamp
from app.schemas.docfirma import ResultContract, ResultFase, ResultFlag
from app.schemas.flujo import FlujoCreate, PasoCreate
from app.services.alfresco_service import AlfrescoLabClient, AlfrescoService
from app.services.flow_service import flow_service
from app.services.pdf_signature_service import PdfSignatureService
from app.services.signature_exceptions import (
    SignatureConcurrencyError,
    SignatureIntegrityError,
    SignaturePublicationError,
    SignatureRecoveryRequiredError,
    SignatureUploadError,
    SignatureVersionConflictError,
    SignatureWriteDisabledError,
)
from app.services.signature_service import signature_service
from app.services.step_service import step_service
from app.services.temporary_artifact_service import TemporaryArtifactService


ROOT_URL = "https://alfresco-lab.test"
API_PATH = "/alfresco/api/-default-/public/alfresco/versions/1"
API_URL = f"{ROOT_URL}{API_PATH}"


def _make_pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=12)
    doc.set_metadata({})
    data = doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    doc.close()
    return data


def _seed_publication_context(db, tmp_path: Path) -> dict[str, object]:
    temp_service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    pdf_service = PdfSignatureService(artifact_service=temp_service)
    unique_suffix = uuid4().hex[:8]

    source_path = tmp_path / "source.pdf"
    source_bytes = _make_pdf_bytes("Source PDF")
    source_path.write_bytes(source_bytes)
    source_sha = hashlib.sha256(source_bytes).hexdigest()

    plantill_code = f"TPL-PUB-{unique_suffix}"
    flow_code = f"F_PUB_{unique_suffix}"
    step_code = f"P1_{unique_suffix}"
    participant_user = f"firmante_{unique_suffix}"
    participant_name = f"Usuario Firmante {unique_suffix}"
    participant_email = f"firmante_{unique_suffix}@empresa.local"
    node_id = f"node-pdf-{unique_suffix}"

    plantill = Plantill(
        tplcod=plantill_code,
        tplnom=f"Plantilla Publicacion {unique_suffix}",
        tplver=1,
        numpag=1,
        estado="ACTIVA",
        usrcre="admin",
    )
    db.add(plantill)
    db.flush()
    plantill_id = plantill.tplid

    camp = TplCamp(
        tplid=plantill_id,
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
        FlujoCreate(flucod=flow_code, flunom=f"Flujo Publicacion {unique_suffix}", usrcre="admin"),
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
        docnom="source.pdf",
        tamano=len(source_bytes),
        verini="1.0",
        hasori=source_sha,
        usrcre="admin",
    )
    doc.fluid = flow_id
    doc.tplid = plantill_id
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
    firma = signature_service.reserve_signature_attempt(
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
    assert firma_row is not None
    assert part_row is not None
    assert doc_row is not None

    return {
        "temp_service": temp_service,
        "source_path": source_path,
        "source_bytes": source_bytes,
        "source_sha": source_sha,
        "generated_artifact": generated_artifact,
        "firma": firma_row,
        "part": part_row,
        "doc": doc_row,
        "node_id": node_id,
        "actor": actor,
        "comment": f"FirmaDoc:{doc_row.docid}:{firma_row.firid}:{firma_row.opeid}",
    }


def _publication_service(
    temp_service: TemporaryArtifactService,
    node_id: str = "node-pdf-1",
    test_expected_name: str | None = "source.pdf",
    test_expected_path: str | None = None,
) -> AlfrescoService:
    client = AlfrescoLabClient(
        base_url=ROOT_URL,
        api_path=API_PATH,
        username="lab_user",
        password="lab_password",
        expected_host="alfresco-lab.test",
        connect_timeout=1,
        read_timeout=1,
        write_timeout=1,
        max_download_size=2 * 1024 * 1024,
        max_history_pages=10,
        history_page_size=5,
    )
    return AlfrescoService(
        client=client,
        artifact_service=temp_service,
        write_enabled=True,
        test_node_id=node_id,
        test_expected_name=test_expected_name,
        test_expected_path=test_expected_path,
        test_expected_mimetype="application/pdf",
        major_version=False,
    )


def _mark_context_pending_publication(db, context: dict[str, object]) -> None:
    firma = db.get(DocFirma, context["firma"].firid)
    part = db.get(DocPart, context["part"].parid)
    doc = db.get(DocFir, context["doc"].docid)
    assert firma is not None
    assert part is not None
    assert doc is not None
    now = datetime.now(timezone.utc)
    firma.estado = EstadoDocFirma.COMPLETADA.value
    firma.fecfin = now
    firma.verfin = doc.verini
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


def _event_count(db, firid: int, evento: str) -> int:
    return (
        db.query(Audifir)
        .filter(Audifir.entid == firid, Audifir.evento == evento)
        .count()
    )


@respx.mock
def test_signature_publication_success(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    generated_bytes = context["generated_artifact"].path.read_bytes()

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"etag": '"etag-0"'}, content=context["source_bytes"])
    )

    def _upload_response(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-Match"] == '"etag-0"'
        assert request.url.params["majorVersion"] == "false"
        assert request.url.params["comment"] == context["comment"]
        return httpx.Response(
            200,
            headers={"etag": '"etag-1"'},
            json={
                "entry": {
                    "id": node_id,
                    "properties": {"cm:versionLabel": "1.1"},
                    "versionComment": context["comment"],
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )

    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(side_effect=_upload_response)
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": "1.1",
                    "nodeId": node_id,
                    "versionComment": context["comment"],
                    "createdAt": "2026-08-03T12:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1/content").mock(
        return_value=httpx.Response(200, content=generated_bytes)
    )

    result = service.publish_generated_signature(
        db=db_session,
        firid=context["firma"].firid,
        expected_revnum=context["firma"].revnum,
        expected_participant_verlock=context["part"].verlock,
    )

    db_session.expire_all()
    firma = db_session.get(DocFirma, context["firma"].firid)
    doc = db_session.get(DocFir, context["doc"].docid)
    assert firma is not None
    assert doc is not None
    assert result.version_id == "1.1"
    assert firma.estado == EstadoDocFirma.COMPLETADA.value
    assert firma.verfin == "1.1"
    assert firma.hasfin == context["generated_artifact"].sha256
    assert doc.estado == "COMPLETADO"
    assert doc.verfin == "1.1"
    assert doc.hasfir == context["generated_artifact"].sha256
    assert _event_count(db_session, context["firma"].firid, "FIR_COMP") == 1
    assert _event_count(db_session, context["firma"].firid, "FIR_RECO") == 0
    assert context["generated_artifact"].path.exists() is False


@respx.mock
def test_signature_publication_current_hash_mismatch_marks_conflict(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    remote_bytes = _make_pdf_bytes("Remote mismatch")

    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "source.pdf",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "application/pdf", "sizeInBytes": len(remote_bytes)},
                    "properties": {"cm:versionLabel": "1.0"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(200, content=remote_bytes))

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )
    assert excinfo.value.code == "REMOTE_HASH_CONFLICT"

    db_session.expire_all()
    firma = db_session.get(DocFirma, context["firma"].firid)
    assert firma is not None
    assert firma.estado == EstadoDocFirma.GENERADA.value
    assert firma.errcod is None
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_REMOTE_CONFLICT") == 1
    assert _event_count(db_session, context["firma"].firid, "FIR_COMP") == 0
    assert context["generated_artifact"].path.exists() is True


@respx.mock
def test_signature_publication_timeout_keeps_subiendo_and_records_recovery(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"etag": '"etag-0"'}, content=context["source_bytes"])
    )
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(SignatureRecoveryRequiredError):
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )

    db_session.expire_all()
    firma = db_session.get(DocFirma, context["firma"].firid)
    assert firma is not None
    assert firma.estado == EstadoDocFirma.SUBIENDO.value
    assert _event_count(db_session, context["firma"].firid, "FIR_RECO") == 1
    assert _event_count(db_session, context["firma"].firid, "FIR_COMP") == 0
    assert context["generated_artifact"].path.exists() is True


@respx.mock
def test_signature_publication_write_disabled_runs_preflight_and_never_puts(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    service.write_enabled = False
    node_id = context["node_id"]

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, content=context["source_bytes"])
    )
    put_route = respx.put(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(500))

    with pytest.raises(SignatureWriteDisabledError) as excinfo:
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )

    assert excinfo.value.code == "ALFRESCO_WRITE_DISABLED"
    assert put_route.call_count == 0
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_PREFLIGHT_OK") == 1
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_BLOCKED_WRITE_DISABLED") == 1
    assert context["generated_artifact"].path.exists() is True


@respx.mock
def test_publish_document_write_disabled_retry_is_idempotent(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    _mark_context_pending_publication(db_session, context)
    service = _publication_service(context["temp_service"], context["node_id"])
    service.write_enabled = False
    node_id = context["node_id"]

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, content=context["source_bytes"])
    )
    put_route = respx.put(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(200))

    errors = []
    for _ in range(2):
        with pytest.raises(SignatureWriteDisabledError) as excinfo:
            service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")
        errors.append(excinfo.value)

    assert errors[0].operation_id == errors[1].operation_id == str(context["firma"].opeid)
    assert put_route.call_count == 0


def test_publish_document_requires_pending_publication(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "DOCUMENT_NOT_PENDING_PUBLICATION"


def test_publish_document_rejects_incomplete_required_signatures(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    doc = db_session.get(DocFir, context["doc"].docid)
    assert doc is not None
    doc.estado = EstadoDoc.PENDIENTE_PUBLICACION.value
    doc.hasfir = context["generated_artifact"].sha256
    db_session.commit()
    service = _publication_service(context["temp_service"], context["node_id"])

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")

    assert excinfo.value.code == "REQUIRED_SIGNATURES_INCOMPLETE"


def test_publish_document_rejects_unauthorized_actor(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    _mark_context_pending_publication(db_session, context)
    service = _publication_service(context["temp_service"], context["node_id"])

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "firmante", "127.0.0.1", "pytest")

    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "PUBLICATION_FORBIDDEN"


def test_publish_document_missing_artifact_returns_410(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    _mark_context_pending_publication(db_session, context)
    context["temp_service"].cleanup_path(context["generated_artifact"].path)
    service = _publication_service(context["temp_service"], context["node_id"])

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")

    assert excinfo.value.status_code == 410
    assert excinfo.value.code == "ACCUMULATED_ARTIFACT_GONE"


def test_publish_document_local_hash_mismatch_stops_before_remote(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    _mark_context_pending_publication(db_session, context)
    context["generated_artifact"].path.write_bytes(_make_pdf_bytes("altered final"))
    service = _publication_service(context["temp_service"], context["node_id"])

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")

    assert excinfo.value.code == "LOCAL_HASH_MISMATCH"
    assert context["generated_artifact"].path.exists() is True


def test_publish_document_invalid_accumulated_pdf_stops_before_remote(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    _mark_context_pending_publication(db_session, context)
    context["generated_artifact"].path.write_bytes(b"not a pdf")
    service = _publication_service(context["temp_service"], context["node_id"])

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "LOCAL_PDF_INVALID"
    assert context["generated_artifact"].path.exists() is True


@respx.mock
def test_publish_document_missing_remote_node_is_controlled_and_does_not_put(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    _mark_context_pending_publication(db_session, context)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    respx.get(f"{API_URL}/nodes/{node_id}").mock(return_value=httpx.Response(404, text="not found"))
    put_route = respx.put(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(200))

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")

    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "REMOTE_VERSION_CONFLICT"
    assert put_route.call_count == 0


@respx.mock
def test_publish_document_remote_non_pdf_does_not_put(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    _mark_context_pending_publication(db_session, context)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    respx.get(f"{API_URL}/nodes/{node_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "name": "source.txt",
                    "nodeType": "cm:content",
                    "isFile": True,
                    "content": {"mimeType": "text/plain", "sizeInBytes": 4},
                    "properties": {"cm:versionLabel": "1.0"},
                }
            },
        )
    )
    put_route = respx.put(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(200))

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")

    assert excinfo.value.code == "ALFRESCO_REMOTE_ERROR"
    assert put_route.call_count == 0


@respx.mock
def test_publish_document_remote_version_conflict_does_not_put(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    _mark_context_pending_publication(db_session, context)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, content=context["source_bytes"])
    )
    put_route = respx.put(f"{API_URL}/nodes/{node_id}/content").mock(return_value=httpx.Response(200))

    with pytest.raises(SignaturePublicationError) as excinfo:
        service.publish_document(db_session, context["doc"].docid, "admin", "127.0.0.1", "pytest")

    assert excinfo.value.code == "REMOTE_VERSION_CONFLICT"
    assert put_route.call_count == 0
    assert context["generated_artifact"].path.exists() is True


@respx.mock
def test_publication_upload_failure_preserves_signed_artifact(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"etag": '"etag-0"'}, content=context["source_bytes"])
    )
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(500, json={"error": {"statusCode": 500, "briefSummary": "Internal Server Error"}})
    )

    with pytest.raises(SignatureRecoveryRequiredError):
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )

    assert context["generated_artifact"].path.exists() is True
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 0


@respx.mock
def test_publication_verification_failure_preserves_signed_artifact(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"etag": '"etag-0"'}, content=context["source_bytes"])
    )
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "properties": {"cm:versionLabel": "1.1"},
                    "versionComment": context["comment"],
                    "modifiedByUser": {"id": "lab_user"},
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
                    "versionComment": context["comment"],
                    "createdAt": "2026-08-03T12:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1/content").mock(
        return_value=httpx.Response(500, text="Internal Error during download")
    )

    with pytest.raises((SignatureRecoveryRequiredError, SignatureUploadError)):
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )

    assert context["generated_artifact"].path.exists() is True
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 0


@respx.mock
def test_publication_hash_mismatch_preserves_signed_artifact(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    tampered_bytes = _make_pdf_bytes("Tampered Remote Content")

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"etag": '"etag-0"'}, content=context["source_bytes"])
    )
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "properties": {"cm:versionLabel": "1.1"},
                    "versionComment": context["comment"],
                    "modifiedByUser": {"id": "lab_user"},
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
                    "versionComment": context["comment"],
                    "createdAt": "2026-08-03T12:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1/content").mock(
        return_value=httpx.Response(200, content=tampered_bytes)
    )

    with pytest.raises(SignatureIntegrityError):
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )

    assert context["generated_artifact"].path.exists() is True
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 0


@respx.mock
def test_publication_verified_success_deletes_signed_artifact(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    generated_bytes = context["generated_artifact"].path.read_bytes()

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"etag": '"etag-0"'}, content=context["source_bytes"])
    )
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "properties": {"cm:versionLabel": "1.1"},
                    "versionComment": context["comment"],
                    "modifiedByUser": {"id": "lab_user"},
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
                    "versionComment": context["comment"],
                    "createdAt": "2026-08-03T12:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1/content").mock(
        return_value=httpx.Response(200, content=generated_bytes)
    )

    result = service.publish_generated_signature(
        db=db_session,
        firid=context["firma"].firid,
        expected_revnum=context["firma"].revnum,
        expected_participant_verlock=context["part"].verlock,
    )

    assert result.version_id == "1.1"
    assert context["generated_artifact"].path.exists() is False
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 1


@respx.mock
def test_publication_finalize_failure_preserves_artifact_for_reconciliation(db_session, tmp_path, monkeypatch):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    generated_bytes = context["generated_artifact"].path.read_bytes()

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"etag": '"etag-0"'}, content=context["source_bytes"])
    )
    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": node_id,
                    "properties": {"cm:versionLabel": "1.1"},
                    "versionComment": context["comment"],
                    "modifiedByUser": {"id": "lab_user"},
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
                    "versionComment": context["comment"],
                    "createdAt": "2026-08-03T12:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1/content").mock(
        return_value=httpx.Response(200, content=generated_bytes)
    )

    def _fail_finalize(*args, **kwargs):
        raise RuntimeError("Database error during local finalization")

    monkeypatch.setattr(service, "_finalize_verified_signature", _fail_finalize)

    with pytest.raises(RuntimeError, match="Database error during local finalization"):
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )

    # Artifact must be preserved so reconciliation can finalize it later!
    assert context["generated_artifact"].path.exists() is True
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 0


@respx.mock
def test_publication_missing_etag_fails_closed_and_does_not_put(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]

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
    # Note: no ETag header returned by GET /content
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, content=context["source_bytes"])
    )
    put_route = respx.put(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200)
    )

    with pytest.raises(SignatureConcurrencyError, match="precondicion ETag"):
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )

    # Fail closed: no PUT should have been executed!
    assert put_route.call_count == 0
    # Artifact must be preserved!
    assert context["generated_artifact"].path.exists() is True
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_REMOTE_CONFLICT") == 1
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 0


@respx.mock
def test_publish_document_missing_etag_fails_closed_and_does_not_put(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    _mark_context_pending_publication(db_session, context)
    node_id = context["node_id"]

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
    # No ETag
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, content=context["source_bytes"])
    )
    put_route = respx.put(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200)
    )

    with pytest.raises(SignaturePublicationError) as exc_info:
        service.publish_document(
            db=db_session,
            docid=context["doc"].docid,
            actor_user="admin",
            iporig="127.0.0.1",
            user_agent="pytest",
        )

    assert exc_info.value.code == "REMOTE_PRECONDITION_MISSING"
    assert exc_info.value.status_code == 409
    assert put_route.call_count == 0
    assert context["generated_artifact"].path.exists() is True
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_REMOTE_CONFLICT") == 1
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 0


@respx.mock
def test_publication_412_precondition_failed_marks_conflict_without_retry(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    expected_etag = '"1786048128957"'

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"ETag": expected_etag}, content=context["source_bytes"])
    )

    def _put_response(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-Match"] == expected_etag
        return httpx.Response(412, text="Precondition Failed")

    put_route = respx.put(f"{API_URL}/nodes/{node_id}/content").mock(side_effect=_put_response)

    with pytest.raises(SignatureVersionConflictError):
        service.publish_generated_signature(
            db=db_session,
            firid=context["firma"].firid,
            expected_revnum=context["firma"].revnum,
            expected_participant_verlock=context["part"].verlock,
        )

    # Exactly 1 call, NO automatic retry
    assert put_route.call_count == 1
    # Artifact must be preserved!
    assert context["generated_artifact"].path.exists() is True
    # Conflict recorded
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_REMOTE_CONFLICT") >= 1
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 0


@respx.mock
def test_publication_preserves_exact_quotes_in_if_match(db_session, tmp_path):
    context = _seed_publication_context(db_session, tmp_path)
    service = _publication_service(context["temp_service"], context["node_id"])
    node_id = context["node_id"]
    generated_bytes = context["generated_artifact"].path.read_bytes()
    expected_etag = '"1786048128957"'

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
    respx.get(f"{API_URL}/nodes/{node_id}/content").mock(
        return_value=httpx.Response(200, headers={"ETag": expected_etag}, content=context["source_bytes"])
    )

    def _upload_response(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-Match"] == expected_etag
        assert request.headers["If-Match"].startswith('"') and request.headers["If-Match"].endswith('"')
        return httpx.Response(
            200,
            headers={"etag": '"etag-new"'},
            json={
                "entry": {
                    "id": node_id,
                    "properties": {"cm:versionLabel": "1.1"},
                    "versionComment": context["comment"],
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )

    respx.put(f"{API_URL}/nodes/{node_id}/content").mock(side_effect=_upload_response)
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": {
                    "id": "1.1",
                    "nodeId": node_id,
                    "versionComment": context["comment"],
                    "createdAt": "2026-08-03T12:00:00Z",
                    "modifiedByUser": {"id": "lab_user"},
                }
            },
        )
    )
    respx.get(f"{API_URL}/nodes/{node_id}/versions/1.1/content").mock(
        return_value=httpx.Response(200, content=generated_bytes)
    )

    result = service.publish_generated_signature(
        db=db_session,
        firid=context["firma"].firid,
        expected_revnum=context["firma"].revnum,
        expected_participant_verlock=context["part"].verlock,
    )

    assert result.version_id == "1.1"
    assert context["generated_artifact"].path.exists() is False
    assert _event_count(db_session, context["firma"].firid, "PUBLICATION_COMPLETED") == 1
