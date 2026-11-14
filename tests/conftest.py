import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.application import Application
from app.models.bench import Bench

# Registers every model on Base.metadata. Imported as `from app import models`
# rather than `import app.models`, which would rebind the name `app` to the
# package and shadow the FastAPI instance imported above.
from app import models  # noqa: F401


@pytest.fixture(autouse=True)
def _credential_encryption_key(monkeypatch):
    """A fixed test key, so the suite does not depend on -- or leak -- the
    developer's real CREDENTIAL_ENCRYPTION_KEY from .env."""
    from cryptography.fernet import Fernet

    monkeypatch.setattr(
        "app.services.crypto.settings.credential_encryption_key", Fernet.generate_key().decode()
    )


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
            compose_template_path="dedicated-tenant-compose.yml.j2",
            default_version="version-15",
        )
    )
    # Tenant creation resolves the default bench to compute the site name.
    seed_db.add(
        Bench(
            key="default",
            compose_project_name="bluhands-shared-bench",
            compose_file_path="docker/shared-bench-compose.yml",
            backend_service="backend",
            frontend_http_port=8080,
            base_domain="tenants.localhost",
            image_version="test",
            status="ready",
            is_default=True,
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
