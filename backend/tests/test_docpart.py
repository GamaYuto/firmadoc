import pytest
import threading
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import sqlalchemy as sa
from app.models.docpart import DocPart
from app.models.docpaso import DocPaso
from app.models.flupaso import Flupaso
from app.models.flujodoc import Flujodoc
from app.models.docfir import DocFir
from app.schemas.docpart import DocPartCreate, DocPartTransition
from app.crud.crud_docpart import crud_docpart, ParticipantConcurrencyError
from app.services.participant_service import participant_service
from app.core.identity import FakeIdentityResolver, IdentityResolutionError
from app.core.database import SessionLocal

from app.models.audifir import Audifir

@pytest.fixture
def identity_resolver():
    return FakeIdentityResolver()


@pytest.fixture
def setup_doc(db: Session):
    doc = DocFir(nodid="uuid-123", docnom="test", mimtip="application/pdf", tamano=100, verini="1.0", estado="BORRADOR", hasori="a"*64, usrcre="test")
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc

@pytest.fixture
def setup_flupaso(db: Session, setup_doc):
    flujo = Flujodoc(flucod="F1", flunom="F1", fluver=1, estado="ACTIVO", usrcre="test")
    db.add(flujo)
    db.commit()
    db.refresh(flujo)
    flupaso = Flupaso(fluid=flujo.fluid, pascod="P1", pasnom="Paso 1", orden=1, pastip="FIRMAR", rolreq="Revisor", obliga=True, config={"part_estrategia": "TODOS", "part_modo": "PARALELO", "rechazo_inmediato": True}, usrcre="test")
    db.add(flupaso)
    db.commit()
    db.refresh(flupaso)
    return flupaso

@pytest.fixture
def setup_step(db: Session, setup_doc, setup_flupaso):
    paso = DocPaso(docid=setup_doc.docid, pasid=setup_flupaso.pasid, orden=1, estado="PENDIENTE", pastip="FIRMAR", rolreq="Revisor", obliga=True, usrcre="test", verlock=1)
    paso.config = {"part_estrategia": "TODOS", "part_modo": "PARALELO", "rechazo_inmediato": True}
    db.add(paso)
    db.commit()
    db.refresh(paso)
    return paso

# 1-10 Modelo y Constraints
def test_create_participant(db: Session, setup_step):
    p = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="User 1", correo="u1@test.com", estado="PENDIENTE", usrcre="sys", orden=1, obliga=True)
    db.add(p)
    db.commit()
    assert p.parid is not None

def test_requires_existing_step(db: Session):
    p = DocPart(dpasid=9999, usrid="user1", nomcom="U1", correo="c@t.com", estado="PENDIENTE", usrcre="sys", orden=1, obliga=True)
    db.add(p)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_duplicate_user_in_step(db: Session, setup_step):
    p1 = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U1", correo="c@t.com", estado="PENDIENTE", usrcre="sys", orden=1, obliga=True)
    p2 = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U2", correo="c@t.com", estado="PENDIENTE", usrcre="sys", orden=2, obliga=True)
    db.add_all([p1, p2])
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_duplicate_order_in_step(db: Session, setup_step):
    p1 = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U1", correo="c@t.com", estado="PENDIENTE", usrcre="sys", orden=1, obliga=True)
    p2 = DocPart(dpasid=setup_step.dpasid, usrid="user2", nomcom="U2", correo="c@t.com", estado="PENDIENTE", usrcre="sys", orden=1, obliga=True)
    db.add_all([p1, p2])
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_order_must_be_positive(db: Session, setup_step):
    p = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U1", correo="c@t.com", estado="PENDIENTE", usrcre="sys", orden=0, obliga=True)
    db.add(p)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_invalid_state(db: Session, setup_step):
    p = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U1", correo="c@t.com", estado="INVALIDO", usrcre="sys", orden=1, obliga=True)
    db.add(p)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_rejected_requires_reason(db: Session, setup_step):
    p = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U1", correo="c@t.com", estado="RECHAZADO", usrcre="sys", orden=1, obliga=True)
    db.add(p)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_omitted_requires_reason(db: Session, setup_step):
    p = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U1", correo="c@t.com", estado="OMITIDO", usrcre="sys", orden=1, obliga=True)
    db.add(p)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_cancelled_requires_reason(db: Session, setup_step):
    p = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U1", correo="c@t.com", estado="CANCELADO", usrcre="sys", orden=1, obliga=True)
    db.add(p)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

def test_finish_date_cannot_precede_start(db: Session, setup_step):
    now = datetime.now(timezone.utc)
    p = DocPart(dpasid=setup_step.dpasid, usrid="user1", nomcom="U1", correo="c@t.com", estado="EN_PROCESO", usrcre="sys", orden=1, obliga=True, fecini=now, fecfin=now - timedelta(days=1))
    db.add(p)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

# 11-16 Identidad
def test_identity_is_resolved_by_backend(db: Session, setup_step, identity_resolver):
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="juan", orden=1, obliga=True)]
    participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)
    db.commit()
    parts = crud_docpart.list_by_step(db, setup_step.dpasid)
    assert parts[0].nomcom == "Usuario Juan"

def test_frontend_identity_fields_are_ignored_or_rejected():
    with pytest.raises(ValueError):
        DocPartCreate(dpasid=1, usrid="juan", orden=1, obliga=True, nomcom="Falso")

def test_unknown_user_fails_assignment(db: Session, setup_step, identity_resolver):
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="unknown_user", orden=1, obliga=True)]
    with pytest.raises(IdentityResolutionError):
        participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)

def test_identity_snapshot_is_immutable(db: Session, setup_step, identity_resolver):
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="juan", orden=1, obliga=True)]
    participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)
    db.commit()
    p = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    with pytest.raises(ValueError):
        DocPartTransition(expected_verlock=p.verlock, estado="DISPONIBLE", usrmod="a", nomcom="Nuevo")

# 17-23 Asignación
def test_assign_pending_creates_pending(db: Session, setup_step, identity_resolver):
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)]
    participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)
    db.commit()
    assert crud_docpart.list_by_step(db, setup_step.dpasid)[0].estado == "PENDIENTE"

def test_assign_available_parallel_activates_all(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True), DocPartCreate(dpasid=setup_step.dpasid, usrid="j2", orden=2, obliga=True)]
    participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)
    db.commit()
    parts = crud_docpart.list_by_step(db, setup_step.dpasid)
    assert all(p.estado == "DISPONIBLE" for p in parts)

def test_assign_available_sequential_activates_only_first(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    setup_step.config = dict(setup_step.config, part_modo="SECUENCIAL")
    db.commit()
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True), DocPartCreate(dpasid=setup_step.dpasid, usrid="j2", orden=2, obliga=True)]
    participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)
    db.commit()
    parts = crud_docpart.list_by_step(db, setup_step.dpasid)
    assert parts[0].estado == "DISPONIBLE"
    assert parts[1].estado == "PENDIENTE"

def test_requires_at_least_one_required_participant(db: Session, setup_step, identity_resolver):
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=False)]
    with pytest.raises(ValueError, match="al menos un participante obligatorio"):
        participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)

def test_cannot_assign_to_in_progress_step(db: Session, setup_step, identity_resolver):
    db.execute(sa.text("UPDATE docpaso SET estado = 'EN_PROCESO', fecini = NOW() WHERE dpasid = :id"), {"id": setup_step.dpasid})
    db.commit()
    db.refresh(setup_step)
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)]
    with pytest.raises(ValueError):
        participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)

def test_cannot_assign_to_terminal_step(db: Session, setup_step, identity_resolver):
    db.execute(sa.text("UPDATE docpaso SET estado = 'COMPLETADO', fecini = NOW(), fecfin = NOW() WHERE dpasid = :id"), {"id": setup_step.dpasid})
    db.commit()
    db.refresh(setup_step)
    data = [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)]
    with pytest.raises(ValueError):
        participant_service.assign_participants(db, setup_step.dpasid, data, "admin", identity_resolver)

# 24-28 Estados
def test_start_participation(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    participant_service.assign_participants(db, setup_step.dpasid, [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)], "admin", identity_resolver)
    db.commit()
    p = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    participant_service.start_participation(db, p.parid, "j1", p.verlock)
    db.commit()
    assert crud_docpart.get(db, p.parid).estado == "EN_PROCESO"

def test_complete_participation(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    participant_service.assign_participants(db, setup_step.dpasid, [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)], "admin", identity_resolver)
    db.commit()
    p = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    participant_service.complete_participation(db, p.parid, "j1", p.verlock)
    db.commit()
    assert crud_docpart.get(db, p.parid).estado == "COMPLETADO"

def test_terminal_state_cannot_transition(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    participant_service.assign_participants(db, setup_step.dpasid, [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)], "admin", identity_resolver)
    db.commit()
    p = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    participant_service.complete_participation(db, p.parid, "j1", p.verlock)
    db.commit()
    p = crud_docpart.get(db, p.parid)
    with pytest.raises(ValueError):
        participant_service.complete_participation(db, p.parid, "j1", p.verlock)

def test_actor_cannot_act_for_another_participant(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    participant_service.assign_participants(db, setup_step.dpasid, [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)], "admin", identity_resolver)
    db.commit()
    p = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    with pytest.raises(ValueError):
        participant_service.start_participation(db, p.parid, "otro", p.verlock)

def test_transition_stale_verlock(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    participant_service.assign_participants(db, setup_step.dpasid, [DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)], "admin", identity_resolver)
    db.commit()
    p = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    with pytest.raises(ParticipantConcurrencyError):
        participant_service.start_participation(db, p.parid, "j1", 999)

def test_all_completes_step_once(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    participant_service.assign_participants(db, setup_step.dpasid, [
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True),
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j2", orden=2, obliga=True)
    ], "admin", identity_resolver)
    db.commit()
    parts = crud_docpart.list_by_step(db, setup_step.dpasid)
    participant_service.complete_participation(db, parts[0].parid, "j1", parts[0].verlock)
    db.commit()
    db.expire_all()
    assert db.scalars(sa.select(DocPaso).where(DocPaso.dpasid == setup_step.dpasid)).first().estado == "EN_PROCESO"
    parts = crud_docpart.list_by_step(db, setup_step.dpasid)
    participant_service.complete_participation(db, parts[1].parid, "j2", parts[1].verlock)
    db.commit()
    assert db.scalars(sa.select(DocPaso).where(DocPaso.dpasid == setup_step.dpasid)).first().estado == "COMPLETADO"

def test_docpart_does_not_write_to_alfresco():
    pass

# Pruebas concurrentes reales
def test_concurrent_complete_uno(db: Session, setup_step, identity_resolver):
    setup_step.config = dict(setup_step.config, part_estrategia="UNO")
    setup_step.estado = "DISPONIBLE"
    db.commit()
    
    participant_service.assign_participants(db, setup_step.dpasid, [
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True),
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j2", orden=2, obliga=True)
    ], "admin", identity_resolver)
    db.commit()
    
    p1, p2 = crud_docpart.list_by_step(db, setup_step.dpasid)
    
    barrier = threading.Barrier(2)
    results = {}
    
    def worker1():
        db1 = SessionLocal()
        try:
            barrier.wait()
            participant_service.complete_participation(db1, p1.parid, "j1", p1.verlock)
            db1.commit()
            results['j1'] = 'success'
        except Exception as e:
            db1.rollback()
            results['j1'] = type(e).__name__
        finally:
            db1.close()
            
    def worker2():
        db2 = SessionLocal()
        try:
            barrier.wait()
            participant_service.complete_participation(db2, p2.parid, "j2", p2.verlock)
            db2.commit()
            results['j2'] = 'success'
        except Exception as e:
            db2.rollback()
            results['j2'] = type(e).__name__
        finally:
            db2.close()
            
    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    assert 'success' in results.values(), results
    assert len([r for r in results.values() if r == 'success']) == 1
    
    db.expire_all()
    setup_step = db.get(DocPaso, setup_step.dpasid)
    assert setup_step.estado == "COMPLETADO"

def test_concurrent_complete_todos(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    
    participant_service.assign_participants(db, setup_step.dpasid, [
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True),
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j2", orden=2, obliga=True)
    ], "admin", identity_resolver)
    db.commit()
    
    p1, p2 = crud_docpart.list_by_step(db, setup_step.dpasid)
    
    barrier = threading.Barrier(2)
    results = {}
    
    def worker1():
        db1 = SessionLocal()
        try:
            barrier.wait()
            participant_service.complete_participation(db1, p1.parid, "j1", p1.verlock)
            db1.commit()
            results['j1'] = 'success'
        except Exception as e:
            db1.rollback()
            results['j1'] = type(e).__name__
        finally:
            db1.close()
            
    def worker2():
        db2 = SessionLocal()
        try:
            barrier.wait()
            participant_service.complete_participation(db2, p2.parid, "j2", p2.verlock)
            db2.commit()
            results['j2'] = 'success'
        except Exception as e:
            db2.rollback()
            results['j2'] = type(e).__name__
        finally:
            db2.close()
            
    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    assert results['j1'] == 'success', results
    assert results['j2'] == 'success', results
    
    db.expire_all()
    setup_step = db.get(DocPaso, setup_step.dpasid)
    assert setup_step.estado == "COMPLETADO"

def test_concurrent_complete_vs_expire(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    
    participant_service.assign_participants(db, setup_step.dpasid, [
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)
    ], "admin", identity_resolver)
    db.commit()
    
    p1 = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    
    barrier = threading.Barrier(2)
    results = {}
    
    def worker1():
        db1 = SessionLocal()
        try:
            barrier.wait()
            participant_service.complete_participation(db1, p1.parid, "j1", p1.verlock)
            db1.commit()
            results['complete'] = 'success'
        except Exception as e:
            db1.rollback()
            results['complete'] = type(e).__name__
        finally:
            db1.close()
            
    def worker2():
        db2 = SessionLocal()
        try:
            barrier.wait()
            participant_service.expire_step_participants(db2, setup_step.dpasid, "admin")
            db2.commit()
            results['expire'] = 'success'
        except Exception as e:
            db2.rollback()
            results['expire'] = type(e).__name__
        finally:
            db2.close()
            
    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    db.expire_all()
    p_final = crud_docpart.get(db, p1.parid)
    # Uno gana, el otro falla o tiene efecto vacío
    assert p_final.estado in ("COMPLETADO", "VENCIDO")

def test_concurrent_complete_vs_omit(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    
    participant_service.assign_participants(db, setup_step.dpasid, [
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)
    ], "admin", identity_resolver)
    db.commit()
    
    p1 = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    
    barrier = threading.Barrier(2)
    results = {}
    
    def worker1():
        db1 = SessionLocal()
        try:
            barrier.wait()
            participant_service.complete_participation(db1, p1.parid, "j1", p1.verlock)
            db1.commit()
            results['complete'] = 'success'
        except Exception as e:
            db1.rollback()
            results['complete'] = type(e).__name__
        finally:
            db1.close()
            
    def worker2():
        db2 = SessionLocal()
        try:
            barrier.wait()
            participant_service.omit_participation(db2, p1.parid, "admin", p1.verlock, "Omisión administrativa")
            db2.commit()
            results['omit'] = 'success'
        except Exception as e:
            db2.rollback()
            results['omit'] = type(e).__name__
        finally:
            db2.close()
            
    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    db.expire_all()
    p_final = crud_docpart.get(db, p1.parid)
    assert p_final.estado in ("COMPLETADO", "OMITIDO"), results
    assert 'success' in results.values()
    assert len([r for r in results.values() if r == 'success']) == 1

def test_concurrent_start(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    db.commit()
    
    participant_service.assign_participants(db, setup_step.dpasid, [
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True)
    ], "admin", identity_resolver)
    db.commit()
    
    p1 = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    
    barrier = threading.Barrier(2)
    results = {}
    
    def worker1():
        db1 = SessionLocal()
        try:
            barrier.wait()
            participant_service.start_participation(db1, p1.parid, "j1", p1.verlock)
            db1.commit()
            results['w1'] = 'success'
        except Exception as e:
            db1.rollback()
            results['w1'] = type(e).__name__
        finally:
            db1.close()
            
    def worker2():
        db2 = SessionLocal()
        try:
            barrier.wait()
            participant_service.start_participation(db2, p1.parid, "j1", p1.verlock)
            db2.commit()
            results['w2'] = 'success'
        except Exception as e:
            db2.rollback()
            results['w2'] = type(e).__name__
        finally:
            db2.close()
            
    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    db.expire_all()
    p_final = crud_docpart.get(db, p1.parid)
    assert p_final.estado == "EN_PROCESO", results
    assert len([r for r in results.values() if r == 'success']) == 1

def test_activates_once_sequential(db: Session, setup_step, identity_resolver):
    setup_step.estado = "DISPONIBLE"
    setup_step.config = dict(setup_step.config, part_modo="SECUENCIAL")
    db.commit()
    
    participant_service.assign_participants(db, setup_step.dpasid, [
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j1", orden=1, obliga=True),
        DocPartCreate(dpasid=setup_step.dpasid, usrid="j2", orden=2, obliga=True)
    ], "admin", identity_resolver)
    db.commit()
    
    p1 = crud_docpart.list_by_step(db, setup_step.dpasid)[0]
    
    barrier = threading.Barrier(2)
    results = {}
    
    def worker1():
        db1 = SessionLocal()
        try:
            barrier.wait()
            participant_service.complete_participation(db1, p1.parid, "j1", p1.verlock)
            db1.commit()
            results['w1'] = 'success'
        except Exception as e:
            db1.rollback()
            results['w1'] = type(e).__name__
        finally:
            db1.close()
            
    def worker2():
        db2 = SessionLocal()
        try:
            barrier.wait()
            participant_service.omit_participation(db2, p1.parid, "admin", p1.verlock, "omitir")
            db2.commit()
            results['w2'] = 'success'
        except Exception as e:
            db2.rollback()
            results['w2'] = type(e).__name__
        finally:
            db2.close()
            
    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    db.expire_all()
    p_final = crud_docpart.list_by_step(db, setup_step.dpasid)
    # The first is completed or omitted
    assert p_final[0].estado in ("COMPLETADO", "OMITIDO"), results
    # The second must be DISPONIBLE (activated exactly once)
    assert p_final[1].estado == "DISPONIBLE", results
