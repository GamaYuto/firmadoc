from __future__ import annotations

import hashlib
import struct
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

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
from app.services.signature_exceptions import SignatureIntegrityError, SignaturePlacementError
from app.services.signature_service import signature_service
from app.services.step_service import step_service
from app.services.temporary_artifact_service import TemporaryArtifactService


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


def _bbox_of_nonwhite_pixels(
    page: fitz.Page,
    *,
    left: int = 0,
    top: int = 0,
    width: int | None = None,
    height: int | None = None,
) -> tuple[int, int, int, int] | None:
    pix = page.get_pixmap(alpha=True)
    samples = pix.samples
    stride = 4
    clip_right = pix.width if width is None else min(pix.width, left + width)
    clip_bottom = pix.height if height is None else min(pix.height, top + height)
    min_x = min_y = None
    max_x = max_y = None

    for y in range(top, clip_bottom):
        row_offset = y * pix.width * stride
        for x in range(left, clip_right):
            index = row_offset + x * stride
            red, green, blue, alpha = samples[index : index + 4]
            if alpha == 0 or (red == 255 and green == 255 and blue == 255):
                continue
            if min_x is None:
                min_x = max_x = x
                min_y = max_y = y
            else:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None
    return min_x, min_y, max_x, max_y


def _seed_signature_context(
    db,
    tmp_path: Path,
    tipfir: str,
    *,
    page_rotation: int = 0,
    position_rotation: int = 0,
):
    source_path = tmp_path / "source.pdf"
    source_bytes = _write_pdf(source_path, page_rotation=page_rotation)
    source_sha = hashlib.sha256(source_bytes).hexdigest()

    plantill = Plantill(tplcod="TPL-PDF", tplnom="Plantilla PDF", tplver=1, numpag=1, usrcre="admin", estado="ACTIVA")
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

    flujo = flow_service.create_flow(db, FlujoCreate(flucod="F_PDF", flunom="Flujo PDF", usrcre="admin"))
    flow_service.add_step(
        db,
        flujo.fluid,
        PasoCreate(pascod="P1", pasnom="Paso 1", pastip="FIRMAR", orden=1, rolreq="Firmante", plazo=60),
        "admin",
    )
    flow_service.activate_flow(db, flujo.fluid, "admin")

    doc = create_documento(
        db=db,
        nodid="node-pdf-1",
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
        nomcom="Usuario Firmante",
        correo="firmante@empresa.local",
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
        usrid="firmante",
        nomcom="Usuario Firmante",
        correo="firmante@empresa.local",
        rolpro="Firmante",
    )
    positions = [
        {
            "pagina": 1,
            "posx": 72,
            "posy": 72,
            "ancho": 220,
            "alto": 100,
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
    db.commit()

    return {
        "source_path": source_path,
        "source_sha": source_sha,
        "docid": doc.docid,
        "partid": part.parid,
        "firid": firma.firid,
        "opeid": firma.opeid,
        "actor": actor,
        "campid": camp.camid,
    }


def _assert_bbox_inside_stamp(
    bbox: tuple[int, int, int, int],
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    margin: int = 8,
) -> None:
    x0, y0, x1, y1 = bbox
    assert x0 >= left - margin
    assert y0 >= top - margin
    assert x1 <= left + width + margin
    assert y1 <= top + height + margin


def _assert_bbox_matches_centered_fit(
    bbox: tuple[int, int, int, int],
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    image_width: int,
    image_height: int,
    rotation: int = 0,
    tolerance: float = 2.0,
) -> None:
    x0, y0, x1, y1 = bbox
    effective_width = image_height if rotation in {90, 270} else image_width
    effective_height = image_width if rotation in {90, 270} else image_height
    scale = min(width / effective_width, height / effective_height)
    expected_width = effective_width * scale
    expected_height = effective_height * scale
    expected_x0 = left + (width - expected_width) / 2
    expected_y0 = top + (height - expected_height) / 2
    expected_x1 = expected_x0 + expected_width
    expected_y1 = expected_y0 + expected_height

    assert abs(x0 - expected_x0) <= tolerance
    assert abs(y0 - expected_y0) <= tolerance
    assert abs(x1 - expected_x1) <= tolerance
    assert abs(y1 - expected_y1) <= tolerance


def test_generate_internal_signature_updates_docfirma_and_keeps_source(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value, page_rotation=90)
    artifact_dir = tmp_path / "generated"
    service = PdfSignatureService(
        artifact_service=TemporaryArtifactService(base_dir=artifact_dir),
        clock=lambda: datetime(2026, 7, 30, 9, 15, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    original_source = context["source_path"].read_bytes()
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
        assert artifact.page_count == 1
        assert artifact.path.exists()
        assert artifact.size_bytes == artifact.path.stat().st_size
        assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
        assert original_source == context["source_path"].read_bytes()

        with fitz.open(artifact.path) as document:
            page = document[0]
            assert page.rotation == 90
            text = " ".join(page.get_text("text").split())
            assert "FIRMADO ELECTRÓNICAMENTE" in text
            assert "Usuario Firmante" in text
            assert "Operación:" in text
            assert context["source_sha"][:16] in text
            bbox = _bbox_of_nonwhite_pixels(page, left=72, top=72, width=220, height=100)
            assert bbox is not None
            _assert_bbox_inside_stamp(bbox, left=72, top=72, width=220, height=100)

        stored = db_session.get(DocFirma, context["firid"])
        assert stored is not None
        assert stored.estado == EstadoDocFirma.GENERADA.value
        assert stored.hasfin == artifact.sha256
        assert stored.revnum == 2
    finally:
        artifact.cleanup()
        assert not artifact.path.exists()


@pytest.mark.parametrize("page_rotation", [0, 90, 180, 270])
def test_generate_internal_signature_supports_page_rotations(db_session, tmp_path, page_rotation):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value, page_rotation=page_rotation)
    service = PdfSignatureService(
        artifact_service=TemporaryArtifactService(base_dir=tmp_path / "generated"),
        clock=lambda: datetime(2026, 7, 30, 9, 15, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    artifact = service.generate_signed_pdf(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()

    try:
        with fitz.open(artifact.path) as document:
            page = document[0]
            bbox = _bbox_of_nonwhite_pixels(page, left=72, top=72, width=220, height=100)
            assert bbox is not None
            _assert_bbox_inside_stamp(bbox, left=72, top=72, width=220, height=100)
    finally:
        artifact.cleanup()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_generate_internal_signature_supports_signature_rotations(db_session, tmp_path, rotation):
    context = _seed_signature_context(
        db_session,
        tmp_path,
        TipoFirma.INTERNA.value,
        position_rotation=rotation,
    )
    service = PdfSignatureService(
        artifact_service=TemporaryArtifactService(base_dir=tmp_path / "generated"),
        clock=lambda: datetime(2026, 7, 30, 9, 15, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    artifact = service.generate_signed_pdf(
        db_session,
        context["firid"],
        context["source_path"],
        usrmod=context["actor"].usrid,
    )
    db_session.commit()

    try:
        with fitz.open(artifact.path) as document:
            page = document[0]
            bbox = _bbox_of_nonwhite_pixels(page, left=72, top=72, width=220, height=100)
            assert bbox is not None
            _assert_bbox_inside_stamp(bbox, left=72, top=72, width=220, height=100)
    finally:
        artifact.cleanup()


def test_generate_internal_signature_fails_when_box_is_too_small(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = PdfSignatureService(
        artifact_service=TemporaryArtifactService(base_dir=tmp_path / "generated"),
        clock=lambda: datetime(2026, 7, 30, 9, 15, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    db_session.execute(
        text(
            """
            UPDATE firpos
            SET ancho = 2, alto = 2
            WHERE firid = :firid
            """
        ),
        {"firid": context["firid"]},
    )
    db_session.commit()

    with pytest.raises(SignaturePlacementError):
        service.generate_signed_pdf(
            db_session,
            context["firid"],
            context["source_path"],
            usrmod=context["actor"].usrid,
        )


def test_generate_signed_pdf_rejects_hash_mismatch(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.INTERNA.value)
    service = PdfSignatureService(
        artifact_service=TemporaryArtifactService(base_dir=tmp_path / "generated"),
        clock=lambda: datetime(2026, 7, 30, 9, 15, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    db_session.execute(
        text(
            """
            UPDATE docfirma
            SET hasori = :hasori
            WHERE firid = :firid
            """
        ),
        {"hasori": "0" * 64, "firid": context["firid"]},
    )
    db_session.commit()

    with pytest.raises(SignatureIntegrityError):
        service.generate_signed_pdf(
            db_session,
            context["firid"],
            context["source_path"],
            usrmod=context["actor"].usrid,
        )


def test_generate_manuscrita_signature_uses_png_and_inserts_image_and_cleans_temp(db_session, tmp_path):
    context = _seed_signature_context(db_session, tmp_path, TipoFirma.MANUSCRITA.value)
    artifact_service = TemporaryArtifactService(base_dir=tmp_path / "generated")
    image_path = artifact_service.write_bytes(_png_bytes(72, 28, b"\x2c\x7a\xd9\xff"), prefix="signature-", suffix=".png")

    service = PdfSignatureService(
        artifact_service=artifact_service,
        clock=lambda: datetime(2026, 7, 30, 9, 15, 0, tzinfo=ZoneInfo("America/Bogota")),
    )

    original_source = context["source_path"].read_bytes()
    artifact = service.generate_signature_pdf_and_mark_generated(
        db_session,
        context["firid"],
        context["source_path"],
        signature_image_path=image_path,
        usrmod=context["actor"].usrid,
    )
    db_session.commit()

    try:
        assert artifact.path.exists()
        assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
        assert artifact.page_count == 1
        assert original_source == context["source_path"].read_bytes()
        assert not image_path.exists()

        with fitz.open(artifact.path) as document:
            page = document[0]
            images = page.get_images(full=True)
            assert len(images) >= 1
            bbox = _bbox_of_nonwhite_pixels(page, left=72, top=72, width=220, height=100)
            assert bbox is not None
            _assert_bbox_matches_centered_fit(
                bbox,
                left=72,
                top=72,
                width=220,
                height=100,
                image_width=72,
                image_height=28,
            )

        stored = db_session.get(DocFirma, context["firid"])
        assert stored is not None
        assert stored.estado == EstadoDocFirma.GENERADA.value
        assert stored.hasfin == artifact.sha256
        assert stored.revnum == 2
    finally:
        artifact.cleanup()
        assert not artifact.path.exists()
