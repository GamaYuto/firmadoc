import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.audifir import Audifir
from app.models.docpart import DocPart
from app.models.docpaso import DocPaso
from app.models.flupaso import Flupaso
from app.models.docfir import DocFir
from app.models.flujodoc import Flujodoc
from app.models.tplcamp import TplCamp
from app.models.plantill import Plantill

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
    db_session.execute(Audifir.__table__.delete())
    db_session.execute(DocPart.__table__.delete())
    db_session.execute(DocPaso.__table__.delete())
    db_session.execute(TplCamp.__table__.delete())
    db_session.execute(DocFir.__table__.delete())
    db_session.execute(Flupaso.__table__.delete())
    db_session.execute(Flujodoc.__table__.delete())
    db_session.execute(Plantill.__table__.delete())
    db_session.commit()
