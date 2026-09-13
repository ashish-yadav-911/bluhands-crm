import pytest

from app.models.application import Application
from app.models.deployment import Deployment
from app.models.organization import Organization
from app.models.tenant import Tenant
from app.services.provisioning.docker_runner import DockerCommandError


@pytest.fixture()
def tenant_and_deployment(db_session):
    org = Organization(name="Acme", slug="acme")
    db_session.add(org)
    db_session.flush()

    app_row = Application(
        key="erpnext",
        display_name="ERPNext",
        compose_template_path="tenant-compose.yml.j2",
        default_version="version-15",
    )
    db_session.add(app_row)
    db_session.flush()

    tenant = Tenant(organization_id=org.id, application_id=app_row.id, name="Acme CRM", slug="acme-crm")
    db_session.add(tenant)
    db_session.flush()

    deployment = Deployment(tenant_id=tenant.id, status="pending")
    db_session.add(deployment)
    db_session.commit()

    return tenant, deployment


def test_provision_tenant_task_success_marks_running(db_session, tenant_and_deployment, monkeypatch, tmp_path):
    _tenant, deployment = tenant_and_deployment

    monkeypatch.setattr("app.tasks.provisioning_tasks.SessionLocal", lambda: db_session)
    monkeypatch.setattr("app.tasks.provisioning_tasks.docker_runner.compose_pull", lambda *a, **k: None)
    monkeypatch.setattr("app.tasks.provisioning_tasks.docker_runner.compose_up", lambda *a, **k: None)
    monkeypatch.setattr("app.tasks.provisioning_tasks.docker_runner.wait_for_healthy", lambda *a, **k: None)
    monkeypatch.setattr("app.tasks.provisioning_tasks.frappe_bootstrap.create_site", lambda *a, **k: None)
    monkeypatch.setattr("app.tasks.provisioning_tasks.proxy_registrar.register_tenant_route", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.tasks.provisioning_tasks.render_compose_file",
        lambda template_path, ctx: tmp_path / "docker-compose.yml",
    )
    (tmp_path / "docker-compose.yml").write_text("services: {}", encoding="utf-8")

    from app.tasks.provisioning_tasks import provision_tenant_task

    deployment_id = deployment.id
    provision_tenant_task.run(str(deployment_id), "admin-secret")

    # The task's finally-block closes the session (as it would its own),
    # which detaches `deployment` -- re-fetch by id rather than refresh().
    refreshed = db_session.get(Deployment, deployment_id)
    assert refreshed.status == "running"
    assert refreshed.url is not None
    assert refreshed.status_detail is None


def test_provision_tenant_task_failure_marks_failed(db_session, tenant_and_deployment, monkeypatch, tmp_path):
    _tenant, deployment = tenant_and_deployment

    monkeypatch.setattr("app.tasks.provisioning_tasks.SessionLocal", lambda: db_session)
    monkeypatch.setattr("app.tasks.provisioning_tasks.docker_runner.compose_pull", lambda *a, **k: None)

    def _boom(*args, **kwargs):
        raise DockerCommandError("docker compose up failed", "some docker error output")

    monkeypatch.setattr("app.tasks.provisioning_tasks.docker_runner.compose_up", _boom)
    monkeypatch.setattr(
        "app.tasks.provisioning_tasks.render_compose_file",
        lambda template_path, ctx: tmp_path / "docker-compose.yml",
    )
    (tmp_path / "docker-compose.yml").write_text("services: {}", encoding="utf-8")

    from app.tasks.provisioning_tasks import provision_tenant_task

    deployment_id = deployment.id
    with pytest.raises(DockerCommandError):
        provision_tenant_task.run(str(deployment_id), "admin-secret")

    refreshed = db_session.get(Deployment, deployment_id)
    assert refreshed.status == "failed"
    assert "some docker error output" in refreshed.status_detail
