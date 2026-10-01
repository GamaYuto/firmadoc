from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.sso import DatabaseReplayStore, SsoAssertionError, SsoReplayStoreUnavailableError


BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
HEAD_REV = "d6d4d4a7d946"
BASE_REV = "f61284a6c891"
MIGRATION_DATABASE_URL = settings.DATABASE_URL


def _alembic_cfg() -> Config:
    return Config(str(ALEMBIC_INI))


def _make_db():
    engine = create_engine(MIGRATION_DATABASE_URL)
    session_factory = sessionmaker(bind=engine)
    return engine, session_factory


def _close_db(db, engine):
    try:
        db.close()
    finally:
        engine.dispose()


def _table_exists(db, table_name: str) -> bool:
    return bool(
        db.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": table_name},
        ).scalar()
    )


def _index_def(db, index_name: str) -> str:
    return db.execute(
        text(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND indexname = :name
            """
        ),
        {"name": index_name},
    ).scalar_one()


def _constraint_def(db, table_name: str, constraint_name: str) -> str:
    return db.execute(
        text(
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = current_schema()
              AND t.relname = :table_name
              AND c.conname = :constraint_name
            """
        ),
        {"table_name": table_name, "constraint_name": constraint_name},
    ).scalar_one()


def _version_num(db) -> str:
    return db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


@pytest.fixture(scope="module", autouse=True)
def isolated_migration_schema():
    original_url = settings.DATABASE_URL
    database_url = make_url(original_url)
    if not (database_url.database or "").endswith("_test"):
        pytest.exit("REFUSING_TO_RUN_MIGRATION_TESTS_AGAINST_NON_TEST_DATABASE", returncode=2)

    schema_name = f"migration_test_{uuid4().hex}"
    maintenance_engine = create_engine(original_url)
    with maintenance_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    scoped_url = database_url.set(
        query={**database_url.query, "options": f"-csearch_path={schema_name}"}
    ).render_as_string(hide_password=False)
    global MIGRATION_DATABASE_URL
    MIGRATION_DATABASE_URL = scoped_url
    settings.DATABASE_URL = scoped_url.replace("%", "%%")
    try:
        command.upgrade(_alembic_cfg(), BASE_REV)
        yield
    finally:
        settings.DATABASE_URL = original_url
        MIGRATION_DATABASE_URL = original_url
        try:
            with maintenance_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        finally:
            maintenance_engine.dispose()


def test_migration_upgrade_downgrade_normal():
    engine, Session = _make_db()
    db = Session()
    cfg = _alembic_cfg()
    try:
        command.downgrade(cfg, BASE_REV)
        db.close()
        db = Session()

        command.upgrade(cfg, HEAD_REV)
        db.close()
        db = Session()

        assert _version_num(db) == HEAD_REV
        assert _table_exists(db, "docfirma")
        assert _table_exists(db, "firpos")
        assert _table_exists(db, "docfir")

        command.downgrade(cfg, BASE_REV)
        db.close()
        db = Session()

        assert _version_num(db) == BASE_REV
        assert not _table_exists(db, "docfirma")
        assert not _table_exists(db, "firpos")
    finally:
        _close_db(db, engine)
        command.upgrade(cfg, "head")


def test_migration_downgrade_rechaza_eventos_firma():
    engine, Session = _make_db()
    db = Session()
    cfg = _alembic_cfg()
    try:
        command.upgrade(cfg, HEAD_REV)
        db.close()
        db = Session()

        db.execute(
            text(
                """
                INSERT INTO audifir (evento, enttip, entid, docid, usrid, fecope)
                VALUES ('TEST_FIRMA', 'FIRMA', 1, NULL, 'tester', now())
                """
            )
        )
        db.commit()

        with pytest.raises(Exception):
            command.downgrade(cfg, BASE_REV)

        db.close()
        db = Session()
        assert _version_num(db) == HEAD_REV
        assert _table_exists(db, "docfirma")
        assert _table_exists(db, "firpos")
        assert db.execute(text("SELECT COUNT(*) FROM audifir WHERE enttip = 'FIRMA'")).scalar_one() == 1
    finally:
        try:
            db.execute(text("DELETE FROM audifir WHERE enttip = 'FIRMA'"))
            db.commit()
        except Exception:
            db.rollback()
        _close_db(db, engine)
        try:
            command.downgrade(cfg, BASE_REV)
        except Exception:
            pass
        command.upgrade(cfg, "head")


def test_migration_preflight_rechaza_nodid_duplicado():
    engine, Session = _make_db()
    db = Session()
    cfg = _alembic_cfg()
    nodid = "dup-node-1"
    try:
        command.downgrade(cfg, BASE_REV)
        db.close()
        db = Session()

        db.execute(text("DELETE FROM docfir WHERE nodid = :nodid"), {"nodid": nodid})
        db.execute(
            text(
                """
                INSERT INTO docfir
                    (nodid, docnom, mimtip, tamano, verini, estado, hasori, usrcre, feccre, activo)
                VALUES
                    (:nodid, 'Doc1.pdf', 'application/pdf', 100, '1.0', 'EN_CURSO',
                     'a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0',
                     'tester', now(), true),
                    (:nodid, 'Doc2.pdf', 'application/pdf', 200, '2.0', 'EN_CURSO',
                     'b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0',
                     'tester', now(), true)
                """
            ),
            {"nodid": nodid},
        )
        db.commit()

        with pytest.raises(Exception):
            command.upgrade(cfg, HEAD_REV)

        db.close()
        db = Session()
        assert _version_num(db) == BASE_REV
        assert db.execute(text("SELECT COUNT(*) FROM docfir WHERE nodid = :nodid"), {"nodid": nodid}).scalar_one() == 2
        assert not _table_exists(db, "docfirma")
        assert not _table_exists(db, "firpos")
    finally:
        try:
            db.execute(text("DELETE FROM docfir WHERE nodid = :nodid"), {"nodid": nodid})
            db.commit()
        except Exception:
            db.rollback()
        _close_db(db, engine)
        command.upgrade(cfg, "head")


def test_migration_un_solo_head():
    script = ScriptDirectory.from_config(_alembic_cfg())
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == HEAD_REV


def test_migration_crea_constraints_e_indices():
    engine, Session = _make_db()
    db = Session()
    cfg = _alembic_cfg()
    try:
        command.upgrade(cfg, HEAD_REV)
        db.close()
        db = Session()

        assert _version_num(db) == HEAD_REV
        assert _table_exists(db, "docfirma")
        assert _table_exists(db, "firpos")

        docfir_index = _index_def(db, "uq_docfir_nodid_activo")
        docfirma_docid_index = _index_def(db, "uq_docfirma_docid_activa")
        docfirma_parid_index = _index_def(db, "uq_docfirma_parid_compl")
        audifir_constraint = _constraint_def(db, "audifir", "ck_audifir_enttip")

        assert "WHERE" in docfir_index
        assert "estado" in docfir_index
        assert "WHERE" in docfirma_docid_index
        assert "WHERE" in docfirma_parid_index
        assert "FIRMA" in audifir_constraint
    finally:
        _close_db(db, engine)
        command.upgrade(cfg, "head")


def test_migration_ssojti_replay_runtime_before_and_after_upgrade(monkeypatch):
    engine, Session = _make_db()
    db = Session()
    cfg = _alembic_cfg()
    replay_store_sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("app.core.sso.SessionLocal", replay_store_sessions)
    replay_store = DatabaseReplayStore()
    jti = uuid4().hex
    expires_at = 2_000_000_000
    try:
        command.downgrade(cfg, "d2b8c7a1e5f4")
        db.close()
        db = Session()

        assert _version_num(db) == "d2b8c7a1e5f4"
        assert not _table_exists(db, "ssojti")
        with pytest.raises(SsoReplayStoreUnavailableError):
            replay_store.consume(jti, expires_at)
        assert not _table_exists(db, "ssojti")
        assert _version_num(db) == "d2b8c7a1e5f4"

        command.upgrade(cfg, HEAD_REV)
        db.close()
        db = Session()

        assert _version_num(db) == HEAD_REV
        assert _table_exists(db, "ssojti")
        assert {column["name"] for column in inspect(db.bind).get_columns("ssojti")} == {
            "jtihas",
            "fecexp",
            "fecuse",
        }
        assert "ix_ssojti_fecexp" in inspect(db.bind).get_indexes("ssojti")[0]["name"]

        replay_store.consume(jti, expires_at)
        with pytest.raises(SsoAssertionError, match="ya fue usado"):
            replay_store.consume(jti, expires_at)

        command.downgrade(cfg, "d2b8c7a1e5f4")
        db.close()
        db = Session()

        assert _version_num(db) == "d2b8c7a1e5f4"
        assert not _table_exists(db, "ssojti")

        command.upgrade(cfg, HEAD_REV)
        db.close()
        db = Session()
        assert _version_num(db) == HEAD_REV
        assert _table_exists(db, "ssojti")
    finally:
        _close_db(db, engine)
        command.upgrade(cfg, "head")
