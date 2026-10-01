from __future__ import annotations

import unicodedata

import hashlib
import struct
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore[no-redef]

from app.core.identity import IdentitySnapshot
from app.crud.crud_docfir import create_documento
from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.docfir import EstadoDoc
from app.models.docpart import DocPart
from app.models.plantill import Plantill
from app.models.tplcamp import TplCamp
from app.schemas.flujo import FlujoCreate, PasoCreate
from app.services.flow_service import flow_service
from app.services.pdf_signature_service import PdfSignatureService
from app.services.signature_exceptions import (
    SignatureConcurrencyError,
    SignatureIntegrityError,
    SignaturePayloadError,
    SignaturePlacementError,
)
from app.services.signature_service import signature_service
from app.services.step_service import step_service
from app.services.temporary_artifact_service import TemporaryArtifactService


_BOGOTA = ZoneInfo("America/Bogota")


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def _png_bytes(width: int, height: int, pixel: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + pixel * width
    raw = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


def _write_pdf(path: Path, *, page_rotation: int = 0, width: int = 595, height: int = 842) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((24, 24), "Documento de prueba para estampado", fontsize=14)
    if page_rotation:
        page.set_rotation(page_rotation)
    doc.set_metadata({})
    pdf_bytes = doc.tobytes(garbage=3, deflate=True, use_objstms=1, no_new_id=True)
    path.write_bytes(pdf_bytes)
    doc.close()
    return pdf_bytes


def _seed_signature_context(
    db,
    tmp_path: Path,
    tipfir: str,
    *,
    page_rotation: int = 0,
    position_rotation: int = 0,
    step_type: str = "FIRMAR",
    participant_name: str = "Usuario Firmante",
    participant_role: str = "Firmante",
    position_width: int = 220,
    position_height: int = 100,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "source.pdf"
    source_bytes = _write_pdf(source_path, page_rotation=page_rotation)
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    unique_code = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:10]

    plantill = Plantill(tplcod=f"TPL-PDF-{unique_code}", tplnom="Plantilla PDF", tplver=1, numpag=1, usrcre="admin", estado="ACTIVA")
    db.add(plantill)
    db.flush()

    camp = TplCamp(
        tplid=plantill.tplid,
        camcod="C1",
        camnom="Firma",
        camtip="FIRMA",
        pagina=1,
        posx=0.1,
        posy=0.1,
        ancho=0.2,
        alto=0.05,
        orden=1,
        usrcre="admin",
    )
    db.add(camp)
    db.flush()

    flujo = flow_service.create_flow(db, FlujoCreate(flucod=f"F_PDF_{unique_code}", flunom="Flujo PDF", usrcre="admin"))
    flow_service.add_step(
        db,
        flujo.fluid,
        PasoCreate(pascod="P1", pasnom="Paso 1", pastip="FIRMAR", orden=1, rolreq=participant_role, plazo=60),
        "admin",
    )
    flow_service.activate_flow(db, flujo.fluid, "admin")

    doc = create_documento(
        db=db,
        nodid=f"node-pdf-{unique_code}",
        docnom="source.pdf",
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
        usrid="firmante",
        nomcom=participant_name,
        correo="firmante@empresa.local",
        rolpro=participant_role,
        orden=1,
        obliga=True,
        estado="DISPONIBLE",
        verlock=1,
        usrcre="admin",
    )
    db.add(part)
    db.commit()

    actor = IdentitySnapshot(
        usrid="firmante",
        nomcom=participant_name,
        correo="firmante@empresa.local",
        rolpro=participant_role,
    )
    positions = [
        {
            "pagina": 1,
            "posx": 72,
            "posy": 72,
            "ancho": position_width,
            "alto": position_height,
            "orden": 1,
            "camid": camp.camid,
            "rotaci": position_rotation,
        }
    ]
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=part.parid,
        tipfir=tipfir,
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
        "campid": camp.camid,
    }


def _generated_service(tmp_path: Path, *, clock: datetime | None = None) -> PdfSignatureService:
    return PdfSignatureService(
        artifact_service=TemporaryArtifactService(base_dir=tmp_path / "generated"),
        clock=(lambda: clock) if clock is not None else None,
    )


def _generated_png(service: PdfSignatureService) -> Path:
    return service.artifact_service.write_bytes(
        _png_bytes(72, 28, b"\x2c\x7a\xd9\xff"),
        prefix="signature-",
        suffix=".png",
    )


def _fresh_docfirma(bind, firid: int) -> DocFirma:
    session_factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        row = session.get(DocFirma, firid)
        assert row is not None
        session.expunge(row)
        return row
    finally:
        session.close()


def _events_for_firma(bind, firid: int) -> list[str]:
    session_factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        rows = session.execute(
            text(
                """
                SELECT evento
                FROM audifir
                WHERE enttip = 'FIRMA' AND entid = :firid
                ORDER BY audid
                """
            ),
            {"firid": firid},
        ).all()
        return [row[0] for row in rows]
    finally:
        session.close()


def _non_read_locks_count(bind) -> int:
    session_factory = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    session = session_factory()
    try:
        value = session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM pg_locks l
                JOIN pg_class c ON c.oid = l.relation
                WHERE c.relname IN ('docfirma', 'docfir', 'docpart', 'firpos')
                  AND l.mode NOT IN ('AccessShareLock', 'RowShareLock')
                """
            )
        ).scalar()
        return int(value or 0)
    finally:
        session.close()


def _assert_no_generated_output(path: Path) -> None:
    if path.exists():
        assert not any(path.iterdir())


def _assert_source_intact(context: dict[str, object]) -> None:
    source_path = Path(context["source_path"])
    source_bytes = context["source_bytes"]
    assert source_path.read_bytes() == source_bytes
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == context["source_sha"]


def test_pdf_integration_generada_exitosa(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(
        tmp_path,
        clock=datetime(2026, 7, 30, 9, 15, 0, tzinfo=_BOGOTA),
    )
    initial = _fresh_docfirma(db_session.get_bind(), context["firid"])
    artifact = None
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()

    try:
        assert artifact.firid == context["firid"]
        assert artifact.opeid == context["opeid"]
        assert artifact.path.exists()
        assert artifact.size_bytes == artifact.path.stat().st_size
        assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
        assert artifact.page_count == 1

        stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
        assert stored.estado == EstadoDocFirma.GENERADA.value
        assert stored.hasfin == artifact.sha256
        assert stored.revnum == initial.revnum + 1
        assert stored.fecmod != initial.fecmod
        assert artifact.sha256 != context["source_sha"]
        assert _events_for_firma(db_session.get_bind(), context["firid"]).count("FIR_GENE") == 1
        _assert_source_intact(context)
    finally:
        artifact.cleanup()
        assert not artifact.path.exists()
        _assert_no_generated_output(tmp_path / "generated")

def test_pdf_gerencia_visual_compacto_cabe_en_firpos_y_mantiene_evidencia(db_session, tmp_path):
    context = _seed_signature_context(
        db_session,
        tmp_path,
        TipoFirma.INTERNA.value,
        step_type="APROBAR",
        participant_name="Leonel Tarsicio Blanco Bahoque",
        participant_role="Representante legal",
        position_width=230,
        position_height=86,
    )
    service = _generated_service(
        tmp_path,
        clock=datetime(2026, 9, 29, 21, 33, 0, tzinfo=_BOGOTA),
    )
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()

    try:
        with fitz.open(artifact.path) as document:
            text = document[0].get_text()
        assert "LEONEL TARSICIO BLANCO BAHOQUE" in text
        assert "Representante legal" in text
        assert "Autorizado" in text
        assert "29/09/2026 21:33 COT" in text
        assert str(context["opeid"]).replace("-", "")[:12] in text
        assert context["source_sha"][:12] in text
        assert not list((tmp_path / "generated").glob("*.png"))
        assert not list((tmp_path / "generated").glob("*.jpg"))
        assert not list((tmp_path / "generated").glob("*.jpeg"))
        assert not list((tmp_path / "generated").glob("*.svg"))
    finally:
        artifact.cleanup()


def test_pdf_gerencia_visual_generada_por_documento(db_session, tmp_path):
    context_a = _seed_signature_context(
        db_session,
        tmp_path / "a",
        TipoFirma.INTERNA.value,
        step_type="APROBAR",
        participant_name="Leonel Tarsicio Blanco Bahoque",
        participant_role="Representante legal",
    )
    context_b = _seed_signature_context(
        db_session,
        tmp_path / "b",
        TipoFirma.INTERNA.value,
        step_type="APROBAR",
        participant_name="Leonel Tarsicio Blanco Bahoque",
        participant_role="Representante legal",
    )
    service = _generated_service(tmp_path, clock=datetime(2026, 9, 29, 21, 33, 0, tzinfo=_BOGOTA))
    artifact_a = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context_a["firid"],
        context_a["source_path"],
        usrmod=context_a["actor"].usrid,
    )
    artifact_b = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context_b["firid"],
        context_b["source_path"],
        usrmod=context_b["actor"].usrid,
    )
    db_session.commit()

    try:
        assert artifact_a.sha256 != artifact_b.sha256
        with fitz.open(artifact_a.path) as doc_a, fitz.open(artifact_b.path) as doc_b:
            text_a = unicodedata.normalize("NFKD", doc_a[0].get_text())
            text_b = unicodedata.normalize("NFKD", doc_b[0].get_text())
        assert str(context_a["opeid"]).replace("-", "")[:12] in text_a
        assert str(context_b["opeid"]).replace("-", "")[:12] in text_b
        assert str(context_a["opeid"]).replace("-", "")[:12] not in text_b
    finally:
        artifact_a.cleanup()
        artifact_b.cleanup()


def test_pdf_integration_hash_incorrecto_no_cambia_estado(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    invalid_source = tmp_path / "invalid-source.pdf"
    source_bytes = _write_pdf(invalid_source)
    wrong_hash = "0" * 64
    assert hashlib.sha256(source_bytes).hexdigest() != wrong_hash
    db_session.execute(text("UPDATE docfirma SET hasori = :hasori WHERE firid = :firid"), {"hasori": wrong_hash, "firid": context["firid"]})
    db_session.commit()

    with pytest.raises(SignatureIntegrityError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            invalid_source,
            usrmod=context["actor"].usrid,
        )

    stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
    assert stored.estado == EstadoDocFirma.INICIADA.value
    assert stored.revnum == 1
    assert _events_for_firma(db_session.get_bind(), context["firid"]).count("FIR_GENE") == 0
    _assert_no_generated_output(tmp_path / "generated")


def test_pdf_integration_pdf_invalido_no_cambia_estado(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    invalid_source = tmp_path / "invalid-source.pdf"
    invalid_bytes = b"NOT-PDF"
    invalid_source.write_bytes(invalid_bytes)
    invalid_hash = hashlib.sha256(invalid_bytes).hexdigest()
    db_session.execute(
        text("UPDATE docfirma SET hasori = :hasori WHERE firid = :firid"),
        {"hasori": invalid_hash, "firid": context["firid"]},
    )
    db_session.commit()

    with pytest.raises(SignaturePayloadError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            invalid_source,
            usrmod=context["actor"].usrid,
        )

    stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
    assert stored.estado == EstadoDocFirma.INICIADA.value
    assert stored.revnum == 1
    assert _events_for_firma(db_session.get_bind(), context["firid"]).count("FIR_GENE") == 0
    _assert_no_generated_output(tmp_path / "generated")


def test_pdf_integration_posicion_invalida_no_cambia_estado(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    db_session.execute(
        text("UPDATE firpos SET posx = :posx WHERE firid = :firid"),
        {"posx": 9999, "firid": context["firid"]},
    )
    db_session.commit()

    with pytest.raises(SignaturePlacementError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            context["source_path"],
            usrmod=context["actor"].usrid,
        )

    stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
    assert stored.estado == EstadoDocFirma.INICIADA.value
    assert stored.revnum == 1
    assert _events_for_firma(db_session.get_bind(), context["firid"]).count("FIR_GENE") == 0
    _assert_no_generated_output(tmp_path / "generated")


def test_pdf_integration_stale_revnum_elimina_salida(db_session, tmp_path, monkeypatch):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    original_short_tx = service._mark_generated_short_tx

    def _bump_then_mark(db, firid, expected_revnum, hasfin, usrmod):
        other_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
        other_db = other_factory()
        try:
            row = other_db.get(DocFirma, firid)
            assert row is not None
            row.revnum += 1
            other_db.commit()
        finally:
            other_db.close()
        return original_short_tx(db, firid, expected_revnum, hasfin, usrmod)

    monkeypatch.setattr(service, "_mark_generated_short_tx", _bump_then_mark)

    with pytest.raises(SignatureConcurrencyError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            context["source_path"],
            usrmod=context["actor"].usrid,
        )

    stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
    assert stored.estado == EstadoDocFirma.INICIADA.value
    assert stored.revnum == 2
    _assert_no_generated_output(tmp_path / "generated")
    _assert_source_intact(context)


def test_pdf_integration_mark_generated_error_elimina_salida(db_session, tmp_path, monkeypatch):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)

    def _boom(*args, **kwargs):
        raise SignatureConcurrencyError("conflicto controlado")

    monkeypatch.setattr(signature_service, "mark_generated", _boom)

    with pytest.raises(SignatureConcurrencyError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            context["source_path"],
            usrmod=context["actor"].usrid,
        )

    stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
    assert stored.estado == EstadoDocFirma.INICIADA.value
    assert stored.revnum == 1
    _assert_no_generated_output(tmp_path / "generated")
    _assert_source_intact(context)


def test_pdf_integration_fir_gene_unico(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        eventos = _events_for_firma(db_session.get_bind(), context["firid"])
        assert eventos.count("FIR_GENE") == 1
        assert eventos.count("FIR_INIC") == 1
    finally:
        artifact.cleanup()


def test_pdf_integration_revnum_incrementa_una_vez(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    initial = _fresh_docfirma(db_session.get_bind(), context["firid"])
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
        assert stored.revnum == initial.revnum + 1
    finally:
        artifact.cleanup()


def test_pdf_integration_fecmod_actualizado(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    initial = _fresh_docfirma(db_session.get_bind(), context["firid"])
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
        assert stored.fecmod != initial.fecmod
        assert stored.fecmod > initial.fecmod
    finally:
        artifact.cleanup()


def test_pdf_integration_sin_locks_durante_pymupdf(db_session, tmp_path, monkeypatch):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    original_render = service._render_pdf_bytes

    def _check_locks(snapshot, source_pdf_path, signature_image_path=None):
        assert _non_read_locks_count(db_session.get_bind()) == 0
        return original_render(snapshot, source_pdf_path, signature_image_path)

    monkeypatch.setattr(service, "_render_pdf_bytes", _check_locks)
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        assert artifact.path.exists()
    finally:
        artifact.cleanup()


def test_pdf_integration_no_transiciones_posteriores(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
        assert stored.estado == EstadoDocFirma.GENERADA.value
        eventos = _events_for_firma(db_session.get_bind(), context["firid"])
        assert eventos == ["FIR_INIC", "FIR_GENE"]
    finally:
        artifact.cleanup()


def test_pdf_integration_fuente_permanece_intacta(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    before = context["source_path"].read_bytes()
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        assert context["source_path"].read_bytes() == before
        assert hashlib.sha256(context["source_path"].read_bytes()).hexdigest() == context["source_sha"]
    finally:
        artifact.cleanup()


def test_pdf_integration_hasfin_coincide_con_bytes(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        stored = _fresh_docfirma(db_session.get_bind(), context["firid"])
        artifact_hash = hashlib.sha256(artifact.path.read_bytes()).hexdigest()
        assert artifact.sha256 == artifact_hash
        assert stored.hasfin == artifact_hash
        assert len(artifact.sha256) == 64
        assert artifact.sha256 == artifact.sha256.lower()
    finally:
        artifact.cleanup()


def test_pdf_manuscrita_png_eliminado_tras_exito(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.MANUSCRITA.value)
    service = _generated_service(
        tmp_path,
        clock=datetime(2026, 7, 30, 9, 15, 0, tzinfo=_BOGOTA),
    )
    image_path = _generated_png(service)
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        signature_image_path=image_path,
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        assert not image_path.exists()
        _assert_source_intact(context)
    finally:
        artifact.cleanup()


def test_pdf_manuscrita_png_eliminado_ante_pdf_invalido(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.MANUSCRITA.value)
    service = _generated_service(tmp_path)
    image_path = _generated_png(service)
    invalid_source = tmp_path / "invalid-source.pdf"
    invalid_bytes = b"INVALID"
    invalid_source.write_bytes(invalid_bytes)
    db_session.execute(
        text("UPDATE docfirma SET hasori = :hasori WHERE firid = :firid"),
        {"hasori": hashlib.sha256(invalid_bytes).hexdigest(), "firid": context["firid"]},
    )
    db_session.commit()

    with pytest.raises(SignaturePayloadError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            invalid_source,
            signature_image_path=image_path,
            usrmod=context["actor"].usrid,
        )

    assert not image_path.exists()
    _assert_no_generated_output(tmp_path / "generated")


def test_pdf_manuscrita_png_eliminado_ante_posicion_invalida(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.MANUSCRITA.value)
    service = _generated_service(tmp_path)
    image_path = _generated_png(service)
    db_session.execute(
        text("UPDATE firpos SET ancho = :ancho WHERE firid = :firid"),
        {"ancho": 9999, "firid": context["firid"]},
    )
    db_session.commit()

    with pytest.raises(SignaturePlacementError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            context["source_path"],
            signature_image_path=image_path,
            usrmod=context["actor"].usrid,
        )

    assert not image_path.exists()
    _assert_no_generated_output(tmp_path / "generated")


def test_pdf_manuscrita_png_eliminado_ante_stale_revnum(db_session, tmp_path, monkeypatch):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.MANUSCRITA.value)
    service = _generated_service(tmp_path)
    image_path = _generated_png(service)
    original_short_tx = service._mark_generated_short_tx

    def _bump_then_mark(db, firid, expected_revnum, hasfin, usrmod):
        other_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
        other_db = other_factory()
        try:
            row = other_db.get(DocFirma, firid)
            assert row is not None
            row.revnum += 1
            other_db.commit()
        finally:
            other_db.close()
        return original_short_tx(db, firid, expected_revnum, hasfin, usrmod)

    monkeypatch.setattr(service, "_mark_generated_short_tx", _bump_then_mark)

    with pytest.raises(SignatureConcurrencyError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            context["source_path"],
            signature_image_path=image_path,
            usrmod=context["actor"].usrid,
        )

    assert not image_path.exists()
    _assert_no_generated_output(tmp_path / "generated")
    _assert_source_intact(context)


def test_pdf_manuscrita_png_y_pdf_eliminados_cuando_mark_generated_falla(db_session, tmp_path, monkeypatch):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.MANUSCRITA.value)
    service = _generated_service(tmp_path)
    image_path = _generated_png(service)

    def _boom(*args, **kwargs):
        raise SignatureConcurrencyError("conflicto controlado")

    monkeypatch.setattr(signature_service, "mark_generated", _boom)

    with pytest.raises(SignatureConcurrencyError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            context["source_path"],
            signature_image_path=image_path,
            usrmod=context["actor"].usrid,
        )

    assert not image_path.exists()
    _assert_no_generated_output(tmp_path / "generated")
    _assert_source_intact(context)


def test_pdf_manuscrita_fuente_conservada_y_hash_identico(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.MANUSCRITA.value)
    service = _generated_service(
        tmp_path,
        clock=datetime(2026, 7, 30, 9, 15, 0, tzinfo=_BOGOTA),
    )
    image_path = _generated_png(service)
    before = context["source_path"].read_bytes()
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        signature_image_path=image_path,
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        assert context["source_path"].read_bytes() == before
        assert hashlib.sha256(context["source_path"].read_bytes()).hexdigest() == context["source_sha"]
    finally:
        artifact.cleanup()
        assert not image_path.exists()


def test_pdf_interna_pdf_eliminado_ante_stale_revnum(db_session, tmp_path, monkeypatch):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    original_short_tx = service._mark_generated_short_tx

    def _bump_then_mark(db, firid, expected_revnum, hasfin, usrmod):
        other_factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
        other_db = other_factory()
        try:
            row = other_db.get(DocFirma, firid)
            assert row is not None
            row.revnum += 1
            other_db.commit()
        finally:
            other_db.close()
        return original_short_tx(db, firid, expected_revnum, hasfin, usrmod)

    monkeypatch.setattr(service, "_mark_generated_short_tx", _bump_then_mark)

    with pytest.raises(SignatureConcurrencyError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            context["source_path"],
            usrmod=context["actor"].usrid,
        )

    _assert_no_generated_output(tmp_path / "generated")
    _assert_source_intact(context)


def test_pdf_interna_pdf_eliminado_cuando_mark_generated_falla(db_session, tmp_path, monkeypatch):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)

    def _boom(*args, **kwargs):
        raise SignatureConcurrencyError("conflicto controlado")

    monkeypatch.setattr(signature_service, "mark_generated", _boom)

    with pytest.raises(SignatureConcurrencyError):
        service.generate_signature_pdf_and_mark_generated(
            db_session,
            context["firid"],
            context["source_path"],
            usrmod=context["actor"].usrid,
        )

    _assert_no_generated_output(tmp_path / "generated")
    _assert_source_intact(context)


def test_pdf_interna_fuente_conservada_y_hash_identico(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = _generated_service(tmp_path)
    before = context["source_path"].read_bytes()
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()
    try:
        assert context["source_path"].read_bytes() == before
        assert hashlib.sha256(context["source_path"].read_bytes()).hexdigest() == context["source_sha"]
    finally:
        artifact.cleanup()
