from contextlib import nullcontext

import pytest

from app.models.application import Application
from app.models.bench import Bench
from app.models.deployment import Deployment
from app.models.organization import Organization
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.models.user import User
from app.services import crypto
from app.services.frappe.bench_client import BenchCommandError
from app.services.provisioning.site_setup import SiteSetupError

SITE = "acme-crm.tenants.localhost"


@pytest.fixture()
def tenant_and_deployment(db_session):
    org = Organization(name="Acme Inc", slug="acme")
    db_session.add(org)
    db_session.flush()

    db_session.add(
        User(organization_id=org.id, email="owner@acme.example", hashed_password="x", role="owner")
    )

    app_row = Application(
        key="erpnext",
        display_name="ERPNext",
        compose_template_path="dedicated-tenant-compose.yml.j2",
        default_version="version-15",
    )
    bench = Bench(
        key="default",
        compose_project_name="bluhands-shared-bench",
        compose_file_path="docker/shared-bench-compose.yml",
        backend_service="backend",
        frontend_http_port=8080,
        base_domain="tenants.localhost",
        image_version="erpnext-15.120.0",
    )
    db_session.add_all([app_row, bench])
    db_session.flush()

    tenant = Tenant(
        organization_id=org.id,
        application_id=app_row.id,
        bench_id=bench.id,
        name="Acme CRM",
        slug="acme-crm",
        site_name=SITE,
        hosting_mode="shared_bench",
    )
    db_session.add(tenant)
    db_session.flush()

    deployment = Deployment(tenant_id=tenant.id, bench_id=bench.id, status="pending")
    db_session.add(deployment)
    db_session.commit()

    return tenant, deployment


class FakeBenchClient:
    """Records the provisioning steps that were driven through bench, so the
    tests can assert the sequence without a container."""

    instances: list["FakeBenchClient"] = []

    def __init__(self, runtime):
        self.runtime = runtime
        self.calls: list[str] = []
        FakeBenchClient.instances.append(self)

    def new_site(self, admin_password, install_apps=("erpnext",)):
        self.calls.append("new_site")

    def drop_site(self):
        self.calls.append("drop_site")


@pytest.fixture()
def fake_bench(monkeypatch):
    FakeBenchClient.instances = []
    monkeypatch.setattr("app.tasks.provisioning_tasks.BenchClient", FakeBenchClient)
    monkeypatch.setattr(
        "app.tasks.provisioning_tasks.docker_runner.wait_for_healthy", lambda *a, **k: None
    )
    monkeypatch.setattr("app.tasks.provisioning_tasks.site_creation_lock", nullcontext)
    return FakeBenchClient


class _FakeRestClient:
    def __init__(self, ok=True):
        self._ok = ok

    def ping(self):
        return self._ok

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def _patch_happy_path(monkeypatch, db_session, ping_ok=True):
    monkeypatch.setattr("app.tasks.provisioning_tasks.SessionLocal", lambda: db_session)
    monkeypatch.setattr(
        "app.tasks.provisioning_tasks.site_setup.complete_setup_wizard", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.tasks.provisioning_tasks.site_setup.mint_api_keys", lambda *a, **k: ("KEY", "SECRET")
    )
    monkeypatch.setattr(
        "app.tasks.provisioning_tasks.FrappeClient.for_runtime",
        lambda *a, **k: _FakeRestClient(ping_ok),
    )


def test_provisioning_marks_running_and_stores_encrypted_credentials(
    db_session, tenant_and_deployment, fake_bench, monkeypatch
):
    tenant, deployment = tenant_and_deployment
    _patch_happy_path(monkeypatch, db_session)

    from app.tasks.provisioning_tasks import provision_tenant_task

    deployment_id = deployment.id
    tenant_id = tenant.id  # read before the task closes the session and detaches `tenant`
    provision_tenant_task.run(str(deployment_id), "admin-secret")

    # The task closes the session in its finally block, detaching the object.
    refreshed = db_session.get(Deployment, deployment_id)
    assert refreshed.status == "running"
    assert refreshed.status_detail is None
    assert refreshed.url == f"http://{SITE}"
    assert refreshed.site_name == SITE

    creds = {c.kind: c for c in db_session.query(TenantCredential).filter_by(tenant_id=tenant_id)}
    assert set(creds) == {"admin", "api"}
    assert creds["api"].api_key == "KEY"
    # Secrets must never be readable straight off the row.
    assert creds["api"].api_secret_encrypted != "SECRET"
    assert crypto.decrypt(creds["api"].api_secret_encrypted) == "SECRET"
    assert crypto.decrypt(creds["admin"].password_encrypted) == "admin-secret"


def test_failed_provisioning_drops_the_half_built_site(
    db_session, tenant_and_deployment, fake_bench, monkeypatch
):
    """A half-built site on a shared bench still occupies a database and a
    slot, and holds nothing worth keeping -- so failure cleans up rather
    than leaving wreckage for inspection."""
    _tenant, deployment = tenant_and_deployment
    _patch_happy_path(monkeypatch, db_session)
    monkeypatch.setattr(
        "app.tasks.provisioning_tasks.site_setup.complete_setup_wizard",
        lambda *a, **k: (_ for _ in ()).throw(SiteSetupError("wizard failed", "wizard output")),
    )

    from app.tasks.provisioning_tasks import provision_tenant_task

    deployment_id = deployment.id
    with pytest.raises(SiteSetupError):
        provision_tenant_task.run(str(deployment_id), "admin-secret")

    refreshed = db_session.get(Deployment, deployment_id)
    assert refreshed.status == "failed"
    assert "wizard output" in refreshed.status_detail
    assert FakeBenchClient.instances[-1].calls[-1] == "drop_site"


def test_no_site_is_dropped_when_creation_itself_failed(
    db_session, tenant_and_deployment, fake_bench, monkeypatch
):
    """If new-site never succeeded there is nothing to drop, and attempting
    it would bury the real error under a spurious cleanup failure."""
    _tenant, deployment = tenant_and_deployment
    _patch_happy_path(monkeypatch, db_session)

    def _boom(self, admin_password, install_apps=("erpnext",)):
        self.calls.append("new_site_failed")
        raise BenchCommandError("bench new-site failed", "mariadb refused the connection")

    monkeypatch.setattr(FakeBenchClient, "new_site", _boom)

    from app.tasks.provisioning_tasks import provision_tenant_task

    deployment_id = deployment.id
    with pytest.raises(BenchCommandError):
        provision_tenant_task.run(str(deployment_id), "admin-secret")

    refreshed = db_session.get(Deployment, deployment_id)
    assert refreshed.status == "failed"
    assert "mariadb refused the connection" in refreshed.status_detail
    assert "drop_site" not in FakeBenchClient.instances[-1].calls


def test_cleanup_failure_does_not_mask_the_original_error(
    db_session, tenant_and_deployment, fake_bench, monkeypatch
):
    _tenant, deployment = tenant_and_deployment
    _patch_happy_path(monkeypatch, db_session)
    monkeypatch.setattr(
        "app.tasks.provisioning_tasks.site_setup.mint_api_keys",
        lambda *a, **k: (_ for _ in ()).throw(SiteSetupError("could not mint", "key output")),
    )
    monkeypatch.setattr(
        FakeBenchClient,
        "drop_site",
        lambda self: (_ for _ in ()).throw(RuntimeError("drop exploded")),
    )

    from app.tasks.provisioning_tasks import provision_tenant_task

    deployment_id = deployment.id
    with pytest.raises(SiteSetupError):
        provision_tenant_task.run(str(deployment_id), "admin-secret")

    detail = db_session.get(Deployment, deployment_id).status_detail
    assert "could not mint" in detail          # the real cause survives
    assert "drop exploded" in detail           # and the cleanup problem is reported too


def test_unreachable_site_after_provisioning_is_a_failure(
    db_session, tenant_and_deployment, fake_bench, monkeypatch
):
    _tenant, deployment = tenant_and_deployment
    _patch_happy_path(monkeypatch, db_session, ping_ok=False)

    from app.tasks.provisioning_tasks import provision_tenant_task

    deployment_id = deployment.id
    with pytest.raises(RuntimeError):
        provision_tenant_task.run(str(deployment_id), "admin-secret")

    assert db_session.get(Deployment, deployment_id).status == "failed"
