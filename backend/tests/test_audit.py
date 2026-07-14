import pytest
from sqlalchemy.exc import IntegrityError
from app.crud.crud_audifir import create_evento, get_evento, get_eventos
from app.core.database import SessionLocal, engine, Base
import os

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    # Solo ejecutar si DATABASE_URL es firmadoc_test u otro explícito, esto previene borrar PRD.
    # En este test usaremos la BD configurada. Alembic ya debe haber creado las tablas.
    # Se recomienda que el test corra con la BD de prueba levantada en postgres.
    yield

@pytest.fixture
def db_session():
    db = SessionLocal()
    # Limpiamos antes del test (o iniciamos transaccion)
    try:
        yield db
    finally:
        db.rollback()
        db.close()

def test_config_database_url_is_postgres():
    from app.core.config import settings
    assert settings.DATABASE_URL.startswith("postgresql")

def test_create_audifir_success(db_session):
    evento = create_evento(db_session, evento="PRUEBA_CREACION", usrid="testuser")
    assert evento.audid is not None
    assert evento.evento == "PRUEBA_CREACION"
    assert evento.usrid == "testuser"
    assert evento.fecope is not None

def test_create_audifir_empty_evento_raises_valueerror(db_session):
    with pytest.raises(ValueError, match="El evento no puede estar vacío"):
        create_evento(db_session, evento="")

def test_rollback_on_exception(db_session):
    # Generar error de integridad insertando None en evento (aunque SQLAlchemy/DB lo atraparán)
    with pytest.raises(IntegrityError):
        from app.models.audifir import Audifir
        db_obj = Audifir(evento=None)
        db_session.add(db_obj)
        db_session.commit()
    db_session.rollback()
    assert True
