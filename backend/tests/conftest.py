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

from app.core.config import settings
from app.core.security import AuthenticatedPrincipal, create_session_token

class AuthTestClient(TestClient):
    def request(self, *args, **kwargs):
        headers = kwargs.get("headers")
        if headers is None:
            headers = {}
            kwargs["headers"] = headers
        else:
            headers = dict(headers)
            kwargs["headers"] = headers

        cookies = kwargs.get("cookies")
        if "X-FirmaDoc-User" in headers:
            user_val = headers.get("X-FirmaDoc-User")
            if user_val and user_val.strip():
                u_id = user_val.strip().lower()
                if "admin" in u_id:
                    roles = ("ADMIN", "GESTOR")
                elif "gestor" in u_id or "preparador" in u_id:
                    roles = ("GESTOR",)
                else:
                    roles = ("EMPLEADO",)
                principal = AuthenticatedPrincipal(
                    user_id=u_id,
                    nombre_completo=f"Test {u_id.capitalize()}",
                    correo=f"{u_id}@firmadoc.local",
                    roles=roles,
                )
                token = create_session_token(principal)
                if cookies is None:
                    cookies = {}
                    kwargs["cookies"] = cookies
                if settings.FIRMADOC_SESSION_COOKIE_NAME not in cookies:
                    cookies[settings.FIRMADOC_SESSION_COOKIE_NAME] = token
                if "X-FirmaDoc-CSRF" not in headers and principal.csrf_token:
                    headers["X-FirmaDoc-CSRF"] = principal.csrf_token
        return super().request(*args, **kwargs)

@pytest.fixture
def client():
    with AuthTestClient(app) as client:
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
def clean_db(request):
    if "test_auth" in request.module.__name__ or "test_qr_mobile" in request.module.__name__:
        return
    db_session = request.getfixturevalue("db_session")
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
