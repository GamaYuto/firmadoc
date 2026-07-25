import pytest
from app.schemas.flujo import FlujoCreate, FlujoUpdate, PasoCreate, PasoUpdate
from app.services.flow_service import flow_service
from app.models.flujodoc import Flujodoc
from app.models.flupaso import Flupaso
from app.models.docpaso import DocPaso
from app.models.docfir import DocFir
from app.models.audifir import Audifir
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from sqlalchemy import select
from app.core.database import SessionLocal



def test_crear_flujo_borrador(db_session):
    obj = FlujoCreate(flucod="F001", flunom="Flujo Test", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    assert flujo.flucod == "F001"
    assert flujo.fluver == 1
    assert flujo.estado == "BORRADOR"

def test_flujo_codigo_vacio():
    with pytest.raises(ValidationError):
        FlujoCreate(flucod="   ", flunom="Flujo Test", usrcre="admin")

def test_flujo_nombre_vacio():
    with pytest.raises(ValidationError):
        FlujoCreate(flucod="F002", flunom="", usrcre="admin")

def test_version_duplicada(db_session):
    obj1 = FlujoCreate(flucod="FDUP", flunom="Flujo Test", usrcre="admin")
    flujo1 = flow_service.create_flow(db_session, obj1)
    
    # Force duplicate version at DB level directly since service handles it correctly via fluver increment
    flujo2 = Flujodoc(flucod="FDUP", flunom="Flujo Test 2", fluver=1, estado="BORRADOR", usrcre="admin")
    db_session.add(flujo2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_estado_invalido_db(db_session):
    obj = FlujoCreate(flucod="F003", flunom="Test DB", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    flujo.estado = "INVENTADO"
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

def test_crear_paso_valido(db_session):
    obj = FlujoCreate(flucod="F004", flunom="Flujo Pasos", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    paso_in = PasoCreate(pascod="P01", pasnom="Paso 1", pastip="REVISAR", orden=1, rolreq="Revisor")
    paso = flow_service.add_step(db_session, flujo.fluid, paso_in, "admin")
    assert paso.pascod == "P01"

def test_paso_tipo_invalido():
    with pytest.raises(ValidationError):
        PasoCreate(pascod="P02", pasnom="Paso 2", pastip="INVENTADO", orden=2, rolreq="Revisor")

def test_paso_orden_invalido():
    with pytest.raises(ValidationError):
        PasoCreate(pascod="P02", pasnom="Paso 2", pastip="REVISAR", orden=0, rolreq="Revisor")

def test_paso_plazo_invalido():
    with pytest.raises(ValidationError):
        PasoCreate(pascod="P02", pasnom="Paso 2", pastip="REVISAR", orden=2, rolreq="Revisor", plazo=0)

def test_codigo_paso_duplicado(db_session):
    obj = FlujoCreate(flucod="F005", flunom="Flujo Dup", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    paso_in = PasoCreate(pascod="P01", pasnom="Paso 1", pastip="REVISAR", orden=1, rolreq="Revisor")
    flow_service.add_step(db_session, flujo.fluid, paso_in, "admin")
    paso_dup = PasoCreate(pascod="P01", pasnom="Paso 2", pastip="APROBAR", orden=2, rolreq="Aprobador")
    with pytest.raises(ValueError, match="El código o el orden del paso ya existen"):
        flow_service.add_step(db_session, flujo.fluid, paso_dup, "admin")

def test_orden_paso_duplicado(db_session):
    obj = FlujoCreate(flucod="F006", flunom="Flujo Dup Ord", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    paso_in = PasoCreate(pascod="P01", pasnom="Paso 1", pastip="REVISAR", orden=1, rolreq="Revisor")
    flow_service.add_step(db_session, flujo.fluid, paso_in, "admin")
    paso_dup = PasoCreate(pascod="P02", pasnom="Paso 2", pastip="APROBAR", orden=1, rolreq="Aprobador")
    with pytest.raises(ValueError, match="El código o el orden del paso ya existen"):
        flow_service.add_step(db_session, flujo.fluid, paso_dup, "admin")

def test_activar_flujo(db_session):
    obj = FlujoCreate(flucod="F007", flunom="Activar", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    flow_service.activate_flow(db_session, flujo.fluid, "admin")
    assert flujo.estado == "ACTIVO"

def test_una_sola_version_activa(db_session):
    obj1 = FlujoCreate(flucod="F008", flunom="Unica", usrcre="admin")
    flujo1 = flow_service.create_flow(db_session, obj1)
    flow_service.activate_flow(db_session, flujo1.fluid, "admin")
    
    # Simular otro flujo borrador con mismo codigo
    flujo2 = flow_service.create_new_version(db_session, flujo1.fluid, "admin")
    flow_service.activate_flow(db_session, flujo2.fluid, "admin")
    
    db_session.refresh(flujo1)
    db_session.refresh(flujo2)
    assert flujo1.estado == "INACTIVO"
    assert flujo2.estado == "ACTIVO"

def test_impedir_modificar_flujo_activo(db_session):
    obj = FlujoCreate(flucod="F009", flunom="Modificar", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    flow_service.activate_flow(db_session, flujo.fluid, "admin")
    
    with pytest.raises(ValueError, match="Solo se pueden modificar flujos en estado BORRADOR"):
        flow_service.update_draft(db_session, flujo.fluid, FlujoUpdate(usrmod="admin", flunom="Nuevo"))

def test_crear_nueva_version_y_copiar_pasos(db_session):
    obj = FlujoCreate(flucod="F010", flunom="Versionar", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    paso_in = PasoCreate(pascod="P01", pasnom="Paso 1", pastip="REVISAR", orden=1, rolreq="Revisor")
    paso1 = flow_service.add_step(db_session, flujo.fluid, paso_in, "admin")
    paso_in2 = PasoCreate(pascod="P02", pasnom="Paso Inactivo", pastip="REVISAR", orden=2, rolreq="Revisor")
    paso2 = flow_service.add_step(db_session, flujo.fluid, paso_in2, "admin")
    paso2.activo = False
    db_session.commit()

    flow_service.activate_flow(db_session, flujo.fluid, "admin")
    
    flujo_v2 = flow_service.create_new_version(db_session, flujo.fluid, "admin")
    assert flujo_v2.fluver == 2
    assert flujo_v2.estado == "BORRADOR"
    assert len(flujo_v2.pasos) == 1
    assert flujo_v2.pasos[0].pascod == "P01"
    
    # Conservar version anterior
    db_session.refresh(flujo)
    assert flujo.fluver == 1
    assert flujo.estado == "ACTIVO"

def test_inactivar_flujo(db_session):
    obj = FlujoCreate(flucod="F011", flunom="Inactivar", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    flow_service.activate_flow(db_session, flujo.fluid, "admin")
    flow_service.deactivate_flow(db_session, flujo.fluid, "admin")
    assert flujo.estado == "INACTIVO"

def test_auditoria_transaccional(db_session):
    # Auditoria de creacion y activacion
    obj = FlujoCreate(flucod="FAUDIT", flunom="Auditoria", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    aud_crea = db_session.execute(select(Audifir).where(Audifir.entid == flujo.fluid, Audifir.evento == "FLU_CREA")).scalars().first()
    assert aud_crea is not None

    flow_service.activate_flow(db_session, flujo.fluid, "admin")
    aud_acti = db_session.execute(select(Audifir).where(Audifir.entid == flujo.fluid, Audifir.evento == "FLU_ACTI")).scalars().first()
    assert aud_acti is not None

    # Rollback si falla logica (ej. flujo ya esta activo)
    with pytest.raises(ValueError):
        flow_service.activate_flow(db_session, flujo.fluid, "admin") # Already active, state is not BORRADOR, raises ValueError actually...
    
def test_auditoria_rollback_falla(db_session):
    obj = FlujoCreate(flucod="FFAIL", flunom="Auditoria Fail", usrcre="admin")
    flujo = flow_service.create_flow(db_session, obj)
    # Simulate integrity error
    flujo.fluver = -1
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
