"""Pytest fixtures backed by real PostgreSQL 16 (oxh_prior_auth_test)."""

import os
from collections.abc import Generator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import build_engine, build_session_factory, get_db_session
from app.main import create_app
from app.seed import seed_database


@pytest.fixture(scope="session")
def test_db_url() -> str:
    settings = get_settings()
    return os.getenv("TEST_DATABASE_URL", settings.test_database_url)


@pytest.fixture(scope="session")
def db_engine(test_db_url: str) -> Generator[Engine, None, None]:
    os.environ["DATABASE_URL"] = test_db_url
    get_settings.cache_clear()

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", test_db_url)
    command.upgrade(alembic_cfg, "head")

    engine = build_engine(test_db_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return build_session_factory(db_engine)


@pytest.fixture(autouse=True)
def clean_and_seed_db(session_factory: sessionmaker[Session]) -> Generator[None, None, None]:
    with session_factory() as db:
        db.execute(
            text(
                """
                TRUNCATE TABLE
                    submission_attempts,
                    audit_events,
                    procedure_decisions,
                    authorization_procedures,
                    prior_authorizations,
                    reviewer_patients,
                    coverages,
                    practice_members,
                    users,
                    members,
                    practices
                RESTART IDENTITY CASCADE
                """
            )
        )
        db.commit()
        seed_database(db)
    yield


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with session_factory() as db:
        yield db


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    app = create_app()

    def _override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
