import pytest
import uuid
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.docfirma import DocFirma, EstadoDocFirma, TipoFirma
from app.models.firpos import Firpos
from app.models.docfir import DocFir, EstadoDoc
from app.models.docpaso import DocPaso, EstadoDocPaso
from app.models.docpart import DocPart
from app.models.audifir import Audifir
from app.models.tplcamp import TplCamp
from app.models.plantill import Plantill
from app.schemas.docfirma import ResultContract, ResultFase, ResultFlag
from app.core.identity import IdentitySnapshot
from app.services.signature_service import signature_service
from app.services.signature_exceptions import (
    SignatureNotFoundError,
    SignatureNotAllowedError,
    SignatureConcurrencyError,
    SignatureAlreadyCompletedError,
    SignatureActiveAttemptError,
    SignaturePayloadError,
    SignatureStateError,
    SignatureIntegrityError,
    SignatureVersionConflictError
)

# Helper fixtures
@pytest.fixture
def test_actor() -> IdentitySnapshot:
    return IdentitySnapshot(
        usrid="testuser",
        nomcom="Usuario Test",
        correo="testuser@empresa.local",
        rolpro="Gerente"
    )

@pytest.fixture
def mock_evidence() -> ResultContract:
    return ResultContract(
        schema_ver=1,
        fase=ResultFase.VERIFICACION,
        remcod=200,
        remmsg="Verified",
        recint=0,
        verchk=True,
        haschk=True,
        flags=[ResultFlag.ALFRESCO_OK, ResultFlag.HASH_MATCH]
    )

@pytest.fixture
def setup_agregado(db: Session) -> dict:
    # Clean tables first
    db.execute(sa.text("DELETE FROM firpos"))
    db.execute(sa.text("DELETE FROM docfirma"))
    db.execute(sa.text("DELETE FROM docpart"))
    db.execute(sa.text("DELETE FROM docpaso"))
    db.execute(sa.text("DELETE FROM docfir"))
    db.execute(sa.text("DELETE FROM tplcamp"))
    db.execute(sa.text("DELETE FROM plantill"))
    db.execute(sa.text("DELETE FROM flupaso"))
    db.execute(sa.text("DELETE FROM flujodoc"))
    db.commit()

    from app.schemas.flujo import FlujoCreate, PasoCreate
    from app.services.flow_service import flow_service
    from app.services.step_service import step_service
    from app.crud.crud_docfir import create_documento

    # Create plantill
    plantill = Plantill(tplcod="TPL-TEST", tplnom="Plantilla Test", tplver=1, numpag=1, usrcre="admin", estado="ACTIVA")
    db.add(plantill)
    db.flush()

    # Create tplcamp
    camp = TplCamp(tplid=plantill.tplid, camcod="C1", camnom="Firma1", camtip="FIRMA", pagina=1, posx=0.1, posy=0.1, ancho=0.2, alto=0.05, orden=1, usrcre="admin")
    db.add(camp)
    db.flush()

    # Create flow & step
    f_obj = FlujoCreate(flucod="F_TEST", flunom="Flujo de Pruebas", usrcre="admin")
    flujo = flow_service.create_flow(db, f_obj)
    p1 = PasoCreate(pascod="P1", pasnom="Paso 1", pastip="FIRMAR", orden=1, rolreq="Firmante", plazo=60)
    flow_service.add_step(db, flujo.fluid, p1, "admin")
    flow_service.activate_flow(db, flujo.fluid, "admin")

    # Create docfir
    doc = create_documento(db, nodid="node-uuid-1", docnom="Test.pdf", tamano=1024, verini="1.0", hasori="a" * 64, usrcre="admin")
    doc.fluid = flujo.fluid
    doc.tplid = plantill.tplid
    doc.estado = EstadoDoc.EN_CURSO.value
    db.flush()

    # Instantiate steps
    pasos = step_service.instantiate_document_steps(db, doc.docid, "admin")
    paso = pasos[0]

    # Create docpart
    part = DocPart(
        dpasid=paso.dpasid,
        usrid="testuser",
        nomcom="Usuario Test",
        correo="testuser@empresa.local",
        rolpro="Gerente",
        orden=1,
        obliga=True,
        estado="DISPONIBLE",
        verlock=1,
        usrcre="admin"
    )
    db.add(part)
    db.commit()

    return {
        "doc": doc,
        "paso": paso,
        "part": part,
        "camp": camp
    }

import sqlalchemy as sa

# Model and Migration validations
def test_docfirma_creation_valida(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.INICIADA.value,
        verori="1.0",
        hasori="a" * 64,
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    db.commit()
    assert firma.firid is not None
    assert firma.revnum == 1
    assert firma.fecmod is not None

def test_firpos_creation_valida(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.INICIADA.value,
        verori="1.0",
        hasori="a" * 64,
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    db.flush()

    pos = Firpos(
        firid=firma.firid,
        pagina=1,
        posx=Decimal("100.00"),
        posy=Decimal("150.00"),
        ancho=Decimal("200.00"),
        alto=Decimal("50.00"),
        rotaci=0,
        orden=1,
        camid=ag["camp"].camid
    )
    db.add(pos)
    db.commit()
    assert pos.posid is not None

# Constraint validations
def test_docfirma_estado_invalido(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado="INVALIDO_STATE",
        verori="1.0",
        hasori="a" * 64,
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_tipfir_invalido(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir="DIGITAL",
        estado=EstadoDocFirma.INICIADA.value,
        verori="1.0",
        hasori="a" * 64,
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_hasori_invalido(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.INICIADA.value,
        verori="1.0",
        hasori="not-hex-sha256",
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_hasfin_required_desde_generada(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.GENERADA.value, # GENERADA requires hasfin
        verori="1.0",
        hasori="a" * 64,
        hasfin=None,
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_verfin_required_desde_cargada(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.CARGADA.value,
        verori="1.0",
        hasori="a" * 64,
        hasfin="b" * 64,
        verfin=None, # CARGADA requires verfin
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_result_required_desde_verificando(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.VERIFICANDO.value,
        verori="1.0",
        hasori="a" * 64,
        hasfin="b" * 64,
        verfin="1.1",
        result=None, # VERIFICANDO requires result
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_errcod_requerido_en_fallida(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.FALLIDA.value,
        verori="1.0",
        hasori="a" * 64,
        errcod=None, # FALLIDA requires errcod
        fecini=datetime.now(timezone.utc),
        fecfin=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_motivo_requerido_en_cancelada(db: Session, setup_agregado):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.CANCELADA.value,
        verori="1.0",
        hasori="a" * 64,
        motivo=None, # CANCELADA requires motivo
        fecini=datetime.now(timezone.utc),
        fecfin=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_completada_sin_errcod_ni_motivo(db: Session, setup_agregado, mock_evidence):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.COMPLETADA.value,
        verori="1.0",
        hasori="a" * 64,
        hasfin="b" * 64,
        verfin="1.1",
        result=mock_evidence.model_dump(mode="json"),
        fecini=datetime.now(timezone.utc),
        fecfin=datetime.now(timezone.utc),
        errcod="SOME_ERROR", # COMPLETADA cannot have errcod
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_fecfin_required_en_terminales(db: Session, setup_agregado, mock_evidence):
    ag = setup_agregado
    firma = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.COMPLETADA.value,
        verori="1.0",
        hasori="a" * 64,
        hasfin="b" * 64,
        verfin="1.1",
        result=mock_evidence.model_dump(mode="json"),
        fecini=datetime.now(timezone.utc),
        fecfin=None, # COMPLETADA requires fecfin
        usrcre="testuser"
    )
    db.add(firma)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_operacion_activa_duplicada_por_docid(db: Session, setup_agregado):
    ag = setup_agregado
    f1 = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.INICIADA.value,
        verori="1.0",
        hasori="a" * 64,
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(f1)
    
    f2 = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=2,
        intnum=2,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.GENERADA.value,
        verori="1.0",
        hasori="a" * 64,
        hasfin="b" * 64,
        fecini=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(f2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_docfirma_completada_duplicada_por_parid(db: Session, setup_agregado, mock_evidence):
    ag = setup_agregado
    f1 = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=1,
        intnum=1,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.COMPLETADA.value,
        verori="1.0",
        hasori="a" * 64,
        hasfin="b" * 64,
        verfin="1.1",
        result=mock_evidence.model_dump(mode="json"),
        fecini=datetime.now(timezone.utc),
        fecfin=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(f1)
    
    f2 = DocFirma(
        docid=ag["doc"].docid,
        parid=ag["part"].parid,
        opeid=uuid.uuid4(),
        secuen=2,
        intnum=2,
        tipfir=TipoFirma.INTERNA.value,
        estado=EstadoDocFirma.COMPLETADA.value,
        verori="1.0",
        hasori="a" * 64,
        hasfin="b" * 64,
        verfin="1.1",
        result=mock_evidence.model_dump(mode="json"),
        fecini=datetime.now(timezone.utc),
        fecfin=datetime.now(timezone.utc),
        usrcre="testuser"
    )
    db.add(f2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

# Pydantic ResultContract tests
def test_result_contract_validation():
    # Valid PUBLICATION result
    pub = ResultContract(
        schema_ver=1,
        fase=ResultFase.PUBLICACION,
        remcod=201,
        remmsg="Created",
        recint=0,
        verchk=False,
        haschk=False,
        flags=[ResultFlag.ALFRESCO_OK]
    )
    assert pub.fase == ResultFase.PUBLICACION

    # Valid VERIFICATION result
    veri = ResultContract(
        schema_ver=1,
        fase=ResultFase.VERIFICACION,
        remcod=200,
        remmsg="Verified",
        recint=0,
        verchk=True,
        haschk=True,
        flags=[ResultFlag.ALFRESCO_OK, ResultFlag.HASH_MATCH]
    )
    assert veri.fase == ResultFase.VERIFICACION

    # Extra fields forbidden
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResultContract(
            schema_ver=1,
            fase=ResultFase.PUBLICACION,
            remcod=201,
            remmsg="Created",
            recint=0,
            verchk=False,
            haschk=False,
            flags=[ResultFlag.ALFRESCO_OK],
            token="secret_token" # forbidden
        )

# Signature Reserve Attempt tests
def test_reserve_signature_attempt_exito(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    positions = [
        {"pagina": 1, "posx": 10.0, "posy": 20.0, "ancho": 150.0, "alto": 50.0, "orden": 1, "camid": ag["camp"].camid}
    ]
    
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=positions,
        verori="1.0",
        hasori="a" * 64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.commit()

    assert firma.firid is not None
    assert firma.estado == EstadoDocFirma.INICIADA.value
    assert len(firma.posiciones) == 1
    assert ag["part"].estado == "EN_PROCESO"
    assert ag["part"].verlock == 2

    # Check audit
    audit = db.scalars(select(Audifir).where(Audifir.evento == "FIR_INIC")).first()
    assert audit is not None
    assert audit.enttip == "FIRMA"
    assert audit.entid == firma.firid

def test_reserve_signature_attempt_actor_incorrecto(db: Session, setup_agregado):
    ag = setup_agregado
    bad_actor = IdentitySnapshot(usrid="baduser", nomcom="Bad User", correo="bad@user.com")
    with pytest.raises(SignatureNotAllowedError):
        signature_service.reserve_signature_attempt(
            db=db,
            parid=ag["part"].parid,
            tipfir=TipoFirma.INTERNA.value,
            positions=[],
            verori="1.0",
            hasori="a" * 64,
            actor=bad_actor,
            expected_participant_verlock=1
        )
    # Check denied audit
    audit = db.scalars(select(Audifir).where(Audifir.evento == "PAR_FDEN")).first()
    has_audit = audit is not None
    enttip = audit.enttip if audit else None
    db.rollback()
    assert has_audit
    assert enttip == "PARTICIPANTE"

# State transitions tests
def test_signature_transitions(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    positions = [
        {"pagina": 1, "posx": 10.0, "posy": 20.0, "ancho": 150.0, "alto": 50.0, "orden": 1, "camid": ag["camp"].camid}
    ]
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=positions,
        verori="1.0",
        hasori="a" * 64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()

    # INICIADA -> GENERADA
    new_rev = signature_service.mark_generated(db, firma.firid, expected_revnum=1, hasfin="b" * 64, usrmod="testuser")
    assert firma.estado == EstadoDocFirma.GENERADA.value
    assert firma.hasfin == "b" * 64

    # GENERADA -> SUBIENDO
    new_rev = signature_service.mark_upload_started(db, firma.firid, expected_revnum=new_rev, usrmod="testuser")
    assert firma.estado == EstadoDocFirma.SUBIENDO.value

    # SUBIENDO -> CARGADA
    pub_result = ResultContract(
        schema_ver=1,
        fase=ResultFase.PUBLICACION,
        remcod=201,
        remmsg="Created",
        recint=0,
        verchk=False,
        haschk=False,
        flags=[ResultFlag.ALFRESCO_OK]
    )
    new_rev = signature_service.mark_uploaded(db, firma.firid, expected_revnum=new_rev, verfin="1.1", result_data=pub_result, usrmod="testuser")
    assert firma.estado == EstadoDocFirma.CARGADA.value
    assert firma.verfin == "1.1"

    # CARGADA -> VERIFICANDO
    new_rev = signature_service.mark_verification_started(db, firma.firid, expected_revnum=new_rev, usrmod="testuser")
    assert firma.estado == EstadoDocFirma.VERIFICANDO.value

    # VERIFICANDO -> persist evidence
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=mock_evidence.remcod, remmsg=mock_evidence.remmsg, flags=mock_evidence.flags, usrmod="testuser")
    
    # Finalize signature
    signature_service.finalize_verified_signature(
        db=db,
        firid=firma.firid,
        expected_revnum=new_rev,
        expected_participant_verlock=ag["part"].verlock,
        actor=test_actor
    )
    db.commit()

    assert firma.estado == EstadoDocFirma.COMPLETADA.value
    assert ag["part"].estado == "COMPLETADO"
    assert ag["paso"].estado == EstadoDocPaso.COMPLETADO.value
    assert ag["doc"].estado == EstadoDoc.COMPLETADO.value

# Idempotency tests
def test_finalize_idempotencia(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    positions = [
        {"pagina": 1, "posx": 10.0, "posy": 20.0, "ancho": 150.0, "alto": 50.0, "orden": 1, "camid": ag["camp"].camid}
    ]
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=positions,
        verori="1.0",
        hasori="a" * 64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()

    new_rev = signature_service.mark_generated(db, firma.firid, expected_revnum=1, hasfin="b" * 64, usrmod="testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, expected_revnum=new_rev, usrmod="testuser")
    
    pub_result = ResultContract(
        schema_ver=1,
        fase=ResultFase.PUBLICACION,
        remcod=201,
        remmsg="Created",
        recint=0,
        verchk=False,
        haschk=False,
        flags=[ResultFlag.ALFRESCO_OK]
    )
    new_rev = signature_service.mark_uploaded(db, firma.firid, expected_revnum=new_rev, verfin="1.1", result_data=pub_result, usrmod="testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, expected_revnum=new_rev, usrmod="testuser")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=mock_evidence.remcod, remmsg=mock_evidence.remmsg, flags=mock_evidence.flags, usrmod="testuser")
    
    # First finalize
    signature_service.finalize_verified_signature(
        db=db,
        firid=firma.firid,
        expected_revnum=new_rev,
        expected_participant_verlock=ag["part"].verlock,
        actor=test_actor
    )
    db.commit()

    fecmod_before = firma.fecmod
    revnum_before = firma.revnum

    # Second finalize (idempotent call)
    signature_service.finalize_verified_signature(
        db=db,
        firid=firma.firid,
        expected_revnum=new_rev, # old revnum
        expected_participant_verlock=ag["part"].verlock,
        actor=test_actor
    )
    db.commit()

    # Ensure nothing changed
    assert firma.revnum == revnum_before
    assert firma.fecmod == fecmod_before

def test_finalizacion_completada_hash_dif(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    positions = [
        {"pagina": 1, "posx": 10.0, "posy": 20.0, "ancho": 150.0, "alto": 50.0, "orden": 1, "camid": ag["camp"].camid}
    ]
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=positions,
        verori="1.0",
        hasori="a" * 64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()

    new_rev = signature_service.mark_generated(db, firma.firid, expected_revnum=1, hasfin="b" * 64, usrmod="testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, expected_revnum=new_rev, usrmod="testuser")
    
    pub_result = ResultContract(
        schema_ver=1,
        fase=ResultFase.PUBLICACION,
        remcod=201,
        remmsg="Created",
        recint=0,
        verchk=False,
        haschk=False,
        flags=[ResultFlag.ALFRESCO_OK]
    )
    new_rev = signature_service.mark_uploaded(db, firma.firid, expected_revnum=new_rev, verfin="1.1", result_data=pub_result, usrmod="testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, expected_revnum=new_rev, usrmod="testuser")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=mock_evidence.remcod, remmsg=mock_evidence.remmsg, flags=mock_evidence.flags, usrmod="testuser")
    
    # Finalize
    signature_service.finalize_verified_signature(
        db=db,
        firid=firma.firid,
        expected_revnum=new_rev,
        expected_participant_verlock=ag["part"].verlock,
        actor=test_actor
    )
    db.commit()

    # Alter the database result evidence directly (simulating corruption/inconsistency)
    firma.result = {
        "schema_ver": 1,
        "fase": "VERIFICACION",
        "remcod": 200,
        "remmsg": "Verified",
        "recint": 0,
        "verchk": False, # altered
        "haschk": False, # altered
        "flags": []
    }
    db.commit()

    # Re-call finalize -> must fail with SignatureIntegrityError
    with pytest.raises(SignatureIntegrityError):
        signature_service.finalize_verified_signature(
            db=db,
            firid=firma.firid,
            expected_revnum=firma.revnum,
            expected_participant_verlock=ag["part"].verlock,
            actor=test_actor
        )
    db.rollback()

def test_alfresco_desconectado(db: Session, setup_agregado, test_actor, monkeypatch):
    import builtins
    import getpass
    import importlib
    import inspect
    import socket
    from unittest.mock import patch

    signature_module = importlib.import_module("app.services.signature_service")
    source = inspect.getsource(signature_module)
    assert "alfresco_client" not in source.lower()
    assert "alfresco_service" not in source.lower()

    def block_socket(*args, **kwargs):
        raise RuntimeError("Network calls are forbidden during docfirma tests")

    monkeypatch.setattr(socket.socket, "connect", block_socket)
    monkeypatch.setattr(socket, "create_connection", block_socket)

    with patch.object(builtins, "input", side_effect=AssertionError("input() no debe pedirse")), patch.object(
        getpass, "getpass", side_effect=AssertionError("getpass() no debe pedirse")
    ):
        ag = setup_agregado
        firma = signature_service.reserve_signature_attempt(
            db=db,
            parid=ag["part"].parid,
            tipfir=TipoFirma.INTERNA.value,
            positions=[{"pagina": 1, "posx": 10, "posy": 20, "ancho": 150, "alto": 50, "orden": 1, "camid": ag["camp"].camid}],
            verori="1.0",
            hasori="a" * 64,
            actor=test_actor,
            expected_participant_verlock=1,
        )
        db.rollback()

    assert firma.firid is not None
    assert not any("alfresco" in key.lower() for key in vars(signature_service))
    assert not hasattr(signature_service, "alfresco_adapter")

# --- Schema ResultContract Tests ---
def test_result_fase_reserva_valida():
    r = ResultContract(schema_ver=1, fase=ResultFase.RESERVA, verchk=True, haschk=True, flags=[ResultFlag.ALFRESCO_OK])
    assert r.fase == ResultFase.RESERVA

def test_result_fase_generacion_valida():
    r = ResultContract(schema_ver=1, fase=ResultFase.GENERACION, verchk=True, haschk=True, flags=[ResultFlag.ALFRESCO_OK])
    assert r.fase == ResultFase.GENERACION

def test_result_fase_revalidacion_valida():
    r = ResultContract(schema_ver=1, fase=ResultFase.REVALIDACION, verchk=True, haschk=True, flags=[ResultFlag.ALFRESCO_OK])
    assert r.fase == ResultFase.REVALIDACION

def test_result_fase_publicacion_valida():
    r = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, verchk=True, haschk=True, flags=[ResultFlag.ALFRESCO_OK])
    assert r.fase == ResultFase.PUBLICACION

def test_result_fase_verificacion_valida():
    r = ResultContract(schema_ver=1, fase=ResultFase.VERIFICACION, verchk=True, haschk=True, flags=[ResultFlag.HASH_MATCH])
    assert r.fase == ResultFase.VERIFICACION

def test_result_fase_finalizacion_valida():
    r = ResultContract(schema_ver=1, fase=ResultFase.FINALIZACION, verchk=True, haschk=True, flags=[ResultFlag.HASH_MATCH])
    assert r.fase == ResultFase.FINALIZACION

def test_result_fase_reconciliacion_valida():
    r = ResultContract(schema_ver=1, fase=ResultFase.RECONCILIACION, verchk=True, haschk=True, flags=[ResultFlag.ALFRESCO_OK])
    assert r.fase == ResultFase.RECONCILIACION

def test_result_fase_subiendo_rechazada():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResultContract(schema_ver=1, fase="SUBIENDO", verchk=True, haschk=True, flags=[])

def test_result_fase_cargada_rechazada():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResultContract(schema_ver=1, fase="CARGADA", verchk=True, haschk=True, flags=[])

def test_result_campo_extra_rechazado():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResultContract(schema_ver=1, fase=ResultFase.RESERVA, verchk=True, haschk=True, flags=[], extra_field="forbidden")

def test_result_recint_negativo_rechazado():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResultContract(schema_ver=1, fase=ResultFase.RESERVA, verchk=True, haschk=True, flags=[], recint=-1)

def test_result_remmsg_excesivo_rechazado():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResultContract(schema_ver=1, fase=ResultFase.RESERVA, verchk=True, haschk=True, flags=[], remmsg="a" * 201)

def test_result_flag_desconocido_rechazado():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ResultContract(schema_ver=1, fase=ResultFase.RESERVA, verchk=True, haschk=True, flags=["UNKNOWN_FLAG"])


# --- Detailed State Transition Tests ---
def test_trans_iniciada_generada(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, expected_revnum=1, hasfin="b"*64, usrmod="testuser")
    assert firma.estado == EstadoDocFirma.GENERADA.value
    assert firma.revnum == 2

def test_trans_generada_subiendo(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, expected_revnum=new_rev, usrmod="testuser")
    assert firma.estado == EstadoDocFirma.SUBIENDO.value
    assert firma.revnum == 3

def test_trans_subiendo_cargada(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, expected_revnum=new_rev, verfin="1.1", result_data=pub_result, usrmod="testuser")
    assert firma.estado == EstadoDocFirma.CARGADA.value
    assert firma.revnum == 4

def test_trans_cargada_verificando(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, expected_revnum=new_rev, usrmod="testuser")
    assert firma.estado == EstadoDocFirma.VERIFICANDO.value
    assert firma.revnum == 5

def test_trans_verificando_completada(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=mock_evidence.remcod, remmsg=mock_evidence.remmsg, flags=mock_evidence.flags, usrmod="testuser")
    signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev, expected_participant_verlock=ag["part"].verlock, actor=test_actor)
    assert firma.estado == EstadoDocFirma.COMPLETADA.value

def test_trans_iniciada_cancelada(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    signature_service.cancel_signature_attempt(db, firma.firid, expected_revnum=1, motivo="Cancelación test", actor=test_actor)
    assert firma.estado == EstadoDocFirma.CANCELADA.value

def test_trans_generada_cancelada(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    signature_service.cancel_signature_attempt(db, firma.firid, expected_revnum=new_rev, motivo="Cancelación test", actor=test_actor)
    assert firma.estado == EstadoDocFirma.CANCELADA.value

def test_trans_cancelada_rechazada_desde_subiendo(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    with pytest.raises(SignatureStateError):
        signature_service.cancel_signature_attempt(db, firma.firid, expected_revnum=new_rev, motivo="Cancelación test", actor=test_actor)

def test_trans_finalizacion_rechazada_desde_cargada(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    with pytest.raises(SignatureStateError):
        signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev, expected_participant_verlock=ag["part"].verlock, actor=test_actor)

def test_trans_finalizacion_rechazada_desde_subiendo(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    with pytest.raises(SignatureStateError):
        signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev, expected_participant_verlock=ag["part"].verlock, actor=test_actor)

def test_trans_estados_terminales_inmutables(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    signature_service.cancel_signature_attempt(db, firma.firid, expected_revnum=1, motivo="Cancelación test", actor=test_actor)
    with pytest.raises(SignatureStateError):
        signature_service.mark_generated(db, firma.firid, expected_revnum=2, hasfin="b"*64, usrmod="testuser")

def test_trans_fallida_requiere_errcod(db: Session, setup_agregado, test_actor):
    # FALLIDA requires errcod in DB
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    # Attempting to insert/update to FALLIDA without errcod should violate CheckConstraint
    firma.estado = "FALLIDA"
    firma.errcod = None
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_trans_conflicto_requiere_errcod(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    firma.estado = "CONFLICTO"
    firma.errcod = None
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_trans_cancelada_requiere_motivo(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    with pytest.raises(SignaturePayloadError):
        signature_service.cancel_signature_attempt(db, firma.firid, expected_revnum=1, motivo="", actor=test_actor)

def test_trans_updates_revnum_and_fecmod(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    t1 = firma.fecmod
    new_rev = signature_service.mark_generated(db, firma.firid, expected_revnum=1, hasfin="b"*64, usrmod="testuser")
    db.flush()
    assert new_rev == 2
    assert firma.fecmod > t1

def test_trans_records_single_audit_event(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    db.execute(sa.text("DELETE FROM audifir"))
    db.commit()
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    # Check that exactly one event (FIR_INIC) exists for this reservation
    events = db.scalars(select(Audifir).where(Audifir.enttip == "FIRMA")).all()
    assert len(events) == 1
    assert events[0].evento == "FIR_INIC"


# --- Evidence and Finalization Tests ---
def test_evidencia_verificacion_persistida(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=200, remmsg="Verified", flags=[ResultFlag.HASH_MATCH], usrmod="testuser")
    assert firma.result is not None
    assert firma.result["fase"] == "VERIFICACION"
    assert firma.result["verchk"] is True

def test_result_requerido_desde_verificando(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    # Putting state to VERIFICANDO but removing result (set to None/null) must fail
    firma.result = None
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_hash_match_requerido(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    with pytest.raises(SignaturePayloadError):
        # Missing HASH_MATCH flag
        signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=200, remmsg="Verified", flags=[ResultFlag.ALFRESCO_OK], usrmod="testuser")

def test_persist_evidence_returns_new_revnum(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev2 = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=200, remmsg="Verified", flags=[ResultFlag.HASH_MATCH], usrmod="testuser")
    assert new_rev2 == new_rev + 1

def test_finalize_with_old_revnum_fails(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev2 = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=mock_evidence.remcod, remmsg=mock_evidence.remmsg, flags=mock_evidence.flags, usrmod="testuser")
    with pytest.raises(SignatureConcurrencyError):
        signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev, expected_participant_verlock=ag["part"].verlock, actor=test_actor)

def test_finalize_desde_cargada_fails(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    with pytest.raises(SignatureStateError):
        signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev, expected_participant_verlock=ag["part"].verlock, actor=test_actor)

def test_finalize_sin_verfin_fails(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    # bypass verfin mapping during mark_uploaded
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=200, remmsg="Verified", flags=[ResultFlag.HASH_MATCH], usrmod="testuser")
    firma.verfin = None
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_finalize_sin_hasfin_fails(db: Session, setup_agregado, test_actor):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=200, remmsg="Verified", flags=[ResultFlag.HASH_MATCH], usrmod="testuser")
    firma.hasfin = None
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_fir_comp_registered_once(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=mock_evidence.remcod, remmsg=mock_evidence.remmsg, flags=mock_evidence.flags, usrmod="testuser")
    
    db.execute(sa.text("DELETE FROM audifir"))
    db.commit()
    
    signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev, expected_participant_verlock=ag["part"].verlock, actor=test_actor)
    db.commit()
    
    events = db.scalars(select(Audifir).where(Audifir.evento == "FIR_COMP")).all()
    assert len(events) == 1


# --- Idempotency & Inconsistent Evidence Tests ---
def test_finalize_idempotency_old_revnum(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev2 = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=mock_evidence.remcod, remmsg=mock_evidence.remmsg, flags=mock_evidence.flags, usrmod="testuser")
    
    # First finalize
    signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev2, expected_participant_verlock=ag["part"].verlock, actor=test_actor)
    db.commit()
    
    fecmod_before = firma.fecmod
    revnum_before = firma.revnum
    part_verlock_before = ag["part"].verlock
    
    # Second finalize using old revnum (new_rev2, which was used before completion)
    signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev2, expected_participant_verlock=part_verlock_before, actor=test_actor)
    db.commit()
    
    assert firma.revnum == revnum_before
    assert firma.fecmod == fecmod_before
    assert ag["part"].verlock == part_verlock_before

def test_evidence_inconsistent_fails(db: Session, setup_agregado, test_actor, mock_evidence):
    ag = setup_agregado
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=ag["part"].parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=[{"pagina":1, "posx":10, "posy":20, "ancho":150, "alto":50, "orden":1, "camid":ag["camp"].camid}],
        verori="1.0",
        hasori="a"*64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.flush()
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b"*64, "testuser")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "testuser")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "testuser")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "testuser")
    new_rev2 = signature_service.persist_verification_evidence(db, firma.firid, expected_revnum=new_rev, remcod=mock_evidence.remcod, remmsg=mock_evidence.remmsg, flags=mock_evidence.flags, usrmod="testuser")
    
    signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=new_rev2, expected_participant_verlock=ag["part"].verlock, actor=test_actor)
    db.commit()
    
    # Intentionally corrupt/invalidate result evidence in db
    firma.result = {"schema_ver": 1, "fase": "VERIFICACION", "verchk": False, "haschk": False, "flags": []}
    db.commit()
    
    with pytest.raises(SignatureIntegrityError):
        signature_service.finalize_verified_signature(db, firma.firid, expected_revnum=firma.revnum, expected_participant_verlock=ag["part"].verlock, actor=test_actor)
