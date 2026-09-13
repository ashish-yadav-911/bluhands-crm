import pytest

from app.models.agent_job import AgentJob
from app.models.application import Application
from app.models.organization import Organization
from app.models.tenant import Tenant
from app.models.user import User
from app.services.agent.base import AgentRunResult
from app.services.agent.git_runner import GitCommandError
from app.services.security import hash_password


@pytest.fixture()
def tenant_and_job(db_session):
    org = Organization(name="Acme", slug="acme")
    db_session.add(org)
    db_session.flush()

    user = User(organization_id=org.id, email="owner@acme.example", hashed_password=hash_password("secret"))
    db_session.add(user)
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

    job = AgentJob(tenant_id=tenant.id, created_by_user_id=user.id, prompt="Add a WhatsApp button", status="pending")
    db_session.add(job)
    db_session.commit()

    return tenant, job


def test_run_agent_job_task_success_marks_succeeded(db_session, tenant_and_job, monkeypatch, tmp_path):
    _tenant, job = tenant_and_job

    monkeypatch.setattr("app.tasks.agent_tasks.SessionLocal", lambda: db_session)
    monkeypatch.setattr("app.tasks.agent_tasks.ensure_tenant_repo", lambda slug: tmp_path)
    monkeypatch.setattr(
        "app.tasks.agent_tasks.get_agent_runner",
        lambda: type("_FakeRunner", (), {"run": staticmethod(lambda ctx: AgentRunResult(success=True, summary="did the thing"))})(),
    )
    monkeypatch.setattr("app.tasks.agent_tasks.capture_diff", lambda repo_path, commit_message: "diff --git a/x b/x")

    from app.tasks.agent_tasks import run_agent_job_task

    job_id = job.id
    run_agent_job_task.run(str(job_id))

    refreshed = db_session.get(AgentJob, job_id)
    assert refreshed.status == "succeeded"
    assert refreshed.summary == "did the thing"
    assert refreshed.diff == "diff --git a/x b/x"
    assert refreshed.status_detail is None


def test_run_agent_job_task_runner_failure_marks_failed(db_session, tenant_and_job, monkeypatch, tmp_path):
    _tenant, job = tenant_and_job

    monkeypatch.setattr("app.tasks.agent_tasks.SessionLocal", lambda: db_session)
    monkeypatch.setattr("app.tasks.agent_tasks.ensure_tenant_repo", lambda slug: tmp_path)
    monkeypatch.setattr(
        "app.tasks.agent_tasks.get_agent_runner",
        lambda: type(
            "_FakeRunner",
            (),
            {"run": staticmethod(lambda ctx: AgentRunResult(success=False, summary="", error="model call failed"))},
        )(),
    )

    from app.tasks.agent_tasks import run_agent_job_task

    job_id = job.id
    with pytest.raises(RuntimeError):
        run_agent_job_task.run(str(job_id))

    refreshed = db_session.get(AgentJob, job_id)
    assert refreshed.status == "failed"
    assert "model call failed" in refreshed.status_detail


def test_run_agent_job_task_git_failure_marks_failed(db_session, tenant_and_job, monkeypatch, tmp_path):
    _tenant, job = tenant_and_job

    def _boom(*args, **kwargs):
        raise GitCommandError("git commit failed", "nothing to commit, working tree clean??")

    monkeypatch.setattr("app.tasks.agent_tasks.SessionLocal", lambda: db_session)
    monkeypatch.setattr("app.tasks.agent_tasks.ensure_tenant_repo", _boom)

    from app.tasks.agent_tasks import run_agent_job_task

    job_id = job.id
    with pytest.raises(GitCommandError):
        run_agent_job_task.run(str(job_id))

    refreshed = db_session.get(AgentJob, job_id)
    assert refreshed.status == "failed"
