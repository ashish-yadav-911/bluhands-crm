import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.application import Application

# Registers every model on Base.metadata. Imported as `from app import models`
# rather than `import app.models`, which would rebind the name `app` to the
# package and shadow the FastAPI instance imported above.
from app import models  # noqa: F401


@pytest.fixture()
def db_session():
    # StaticPool keeps every connection pointed at the same in-memory
    # SQLite database -- without it, closing and reopening a session (as
    # the provisioning task does) would silently start a fresh empty DB.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(monkeypatch):
    """API client backed by a throwaway in-memory DB with the erpnext
    application row seeded (mirrors app/seed.py). Shared by every router
    test module -- each one used to carry its own near-identical copy."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    seed_db = TestSessionLocal()
    seed_db.add(
        Application(
            key="erpnext",
            display_name="ERPNext",
            compose_template_path="tenant-compose.yml.j2",
            default_version="version-15",
        )
    )
    seed_db.commit()
    seed_db.close()

    # Avoid touching a real Redis broker -- the routers only need `.delay`
    # to be callable; the tasks themselves are tested separately.
    monkeypatch.setattr(
        "app.routers.tenants.provision_tenant_task.delay", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "app.routers.agent_jobs.run_agent_job_task.delay", lambda *args, **kwargs: None
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
