from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
import unicodedata

import pytest
from sqlalchemy import select

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from app.core.config import settings
from app.core.identity import IdentitySnapshot
from app.crud.crud_docfir import create_documento
from app.models.docfir import DocFir, EstadoDoc
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.docpart import DocPart
from app.models.docpaso import DocPaso
from app.models.plantill import Plantill
from app.models.tplcamp import TplCamp
from app.schemas.flujo import FlujoCreate, PasoCreate
from app.services.flow_service import flow_service
from app.services.pdf_signature_service import PdfSignatureService
from app.services.signature_exceptions import SignaturePayloadError, SignaturePlacementError
from app.services.signature_service import signature_service
from app.services.step_service import step_service
from app.services.temporary_artifact_service import TemporaryArtifactService
from app.services.verification_code_service import (
    decode_verification_code,
    encode_verification_code,
    verify_code,
)

_BOGOTA = ZoneInfo("America/Bogota")


def _write_test_pdf(path: Path, pages: int = 1, page_rotation: int = 0) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 50), f"Página de prueba {i+1} para evaluación de sello", fontsize=12)
        if page_rotation:
            page.set_rotation(page_rotation)
    doc.set_metadata({})
    data = doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    path.write_bytes(data)
    doc.close()
    return data


def _seed_stamp_context(
    db,
    tmp_path: Path,
    *,
    usrid: str = "lblanco",
    nomcom: str = "Leonel Tarsicio Blanco Bahoque",
    rolpro: str = "Representante legal",
    step_type: str = "APROBAR",
    pages: int = 1,
    page_rotation: int = 0,
    position_rotation: int = 0,
    width: int = 240,
    height: int = 85,
    page_num: int = 1,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "source.pdf"
    source_bytes = _write_test_pdf(source_path, pages=pages, page_rotation=page_rotation)
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    code_suffix = uuid4().hex[:8]

    plantill = Plantill(
        tplcod=f"TPL-STAMP-{code_suffix}",
        tplnom="Plantilla Sello",
        tplver=1,
        numpag=pages,
        usrcre="admin",
        estado="ACTIVA",
    )
    db.add(plantill)
    db.flush()

    camp = TplCamp(
        tplid=plantill.tplid,
        camcod=f"C_{code_suffix}",
        camnom="Firma",
        camtip="FIRMA",
        pagina=page_num,
        posx=0.1,
        posy=0.1,
        ancho=0.2,
        alto=0.05,
        orden=1,
        usrcre="admin",
    )
    db.add(camp)
    db.flush()

    flujo = flow_service.create_flow(db, FlujoCreate(flucod=f"F_STAMP_{code_suffix}", flunom="Flujo Sello", usrcre="admin"))
    flow_service.add_step(
        db,
        flujo.fluid,
        PasoCreate(pascod=f"P_{code_suffix}", pasnom="Paso Aprobacion", pastip="FIRMAR", orden=1, rolreq=rolpro),
        "admin",
    )
    flow_service.activate_flow(db, flujo.fluid, "admin")

    doc = create_documento(
        db=db,
        nodid=f"node-stamp-{code_suffix}",
        docnom="reporte_clinico.pdf",
        tamano=len(source_bytes),
        verini="1.0",
        hasori=source_sha,
        usrcre="admin",
    )
    doc.fluid = flujo.fluid
    doc.tplid = plantill.tplid
    doc.estado = EstadoDoc.EN_CURSO.value
    db.flush()

    pasos = step_service.instantiate_document_steps(db, doc.docid, "admin")
    part = DocPart(
        dpasid=pasos[0].dpasid,
        usrid=usrid,
        nomcom=nomcom,
        correo=f"{usrid}@clinicadelcaribe.com",
        rolpro=rolpro,
        orden=1,
        obliga=True,
        estado="DISPONIBLE",
        verlock=1,
        usrcre="admin",
    )
    db.add(part)
    db.commit()

    actor = IdentitySnapshot(
        usrid=usrid,
        nomcom=nomcom,
        correo=f"{usrid}@clinicadelcaribe.com",
        rolpro=rolpro,
    )
    positions = [
        {
            "pagina": page_num,
            "posx": 72,
            "posy": 100,
            "ancho": width,
            "alto": height,
            "orden": 1,
            "camid": camp.camid,
            "rotaci": position_rotation,
        }
    ]
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=part.parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=positions,
        verori="1.0",
        hasori=source_sha,
        actor=actor,
        expected_participant_verlock=1,
    )
    if step_type != "FIRMAR":
        pasos[0].pastip = step_type
        db.flush()
    db.commit()

    return {
        "source_path": source_path,
        "source_bytes": source_bytes,
        "source_sha": source_sha,
        "docid": doc.docid,
        "partid": part.parid,
        "firid": firma.firid,
        "opeid": firma.opeid,
        "actor": actor,
    }


def test_verification_code_roundtrip_reversible():
    opeid = UUID("f67664ff-0e8f-4f4a-9123-456789abcdef")
    code = encode_verification_code(opeid)
    assert code.startswith("FMD-")
    assert len(code) == 30  # "FMD-" + 26 chars
    decoded = decode_verification_code(code)
    assert decoded == opeid

    # Case insensitive
    assert decode_verification_code(code.lower()) == opeid

    # Invalid codes return None
    assert decode_verification_code("INVALID-CODE") is None
    assert decode_verification_code("FMD-SHORT") is None
    assert decode_verification_code("") is None


def test_verify_code_distinguishes_lifecycle_states(db_session, tmp_path):
    ctx = _seed_stamp_context(db_session, tmp_path)
    code = encode_verification_code(ctx["opeid"])

    # 1. State: INICIADA -> EN_PROCESO
    result_iniciada = verify_code(db_session, code)
    assert result_iniciada.estado_verificacion == "EN_PROCESO"
    assert result_iniciada.codigo == code
    assert result_iniciada.firmante_nombre == "Leonel Tarsicio Blanco Bahoque"
    assert result_iniciada.firmante_rol == "Representante legal"
    assert result_iniciada.fecha_firma is None

    # 2. State: COMPLETADA -> COMPLETADA with date and document
    firma = db_session.get(DocFirma, ctx["firid"])
    firma.estado = EstadoDocFirma.COMPLETADA.value
    firma.fecfin = datetime.now(ZoneInfo("UTC"))
    firma.verfin = "1.1"
    firma.hasfin = "a" * 64
    firma.result = {"schema_ver": 1, "verchk": True, "haschk": True, "flags": ["HASH_MATCH"]}
    db_session.commit()

    result_completada = verify_code(db_session, code)
    assert result_completada.estado_verificacion == "COMPLETADA"
    assert result_completada.firmante_nombre == "Leonel Tarsicio Blanco Bahoque"
    assert result_completada.fecha_firma is not None
    assert result_completada.documento_nombre == "reporte_clinico.pdf"

    # 3. State: CANCELADA -> FALLIDA_O_CONFLICTO
    firma.estado = EstadoDocFirma.CANCELADA.value
    firma.motivo = "Cancelado para test"
    db_session.commit()

    result_cancelada = verify_code(db_session, code)
    assert result_cancelada.estado_verificacion == "FALLIDA_O_CONFLICTO"

    # 4. Unknown code
    unknown_code = encode_verification_code(uuid4())
    result_unknown = verify_code(db_session, unknown_code)
    assert result_unknown.estado_verificacion == "NO_ENCONTRADO"


def test_lblanco_approval_generates_new_stamp_with_verification_code(db_session, tmp_path, monkeypatch):
    ctx = _seed_stamp_context(db_session, tmp_path, usrid="lblanco", step_type="APROBAR")
    sig_path = Path("C:/Users/Profesional Tics/.firmadoc/private/firma2.png")
    monkeypatch.setattr(settings, "FIRMADOC_LBLANCO_SIGNATURE_PATH", str(sig_path))

    temp_service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    service = PdfSignatureService(artifact_service=temp_service)

    artifact = service.generate_signature_pdf_and_mark_generated(
        db=db_session,
        firid=ctx["firid"],
        source_pdf_path=ctx["source_path"],
        usrmod="lblanco",
    )
    db_session.commit()

    try:
        assert artifact.path.exists()
        assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
        assert artifact.page_count == 1

        expected_code = encode_verification_code(ctx["opeid"])

        with fitz.open(artifact.path) as doc:
            page = doc[0]
            text = page.get_text("text")
            norm_text = unicodedata.normalize("NFKD", text)
            plain_text = "".join(c for c in norm_text if not unicodedata.combining(c))
            # Must contain the new stamp texts
            assert "VALIDADO POR FIRMADOC" in plain_text
            assert "Clinica del Caribe" in plain_text
            assert expected_code in plain_text
            assert "Codigo de verificacion:" in plain_text

            # Must NOT contain obsolete text labels
            assert "ID: " not in text
            assert "SHA: " not in text
            assert "Autorizado electrónicamente" not in text

            # Must contain the signature image
            images = page.get_images(full=True)
            assert len(images) >= 1

        stored = db_session.get(DocFirma, ctx["firid"])
        assert stored.estado == EstadoDocFirma.GENERADA.value
        assert stored.hasfin == artifact.sha256
    finally:
        artifact.cleanup()


def test_other_user_approval_does_not_use_lblanco_image(db_session, tmp_path, monkeypatch):
    ctx = _seed_stamp_context(
        db_session,
        tmp_path,
        usrid="otro_gerente",
        nomcom="Dr. Otro Gerente",
        rolpro="Director Médico",
        step_type="APROBAR",
    )
    sig_path = Path("C:/Users/Profesional Tics/.firmadoc/private/firma2.png")
    monkeypatch.setattr(settings, "FIRMADOC_LBLANCO_SIGNATURE_PATH", str(sig_path))

    temp_service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    service = PdfSignatureService(artifact_service=temp_service)

    artifact = service.generate_signature_pdf_and_mark_generated(
        db=db_session,
        firid=ctx["firid"],
        source_pdf_path=ctx["source_path"],
        usrmod="otro_gerente",
    )
    db_session.commit()

    try:
        with fitz.open(artifact.path) as doc:
            page = doc[0]
            text = page.get_text("text")
            # Other user keeps previous internal approval text representation
            assert "Dr. Otro Gerente" in text
            assert "Director Médico" in text
            # Must NOT contain the lblanco image
            images = page.get_images(full=True)
            assert len(images) == 0
    finally:
        artifact.cleanup()


def test_lblanco_stamp_fails_when_image_is_missing(db_session, tmp_path, monkeypatch):
    ctx = _seed_stamp_context(db_session, tmp_path, usrid="lblanco", step_type="APROBAR")
    monkeypatch.setattr(settings, "FIRMADOC_LBLANCO_SIGNATURE_PATH", str(tmp_path / "non_existent.png"))

    temp_service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    service = PdfSignatureService(artifact_service=temp_service)

    with pytest.raises(SignaturePayloadError) as excinfo:
        service.generate_signature_pdf_and_mark_generated(
            db=db_session,
            firid=ctx["firid"],
            source_pdf_path=ctx["source_path"],
            usrmod="lblanco",
        )
    assert "Imagen de firma institucional no disponible" in str(excinfo.value)


def test_lblanco_stamp_fails_when_box_is_too_small(db_session, tmp_path, monkeypatch):
    ctx = _seed_stamp_context(
        db_session,
        tmp_path,
        usrid="lblanco",
        step_type="APROBAR",
        width=100,  # too small (< 160)
        height=30,  # too small (< 50)
    )
    sig_path = Path("C:/Users/Profesional Tics/.firmadoc/private/firma2.png")
    monkeypatch.setattr(settings, "FIRMADOC_LBLANCO_SIGNATURE_PATH", str(sig_path))

    temp_service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    service = PdfSignatureService(artifact_service=temp_service)

    with pytest.raises(SignaturePlacementError) as excinfo:
        service.generate_signature_pdf_and_mark_generated(
            db=db_session,
            firid=ctx["firid"],
            source_pdf_path=ctx["source_path"],
            usrmod="lblanco",
        )
    assert "area de firma es demasiado pequena" in str(excinfo.value)


def test_lblanco_stamp_multipage_only_on_target_page(db_session, tmp_path, monkeypatch):
    ctx = _seed_stamp_context(
        db_session,
        tmp_path,
        usrid="lblanco",
        step_type="APROBAR",
        pages=3,
        page_num=2,  # Target page 2
    )
    sig_path = Path("C:/Users/Profesional Tics/.firmadoc/private/firma2.png")
    monkeypatch.setattr(settings, "FIRMADOC_LBLANCO_SIGNATURE_PATH", str(sig_path))

    temp_service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    service = PdfSignatureService(artifact_service=temp_service)

    artifact = service.generate_signature_pdf_and_mark_generated(
        db=db_session,
        firid=ctx["firid"],
        source_pdf_path=ctx["source_path"],
        usrmod="lblanco",
    )
    db_session.commit()

    try:
        with fitz.open(artifact.path) as doc:
            assert doc.page_count == 3
            # Page 1 has no stamp texts
            assert "VALIDADO POR FIRMADOC" not in doc[0].get_text()
            assert "Codigo de verificacion" not in unicodedata.normalize("NFKD", doc[0].get_text())
            # Page 2 has the signature stamp
            norm_page2 = unicodedata.normalize("NFKD", doc[1].get_text())
            assert "VALIDADO POR FIRMADOC" in norm_page2
            assert encode_verification_code(ctx["opeid"]) in norm_page2
            # Page 3 has no stamp texts
            assert "VALIDADO POR FIRMADOC" not in doc[2].get_text()
            assert "Codigo de verificacion" not in unicodedata.normalize("NFKD", doc[2].get_text())
    finally:
        artifact.cleanup()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_lblanco_stamp_rotations(db_session, tmp_path, monkeypatch, rotation):
    w = 85 if rotation in (90, 270) else 240
    h = 240 if rotation in (90, 270) else 85
    ctx = _seed_stamp_context(
        db_session,
        tmp_path,
        usrid="lblanco",
        step_type="APROBAR",
        position_rotation=rotation,
        width=w,
        height=h,
    )
    sig_path = Path("C:/Users/Profesional Tics/.firmadoc/private/firma2.png")
    monkeypatch.setattr(settings, "FIRMADOC_LBLANCO_SIGNATURE_PATH", str(sig_path))

    temp_service = TemporaryArtifactService(base_dir=tmp_path / "artifacts")
    service = PdfSignatureService(artifact_service=temp_service)

    artifact = service.generate_signature_pdf_and_mark_generated(
        db=db_session,
        firid=ctx["firid"],
        source_pdf_path=ctx["source_path"],
        usrmod="lblanco",
    )
    db_session.commit()

    try:
        assert artifact.path.exists()
        with fitz.open(artifact.path) as doc:
            assert doc.page_count == 1
            assert len(doc[0].get_images(full=True)) >= 1
    finally:
        artifact.cleanup()
