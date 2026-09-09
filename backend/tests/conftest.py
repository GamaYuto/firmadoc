import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from app.main import app
from app.core.database import Base, SessionLocal
from app.models.audifir import Audifir
from app.models.docpart import DocPart
from app.models.docpaso import DocPaso
from app.models.flupaso import Flupaso
from app.models.docfir import DocFir
from app.models.flujodoc import Flujodoc
from app.models.tplcamp import TplCamp
from app.models.plantill import Plantill
from app.models.docfirma import DocFirma
from app.models.firpos import Firpos


def pytest_configure(config):
    bind = SessionLocal.kw.get("bind")
    database_name = getattr(getattr(bind, "url", None), "database", "") or ""
    print(database_name)
    if not database_name.endswith("_test"):
        pytest.exit("REFUSING_TO_RUN_TESTS_AGAINST_NON_TEST_DATABASE", returncode=2)

@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client

@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()

@pytest.fixture
def db(db_session):
    yield db_session

@pytest.fixture(autouse=True)
def clean_db(db_session):
    db_session.rollback()
    db_session.expunge_all()
    bind = db_session.get_bind()
    inspector = inspect(bind)
    tables = [table for table in Base.metadata.sorted_tables if inspector.has_table(table.name)]
    if bind.dialect.name == "postgresql" and tables:
        quoted_names = ", ".join(f'"{table.name}"' for table in tables)
        db_session.execute(text(f"TRUNCATE {quoted_names} RESTART IDENTITY CASCADE"))
        db_session.commit()
        return
    for table in reversed(tables):
        db_session.execute(table.delete())
    db_session.commit()
