import pytest
import threading
import time
import sqlalchemy as sa
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker, Session
from app.core.database import SessionLocal
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
    SignatureActiveAttemptError,
    SignatureConcurrencyError,
    SignatureNotAllowedError,
    SignatureStateError,
)
from app.services.participant_service import participant_service

from app.core.config import settings
DATABASE_URL = settings.DATABASE_URL

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
def setup_agregado_concurrency():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    # Clean tables
    db.execute(text("DELETE FROM firpos"))
    db.execute(text("DELETE FROM docfirma"))
    db.execute(text("DELETE FROM audifir"))
    db.execute(text("DELETE FROM docpart"))
    db.execute(text("DELETE FROM docpaso"))
    db.execute(text("DELETE FROM docfir"))
    db.execute(text("DELETE FROM tplcamp"))
    db.execute(text("DELETE FROM plantill"))
    db.execute(text("DELETE FROM flupaso"))
    db.execute(text("DELETE FROM flujodoc"))
    db.execute(text("DELETE FROM plantill"))
    db.commit()

    from app.schemas.flujo import FlujoCreate, PasoCreate
    from app.services.flow_service import flow_service
    from app.services.step_service import step_service
    from app.crud.crud_docfir import create_documento

    # Create plantill
    plantill = Plantill(tplcod="TPL-CONC", tplnom="Plantilla Conc", tplver=1, numpag=1, usrcre="admin", estado="ACTIVA")
    db.add(plantill)
    db.flush()

    camp = TplCamp(tplid=plantill.tplid, camcod="C1", camnom="Firma1", camtip="FIRMA", pagina=1, posx=0.1, posy=0.1, ancho=0.2, alto=0.05, orden=1, usrcre="admin")
    db.add(camp)
    db.flush()

    f_obj = FlujoCreate(flucod="F_CONC", flunom="Flujo Concurrency", usrcre="admin")
    flujo = flow_service.create_flow(db, f_obj)
    
    p1 = PasoCreate(pascod="P1", pasnom="Paso 1", pastip="FIRMAR", orden=1, rolreq="Firmante", plazo=60)
    p2 = PasoCreate(pascod="P2", pasnom="Paso 2", pastip="FIRMAR", orden=2, rolreq="Aprobador", plazo=60)
    
    flow_service.add_step(db, flujo.fluid, p1, "admin")
    flow_service.add_step(db, flujo.fluid, p2, "admin")
    flow_service.activate_flow(db, flujo.fluid, "admin")

    doc = create_documento(db, nodid="node-conc-1", docnom="Conc.pdf", tamano=1024, verini="1.0", hasori="a" * 64, usrcre="admin")
    doc.fluid = flujo.fluid
    doc.tplid = plantill.tplid
    doc.estado = EstadoDoc.EN_CURSO.value
    db.commit()

    pasos = step_service.instantiate_document_steps(db, doc.docid, "admin")
    db.flush()

    part1 = DocPart(
        dpasid=pasos[0].dpasid,
        usrid="testuser",
        nomcom="Usuario Test",
        correo="testuser@empresa.local",
        rolpro="Firmante",
        obliga=True,
        estado="DISPONIBLE",
        usrcre="admin"
    )
    part2 = DocPart(
        dpasid=pasos[1].dpasid,
        usrid="approver",
        nomcom="Aprobador Test",
        correo="approver@empresa.local",
        rolpro="Aprobador",
        obliga=True,
        estado="DISPONIBLE",
        usrcre="admin"
    )
    db.add(part1)
    db.add(part2)
    doc_id = doc.docid
    camp_id = camp.camid
    db.commit()

    parid1 = part1.parid
    parid2 = part2.parid
    db.close()
    return {"docid": doc_id, "parid1": parid1, "parid2": parid2, "camid": camp_id}


def test_concurrent_reservation(setup_agregado_concurrency, test_actor):
    data = setup_agregado_concurrency
    barrier = threading.Barrier(2)
    results = []

    def worker():
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        barrier.wait()
        try:
            firma = signature_service.reserve_signature_attempt(
                db=db,
                parid=data["parid1"],
                tipfir=TipoFirma.INTERNA.value,
                positions=[{"pagina": 1, "posx": 10, "posy": 20, "ancho": 150, "alto": 50, "orden": 1, "camid": data["camid"]}],
                verori="1.0",
                hasori="a" * 64,
                actor=test_actor,
                expected_participant_verlock=1
            )
            db.commit()
            results.append(("SUCCESS", firma.firid))
        except Exception as e:
            db.rollback()
            results.append(("ERROR", type(e).__name__))
        finally:
            db.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    successes = [r for r in results if r[0] == "SUCCESS"]
    errors = [r for r in results if r[0] == "ERROR"]
    
    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0][1] in ("SignatureActiveAttemptError", "IntegrityError")


def test_concurrent_finalization(setup_agregado_concurrency, test_actor, mock_evidence):
    data = setup_agregado_concurrency
    
    # 1. Setup attempt to VERIFICANDO state
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=data["parid1"],
        tipfir=TipoFirma.INTERNA.value,
        positions=[],
        verori="1.0",
        hasori="a" * 64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.commit()
    
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b" * 64, "admin")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "admin")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "admin")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "admin")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, new_rev, remcod=200, remmsg="Verified", flags=[ResultFlag.HASH_MATCH], usrmod="admin")
    db.commit()
    
    firid = firma.firid
    expected_revnum = new_rev
    db.close()

    barrier = threading.Barrier(2)
    results = []

    def worker():
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        local_db = Session()
        
        # Get verlock
        part = local_db.scalars(select(DocPart).where(DocPart.parid == data["parid1"])).first()
        barrier.wait()
        try:
            signature_service.finalize_verified_signature(
                db=local_db,
                firid=firid,
                expected_revnum=expected_revnum,
                expected_participant_verlock=part.verlock,
                actor=test_actor
            )
            local_db.commit()
            results.append("SUCCESS")
        except Exception as e:
            local_db.rollback()
            results.append(f"ERROR: {type(e).__name__}")
        finally:
            local_db.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # One succeeds, and the other must either succeed (idempotent result) or fail with concurrency check.
    assert "SUCCESS" in results


def test_lock_ordering_step_participants(setup_agregado_concurrency, test_actor, mock_evidence):
    data = setup_agregado_concurrency
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Pre-reserve and get attempt ready
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=data["parid1"],
        tipfir=TipoFirma.INTERNA.value,
        positions=[],
        verori="1.0",
        hasori="a" * 64,
        actor=test_actor,
        expected_participant_verlock=1
    )
    db.commit()
    
    new_rev = signature_service.mark_generated(db, firma.firid, 1, "b" * 64, "admin")
    new_rev = signature_service.mark_upload_started(db, firma.firid, new_rev, "admin")
    pub_result = ResultContract(schema_ver=1, fase=ResultFase.PUBLICACION, remcod=201, remmsg="Created", recint=0, verchk=False, haschk=False, flags=[ResultFlag.ALFRESCO_OK])
    new_rev = signature_service.mark_uploaded(db, firma.firid, new_rev, "1.1", pub_result, "admin")
    new_rev = signature_service.mark_verification_started(db, firma.firid, new_rev, "admin")
    new_rev = signature_service.persist_verification_evidence(db, firma.firid, new_rev, remcod=200, remmsg="Verified", flags=[ResultFlag.HASH_MATCH], usrmod="admin")
    db.commit()

    # Spy/Record SQLAlchemy execute/select queries
    from sqlalchemy import event
    lock_records = []
    
    @event.listens_for(db.bind, "before_cursor_execute")
    def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            lock_records.append(statement)

    # Finalize
    signature_service.finalize_verified_signature(
        db=db,
        firid=firma.firid,
        expected_revnum=new_rev,
        expected_participant_verlock=2,
        actor=test_actor
    )
    db.commit()

    # Verify that we locked tables
    assert len(lock_records) >= 4
    
    # Confirm lock statements in sequence
    # Sequence of target objects:
    # 1. docpaso
    # 2. docpart (target)
    # 3. docfir
    # 4. docfirma
    paso_locked = any("docpaso" in stmt.lower() for stmt in lock_records[:2])
    part_locked = any("docpart" in stmt.lower() for stmt in lock_records[:3])
    docfir_locked = any("docfir" in stmt.lower() for stmt in lock_records[:4])
    docfirma_locked = any("docfirma" in stmt.lower() for stmt in lock_records[:5])

    assert paso_locked
    assert part_locked
    assert docfir_locked
    assert docfirma_locked
    db.close()


@pytest.fixture
def multi_step_context():
    engine = create_engine(DATABASE_URL)
    SessionFactory = sessionmaker(bind=engine)
    db = SessionFactory()
    try:
        db.execute(text("DELETE FROM firpos"))
        db.execute(text("DELETE FROM docfirma"))
        db.execute(text("DELETE FROM audifir"))
        db.execute(text("DELETE FROM docpart"))
        db.execute(text("DELETE FROM docpaso"))
        db.execute(text("DELETE FROM docfir"))
        db.execute(text("DELETE FROM tplcamp"))
        db.execute(text("DELETE FROM plantill"))
        db.execute(text("DELETE FROM flupaso"))
        db.execute(text("DELETE FROM flujodoc"))
        db.commit()

        from app.schemas.flujo import FlujoCreate, PasoCreate
        from app.services.flow_service import flow_service
        from app.services.step_service import step_service
        from app.crud.crud_docfir import create_documento

        plantill = Plantill(
            tplcod="TPL-MULTI",
            tplnom="Plantilla Multi",
            tplver=1,
            numpag=1,
            usrcre="admin",
            estado="ACTIVA",
        )
        db.add(plantill)
        db.flush()

        camp = TplCamp(
            tplid=plantill.tplid,
            camcod="C1",
            camnom="Firma1",
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

        flujo = flow_service.create_flow(
            db,
            FlujoCreate(flucod="F_MULTI", flunom="Flujo Multi", usrcre="admin"),
        )
        step_cfg = {"part_estrategia": "UNO", "part_modo": "PARALELO"}
        flow_service.add_step(
            db,
            flujo.fluid,
            PasoCreate(
                pascod="P1",
                pasnom="Paso 1",
                pastip="FIRMAR",
                orden=1,
                rolreq="Firmante",
                plazo=60,
                config=step_cfg,
            ),
            "admin",
        )
        flow_service.add_step(
            db,
            flujo.fluid,
            PasoCreate(
                pascod="P2",
                pasnom="Paso 2",
                pastip="FIRMAR",
                orden=2,
                rolreq="Aprobador",
                plazo=60,
                config=step_cfg,
            ),
            "admin",
        )
        flow_service.activate_flow(db, flujo.fluid, "admin")

        doc = create_documento(
            db,
            nodid="node-multi-1",
            docnom="Multi.pdf",
            tamano=1024,
            verini="1.0",
            hasori="a" * 64,
            usrcre="admin",
        )
        doc.fluid = flujo.fluid
        doc.tplid = plantill.tplid
        doc.estado = EstadoDoc.EN_CURSO.value
        db.commit()

        pasos = step_service.instantiate_document_steps(db, doc.docid, "admin")
        db.flush()

        part1 = DocPart(
            dpasid=pasos[0].dpasid,
            usrid="testuser",
            nomcom="Usuario Test",
            correo="testuser@empresa.local",
            rolpro="Firmante",
            obliga=True,
            estado="DISPONIBLE",
            usrcre="admin",
        )
        part2 = DocPart(
            dpasid=pasos[0].dpasid,
            usrid="backup1",
            nomcom="Backup Uno",
            correo="backup1@empresa.local",
            rolpro="Firmante",
            obliga=True,
            estado="DISPONIBLE",
            orden=2,
            usrcre="admin",
        )
        part3 = DocPart(
            dpasid=pasos[1].dpasid,
            usrid="approver1",
            nomcom="Aprobador Uno",
            correo="approver1@empresa.local",
            rolpro="Aprobador",
            obliga=True,
            estado="PENDIENTE",
            orden=1,
            usrcre="admin",
        )
        part4 = DocPart(
            dpasid=pasos[1].dpasid,
            usrid="approver2",
            nomcom="Aprobador Dos",
            correo="approver2@empresa.local",
            rolpro="Aprobador",
            obliga=True,
            estado="PENDIENTE",
            orden=2,
            usrcre="admin",
        )
        db.add_all([part1, part2, part3, part4])
        db.commit()

        yield {
            "engine": engine,
            "Session": SessionFactory,
            "docid": doc.docid,
            "camid": camp.camid,
            "step1_dpasid": pasos[0].dpasid,
            "step2_dpasid": pasos[1].dpasid,
            "part1": part1.parid,
            "part2": part2.parid,
            "part3": part3.parid,
            "part4": part4.parid,
        }
    finally:
        try:
            db.rollback()
        finally:
            db.close()

        cleanup = SessionFactory()
        try:
            cleanup.execute(text("DELETE FROM firpos"))
            cleanup.execute(text("DELETE FROM docfirma"))
            cleanup.execute(text("DELETE FROM audifir"))
            cleanup.execute(text("DELETE FROM docpart"))
            cleanup.execute(text("DELETE FROM docpaso"))
            cleanup.execute(text("DELETE FROM docfir"))
            cleanup.execute(text("DELETE FROM tplcamp"))
            cleanup.execute(text("DELETE FROM plantill"))
            cleanup.execute(text("DELETE FROM flupaso"))
            cleanup.execute(text("DELETE FROM flujodoc"))
            cleanup.commit()
        finally:
            cleanup.close()
            engine.dispose()


def _prepare_signature_to_verifying(
    db: Session,
    parid: int,
    camid: int,
    actor: IdentitySnapshot,
    expected_participant_verlock: int = 1,
):
    positions = [
        {"pagina": 1, "posx": 10, "posy": 20, "ancho": 150, "alto": 50, "orden": 1, "camid": camid}
    ]
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=positions,
        verori="1.0",
        hasori="a" * 64,
        actor=actor,
        expected_participant_verlock=expected_participant_verlock,
    )
    db.commit()

    new_rev = signature_service.mark_generated(db, firma.firid, expected_revnum=1, hasfin="b" * 64, usrmod=actor.usrid)
    new_rev = signature_service.mark_upload_started(db, firma.firid, expected_revnum=new_rev, usrmod=actor.usrid)
    pub_result = ResultContract(
        schema_ver=1,
        fase=ResultFase.PUBLICACION,
        remcod=201,
        remmsg="Created",
        recint=0,
        verchk=False,
        haschk=False,
        flags=[ResultFlag.ALFRESCO_OK],
    )
    new_rev = signature_service.mark_uploaded(
        db,
        firma.firid,
        expected_revnum=new_rev,
        verfin="1.1",
        result_data=pub_result,
        usrmod=actor.usrid,
    )
    new_rev = signature_service.mark_verification_started(db, firma.firid, expected_revnum=new_rev, usrmod=actor.usrid)
    new_rev = signature_service.persist_verification_evidence(
        db,
        firma.firid,
        expected_revnum=new_rev,
        remcod=200,
        remmsg="Verified",
        flags=[ResultFlag.HASH_MATCH],
        usrmod=actor.usrid,
    )
    db.commit()

    part = db.scalars(select(DocPart).where(DocPart.parid == parid)).first()
    return firma.firid, new_rev, part.verlock


def _seed_cancelled_attempt(
    db: Session,
    parid: int,
    camid: int,
    actor: IdentitySnapshot,
    expected_participant_verlock: int = 1,
):
    positions = [
        {"pagina": 1, "posx": 10, "posy": 20, "ancho": 150, "alto": 50, "orden": 1, "camid": camid}
    ]
    firma = signature_service.reserve_signature_attempt(
        db=db,
        parid=parid,
        tipfir=TipoFirma.INTERNA.value,
        positions=positions,
        verori="1.0",
        hasori="a" * 64,
        actor=actor,
        expected_participant_verlock=expected_participant_verlock,
    )
    db.commit()

    new_rev = signature_service.mark_generated(db, firma.firid, expected_revnum=1, hasfin="b" * 64, usrmod=actor.usrid)
    db.commit()
    signature_service.cancel_signature_attempt(
        db,
        firma.firid,
        expected_revnum=new_rev,
        motivo="Seed cancelled attempt",
        actor=actor,
    )
    db.commit()

    part = db.scalars(select(DocPart).where(DocPart.parid == parid)).first()
    return firma.firid, part.verlock


def test_concurrent_reservation_same_document(setup_agregado_concurrency, test_actor):
    data = setup_agregado_concurrency
    barrier = threading.Barrier(2)
    results = []

    def worker():
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            barrier.wait(timeout=5)
            firma = signature_service.reserve_signature_attempt(
                db=db,
                parid=data["parid1"],
                tipfir=TipoFirma.INTERNA.value,
                positions=[{"pagina": 1, "posx": 10, "posy": 20, "ancho": 150, "alto": 50, "orden": 1, "camid": data["camid"]}],
                verori="1.0",
                hasori="a" * 64,
                actor=test_actor,
                expected_participant_verlock=1,
            )
            db.commit()
            results.append(("SUCCESS", firma.firid))
        except Exception as exc:
            db.rollback()
            results.append(("ERROR", type(exc).__name__))
        finally:
            db.close()
            engine.dispose()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert len(results) == 2
    successes = [item for item in results if item[0] == "SUCCESS"]
    errors = [item for item in results if item[0] == "ERROR"]
    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0][1] in ("SignatureActiveAttemptError", "IntegrityError")

    verification = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=verification)
    db = Session()
    try:
        assert db.execute(text("SELECT COUNT(*) FROM docfirma WHERE docid = :docid"), {"docid": data["docid"]}).scalar_one() == 1
        assert db.execute(text("SELECT COUNT(*) FROM audifir WHERE enttip = 'FIRMA' AND evento = 'FIR_INIC'")).scalar_one() == 1
    finally:
        db.close()
        verification.dispose()


def test_concurrent_document_sequence(multi_step_context, test_actor):
    data = multi_step_context
    engine = data["engine"]
    Session = data["Session"]
    db = Session()
    try:
        _seed_cancelled_attempt(db, data["part1"], data["camid"], test_actor, expected_participant_verlock=1)
        db.close()
        db = Session()
        backup_actor = IdentitySnapshot(
            usrid="backup1",
            nomcom="Backup Uno",
            correo="backup1@empresa.local",
            rolpro="Firmante",
        )

        barrier = threading.Barrier(2)
        results = []

        def worker(delay: float = 0.0):
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                if delay:
                    time.sleep(delay)
                firma = signature_service.reserve_signature_attempt(
                    db=local_db,
                    parid=data["part2"],
                    tipfir=TipoFirma.INTERNA.value,
                    positions=[{"pagina": 1, "posx": 10, "posy": 20, "ancho": 150, "alto": 50, "orden": 1, "camid": data["camid"]}],
                    verori="1.0",
                    hasori="a" * 64,
                    actor=backup_actor,
                    expected_participant_verlock=1,
                )
                local_db.commit()
                results.append(("SUCCESS", firma.firid))
            except Exception as exc:
                local_db.rollback()
                results.append(("ERROR", type(exc).__name__))
            finally:
                local_db.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker, args=(0.2,))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert len(results) == 2

        success = next(item for item in results if item[0] == "SUCCESS")
        assert success[0] == "SUCCESS"

        verification = Session()
        try:
            firma = verification.scalars(
                select(DocFirma).where(DocFirma.parid == data["part2"])
            ).first()
            assert firma is not None
            assert firma.secuen == 2
            assert firma.intnum == 1
        finally:
            verification.close()
    finally:
        db.close()


def test_concurrent_participant_attempt_number(multi_step_context, test_actor):
    data = multi_step_context
    Session = data["Session"]
    db = Session()
    try:
        _, seed_verlock = _seed_cancelled_attempt(db, data["part1"], data["camid"], test_actor, expected_participant_verlock=1)
        db.close()
        db = Session()

        barrier = threading.Barrier(2)
        results = []

        def worker(delay: float = 0.0):
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                if delay:
                    time.sleep(delay)
                firma = signature_service.reserve_signature_attempt(
                    db=local_db,
                    parid=data["part1"],
                    tipfir=TipoFirma.INTERNA.value,
                    positions=[{"pagina": 1, "posx": 10, "posy": 20, "ancho": 150, "alto": 50, "orden": 1, "camid": data["camid"]}],
                    verori="1.0",
                    hasori="a" * 64,
                    actor=test_actor,
                    expected_participant_verlock=seed_verlock,
                )
                local_db.commit()
                results.append(("SUCCESS", firma.firid))
            except Exception as exc:
                local_db.rollback()
                results.append(("ERROR", type(exc).__name__))
            finally:
                local_db.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker, args=(0.2,))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert len(results) == 2

        success = next(item for item in results if item[0] == "SUCCESS")
        assert success[0] == "SUCCESS"

        verification = Session()
        try:
            firma = verification.scalars(
                select(DocFirma).where(DocFirma.parid == data["part1"]).order_by(DocFirma.firid.desc())
            ).first()
            assert firma is not None
            assert firma.secuen == 2
            assert firma.intnum == 2
            audits = verification.scalars(select(Audifir).where(Audifir.evento == "FIR_INIC", Audifir.enttip == "FIRMA")).all()
            assert len(audits) >= 2
        finally:
            verification.close()
    finally:
        db.close()


def test_concurrent_finalize_same_attempt(setup_agregado_concurrency, test_actor):
    data = setup_agregado_concurrency
    setup_engine = create_engine(DATABASE_URL)
    setup_session_factory = sessionmaker(bind=setup_engine)
    setup_db = setup_session_factory()
    try:
        firma, revnum, part_verlock = _prepare_signature_to_verifying(
            setup_db,
            data["parid1"],
            data["camid"],
            test_actor,
            expected_participant_verlock=1,
        )
    finally:
        setup_db.close()
        setup_engine.dispose()

    engine_left = create_engine(DATABASE_URL)
    engine_right = create_engine(DATABASE_URL)
    class RefreshingSession(Session):
        pass

    def _refresh_existing_rows(orm_execute_state):
        if orm_execute_state.is_select:
            orm_execute_state.update_execution_options(populate_existing=True)

    sa.event.listen(RefreshingSession, "do_orm_execute", _refresh_existing_rows)
    SessionLeft = sessionmaker(bind=engine_left, class_=RefreshingSession, autoflush=False, expire_on_commit=False)
    SessionRight = sessionmaker(bind=engine_right, class_=RefreshingSession, autoflush=False, expire_on_commit=False)
    barrier = threading.Barrier(2)
    timeout_seconds = 15
    results = {}
    results_lock = threading.Lock()

    def worker(name: str, SessionFactory):
        local_db = SessionFactory()
        trans = local_db.begin()
        started = time.monotonic()
        outcome = {"status": "ERROR", "exc": None, "elapsed": None}
        try:
            local_db.execute(text("SELECT 1"))
            barrier.wait(timeout=timeout_seconds)
            signature_service.finalize_verified_signature(
                db=local_db,
                firid=firma,
                expected_revnum=revnum,
                expected_participant_verlock=part_verlock,
                actor=test_actor,
            )
            trans.commit()
            outcome["status"] = "SUCCESS"
        except Exception as exc:
            if trans.is_active:
                trans.rollback()
            outcome["exc"] = type(exc).__name__
        finally:
            outcome["elapsed"] = time.monotonic() - started
            local_db.close()
            with results_lock:
                results[name] = outcome

    t1 = threading.Thread(target=worker, args=("left", SessionLeft))
    t2 = threading.Thread(target=worker, args=("right", SessionRight))
    t1.start()
    t2.start()
    t1.join(timeout=timeout_seconds)
    t2.join(timeout=timeout_seconds)

    verification = setup_session_factory()
    cleanup = None
    try:
        assert not t1.is_alive()
        assert not t2.is_alive()
        assert len(results) == 2
        assert all(item["elapsed"] is not None and item["elapsed"] <= timeout_seconds for item in results.values())
        assert all(
            item["status"] == "SUCCESS" or item["exc"] == "SignatureConcurrencyError"
            for item in results.values()
        )
        assert any(item["status"] == "SUCCESS" for item in results.values())

        stored = verification.scalars(select(DocFirma).where(DocFirma.firid == firma)).first()
        assert stored is not None
        assert stored.estado == EstadoDocFirma.COMPLETADA.value
        assert stored.revnum == revnum + 1

        participant = verification.scalars(select(DocPart).where(DocPart.parid == data["parid1"])).first()
        assert participant is not None
        assert participant.estado == "COMPLETADO"
        assert participant.verlock == part_verlock + 1

        fir_comp_audits = verification.scalars(
            select(Audifir).where(Audifir.evento == "FIR_COMP", Audifir.entid == firma)
        ).all()
        paso_comp_audits = verification.scalars(
            select(Audifir).where(Audifir.evento == "PASO_COMP", Audifir.docid == stored.docid)
        ).all()
        paso_disp_audits = verification.scalars(
            select(Audifir).where(Audifir.evento == "PASO_DISP", Audifir.docid == stored.docid)
        ).all()

        assert len(fir_comp_audits) == 1
        assert len(paso_comp_audits) == 1
        assert len(paso_disp_audits) == 1
    finally:
        try:
            cleanup = setup_session_factory()
            cleanup.execute(text("DELETE FROM firpos"))
            cleanup.execute(text("DELETE FROM docfirma"))
            cleanup.execute(text("DELETE FROM audifir"))
            cleanup.execute(text("DELETE FROM docpart"))
            cleanup.execute(text("DELETE FROM docpaso"))
            cleanup.execute(text("DELETE FROM docfir"))
            cleanup.execute(text("DELETE FROM tplcamp"))
            cleanup.execute(text("DELETE FROM plantill"))
            cleanup.execute(text("DELETE FROM flupaso"))
            cleanup.execute(text("DELETE FROM flujodoc"))
            cleanup.commit()
        finally:
            if cleanup is not None:
                cleanup.close()
            verification.close()
            sa.event.remove(RefreshingSession, "do_orm_execute", _refresh_existing_rows)
            engine_left.dispose()
            engine_right.dispose()


def test_finalize_vs_cancel(setup_agregado_concurrency, test_actor):
    data = setup_agregado_concurrency
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        firma, revnum, part_verlock = _prepare_signature_to_verifying(db, data["parid1"], data["camid"], test_actor, expected_participant_verlock=1)
        db.close()

        barrier = threading.Barrier(2)
        results = []

        def finalize_worker():
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                signature_service.finalize_verified_signature(
                    db=local_db,
                    firid=firma,
                    expected_revnum=revnum,
                    expected_participant_verlock=part_verlock,
                    actor=test_actor,
                )
                local_db.commit()
                results.append(("SUCCESS", "finalize"))
            except Exception as exc:
                local_db.rollback()
                results.append(("ERROR", type(exc).__name__))
            finally:
                local_db.close()

        def cancel_worker():
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                signature_service.cancel_signature_attempt(
                    db=local_db,
                    firid=firma,
                    expected_revnum=revnum,
                    motivo="cancel race",
                    actor=test_actor,
                )
                local_db.commit()
                results.append(("SUCCESS", "cancel"))
            except Exception as exc:
                local_db.rollback()
                results.append(("ERROR", type(exc).__name__))
            finally:
                local_db.close()

        t1 = threading.Thread(target=finalize_worker)
        t2 = threading.Thread(target=cancel_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert len(results) == 2
        assert any(item[0] == "SUCCESS" for item in results)
        assert any(item[0] == "ERROR" for item in results)

        verification = Session()
        try:
            stored = verification.scalars(select(DocFirma).where(DocFirma.firid == firma)).first()
            assert stored is not None
            assert stored.estado in (EstadoDocFirma.COMPLETADA.value, EstadoDocFirma.CANCELADA.value)
            audits = verification.scalars(select(Audifir).where(Audifir.enttip == "FIRMA", Audifir.entid == firma)).all()
            assert audits
        finally:
            verification.close()
    finally:
        try:
            db.close()
        finally:
            engine.dispose()


def test_finalize_vs_reject(multi_step_context, test_actor):
    data = multi_step_context
    Session = data["Session"]
    db = Session()
    try:
        firma, revnum, part_verlock = _prepare_signature_to_verifying(db, data["part1"], data["camid"], test_actor, expected_participant_verlock=1)
        db.close()

        barrier = threading.Barrier(2)
        results = []

        def finalize_worker():
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                signature_service.finalize_verified_signature(
                    db=local_db,
                    firid=firma,
                    expected_revnum=revnum,
                    expected_participant_verlock=part_verlock,
                    actor=test_actor,
                )
                local_db.commit()
                results.append(("SUCCESS", "finalize"))
            except Exception as exc:
                local_db.rollback()
                results.append(("ERROR", type(exc).__name__))
            finally:
                local_db.close()

        def reject_worker():
            local_db = Session()
            try:
                part = local_db.scalars(select(DocPart).where(DocPart.parid == data["part1"])).first()
                barrier.wait(timeout=5)
                time.sleep(0.2)
                participant_service.reject_participation(
                    db=local_db,
                    parid=part.parid,
                    actor_id=test_actor.usrid,
                    expected_verlock=part.verlock,
                    motivo="reject race",
                )
                local_db.commit()
                results.append(("SUCCESS", "reject"))
            except Exception as exc:
                local_db.rollback()
                results.append(("ERROR", type(exc).__name__))
            finally:
                local_db.close()

        t1 = threading.Thread(target=finalize_worker)
        t2 = threading.Thread(target=reject_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert len(results) == 2
        assert any(item[0] == "SUCCESS" for item in results)
        assert any(item[0] == "ERROR" for item in results)

        verification = Session()
        try:
            stored = verification.scalars(select(DocPart).where(DocPart.parid == data["part1"])).first()
            assert stored is not None
            assert stored.estado in ("COMPLETADO", "RECHAZADO")
            audits = verification.scalars(select(Audifir).where(Audifir.docid == data["docid"])).all()
            assert audits
        finally:
            verification.close()
    finally:
        db.close()


def test_finalize_vs_expire(multi_step_context, test_actor):
    data = multi_step_context
    Session = data["Session"]
    db = Session()
    try:
        firma, revnum, part_verlock = _prepare_signature_to_verifying(db, data["part1"], data["camid"], test_actor, expected_participant_verlock=1)
        db.close()

        barrier = threading.Barrier(2)
        results = []

        def finalize_worker():
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                signature_service.finalize_verified_signature(
                    db=local_db,
                    firid=firma,
                    expected_revnum=revnum,
                    expected_participant_verlock=part_verlock,
                    actor=test_actor,
                )
                local_db.commit()
                results.append(("SUCCESS", "finalize"))
            except Exception as exc:
                local_db.rollback()
                results.append(("ERROR", type(exc).__name__))
            finally:
                local_db.close()

        def expire_worker():
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                participant_service.expire_step_participants(
                    db=local_db,
                    dpasid=data["step1_dpasid"],
                    actor_id="sistema",
                )
                local_db.commit()
                results.append(("SUCCESS", "expire"))
            except Exception as exc:
                local_db.rollback()
                results.append(("ERROR", type(exc).__name__))
            finally:
                local_db.close()

        t1 = threading.Thread(target=finalize_worker)
        t2 = threading.Thread(target=expire_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert len(results) == 2
        assert any(item[0] == "SUCCESS" for item in results)

        verification = Session()
        try:
            stored = verification.scalars(select(DocPart).where(DocPart.parid == data["part1"])).first()
            assert stored is not None
            assert stored.estado in ("COMPLETADO", "VENCIDO")
            audits = verification.scalars(select(Audifir).where(Audifir.docid == data["docid"])).all()
            assert audits
        finally:
            verification.close()
    finally:
        db.close()


def test_full_lock_order(multi_step_context, test_actor):
    data = multi_step_context
    Session = data["Session"]
    engine = data["engine"]
    db = Session()
    try:
        firma, revnum, part_verlock = _prepare_signature_to_verifying(db, data["part1"], data["camid"], test_actor, expected_participant_verlock=1)
        db.close()

        lock_records = []
        from sqlalchemy import event

        @event.listens_for(engine, "before_cursor_execute")
        def capture_for_update(conn, cursor, statement, parameters, context, executemany):
            if "FOR UPDATE" in statement.upper():
                lock_records.append(statement.lower())

        local_db = Session()
        try:
            signature_service.finalize_verified_signature(
                db=local_db,
                firid=firma,
                expected_revnum=revnum,
                expected_participant_verlock=part_verlock,
                actor=test_actor,
            )
            local_db.commit()
        finally:
            local_db.close()
            try:
                event.remove(engine, "before_cursor_execute", capture_for_update)
            except Exception:
                pass

        assert len(lock_records) >= 7
        assert "from docpaso" in lock_records[0]
        assert "from docpart" in lock_records[1]
        assert "from docfir" in lock_records[2]
        assert "from docfirma" in lock_records[3]
        first_remaining_part = next(i for i, stmt in enumerate(lock_records[4:], start=4) if "from docpart" in stmt)
        next_step_idx = next(i for i, stmt in enumerate(lock_records[4:], start=4) if "from docpaso" in stmt)
        next_step_part = next(i for i, stmt in enumerate(lock_records[next_step_idx + 1 :], start=next_step_idx + 1) if "from docpart" in stmt)
        assert first_remaining_part < next_step_idx < next_step_part

        verification = Session()
        try:
            audits = verification.scalars(select(Audifir).where(Audifir.docid == data["docid"])).all()
            assert any(a.evento == "FIR_COMP" for a in audits)
            assert any(a.evento == "PAS_DISP" for a in audits)
        finally:
            verification.close()
    finally:
        db.close()


def test_no_deadlock_under_contention(multi_step_context, test_actor):
    data = multi_step_context
    Session = data["Session"]
    db = Session()
    try:
        firma, revnum, part_verlock = _prepare_signature_to_verifying(db, data["part1"], data["camid"], test_actor, expected_participant_verlock=1)
        db.close()

        barrier = threading.Barrier(4)
        results = []

        def finalize_worker(tag: str):
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                signature_service.finalize_verified_signature(
                    db=local_db,
                    firid=firma,
                    expected_revnum=revnum,
                    expected_participant_verlock=part_verlock,
                    actor=test_actor,
                )
                local_db.commit()
                results.append((tag, "SUCCESS"))
            except Exception as exc:
                local_db.rollback()
                results.append((tag, type(exc).__name__))
            finally:
                local_db.close()

        def reject_worker():
            local_db = Session()
            try:
                part = local_db.scalars(select(DocPart).where(DocPart.parid == data["part1"])).first()
                barrier.wait(timeout=5)
                participant_service.reject_participation(
                    db=local_db,
                    parid=part.parid,
                    actor_id=test_actor.usrid,
                    expected_verlock=part.verlock,
                    motivo="reject contention",
                )
                local_db.commit()
                results.append(("reject", "SUCCESS"))
            except Exception as exc:
                local_db.rollback()
                results.append(("reject", type(exc).__name__))
            finally:
                local_db.close()

        def expire_worker():
            local_db = Session()
            try:
                barrier.wait(timeout=5)
                participant_service.expire_step_participants(
                    db=local_db,
                    dpasid=data["step1_dpasid"],
                    actor_id="sistema",
                )
                local_db.commit()
                results.append(("expire", "SUCCESS"))
            except Exception as exc:
                local_db.rollback()
                results.append(("expire", type(exc).__name__))
            finally:
                local_db.close()

        threads = [
            threading.Thread(target=finalize_worker, args=("finalize-1",)),
            threading.Thread(target=finalize_worker, args=("finalize-2",)),
            threading.Thread(target=reject_worker),
            threading.Thread(target=expire_worker),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        assert all(not thread.is_alive() for thread in threads)
        assert len(results) == 4
        assert any(tag.startswith("finalize") and status == "SUCCESS" for tag, status in results)

        verification = Session()
        try:
            audits = verification.scalars(select(Audifir).where(Audifir.docid == data["docid"])).all()
            assert audits
            assert any(a.evento in {"FIR_COMP", "PAR_RECH", "PAR_VENC"} for a in audits)
        finally:
            verification.close()
    finally:
        db.close()
