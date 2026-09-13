import uuid

from app.celery_app import celery_app
from app.config import settings
from app.db.base import SessionLocal
from app.models.agent_job import AgentJob
from app.models.tenant import Tenant
from app.services.agent.base import AgentRunContext
from app.services.agent.factory import get_agent_runner
from app.services.agent.git_runner import GitCommandError
from app.services.agent.repo import capture_diff, ensure_tenant_repo


def _set_status(db, job: AgentJob, status: str, detail: str | None = None) -> None:
    job.status = status
    job.status_detail = detail
    db.commit()


# Backstop above the AgentRunner's own internal timeout (e.g. OpenHandsRunner's
# docker-run timeout), so a hung agent run can't tie up the agent_jobs queue
# forever even if the runner implementation itself fails to bound its work.
@celery_app.task(bind=True, time_limit=settings.agent_job_timeout_seconds + 120)
def run_agent_job_task(self, agent_job_id: str) -> None:
    """End-to-end agent job: checkout/scaffold the tenant's custom-app repo
    -> run the configured AgentRunner against it -> commit and capture
    whatever it changed as a diff -> mark succeeded/failed. Mirrors
    provisioning_tasks.provision_tenant_task's status-tracking shape.
    Does NOT install the result into the tenant's live site or redeploy --
    that's Phase 3 territory."""
    db = SessionLocal()
    try:
        job = db.get(AgentJob, uuid.UUID(agent_job_id))
        if job is None:
            return

        job.celery_task_id = self.request.id
        _set_status(db, job, "running", "preparing tenant customization repo")

        tenant = db.get(Tenant, job.tenant_id)
        repo_path = ensure_tenant_repo(tenant.slug)

        _set_status(db, job, "running", "running coding agent")
        runner = get_agent_runner()
        result = runner.run(AgentRunContext(tenant_slug=tenant.slug, repo_path=repo_path, prompt=job.prompt))

        if not result.success:
            raise RuntimeError(result.error or "Agent run reported failure with no error message")

        _set_status(db, job, "running", "capturing diff")
        diff = capture_diff(repo_path, commit_message=f"agent-job {job.id}: {job.prompt[:72]}")

        job.diff = diff
        job.summary = result.summary
        _set_status(db, job, "succeeded", None)

    except GitCommandError as exc:
        db.rollback()
        job = db.get(AgentJob, uuid.UUID(agent_job_id))
        if job is not None:
            _set_status(db, job, "failed", f"{exc}\n\n{exc.output}"[:8000])
        raise
    except Exception as exc:  # noqa: BLE001 - top-level task boundary, must record failure
        db.rollback()
        job = db.get(AgentJob, uuid.UUID(agent_job_id))
        if job is not None:
            _set_status(db, job, "failed", str(exc)[:8000])
        raise
    finally:
        db.close()
