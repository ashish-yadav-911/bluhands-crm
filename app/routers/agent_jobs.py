import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.agent_job import AgentJob
from app.models.tenant import Tenant
from app.models.user import User
from app.routers.deps import get_current_user
from app.schemas.agent_job import AgentJobCreate, AgentJobRead
from app.services.agent_job_service import create_agent_job
from app.tasks.agent_tasks import run_agent_job_task

router = APIRouter(tags=["agent-jobs"])


def _get_owned_tenant(db: Session, tenant_id: uuid.UUID, current_user: User) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


@router.post(
    "/tenants/{tenant_id}/agent-jobs",
    response_model=AgentJobRead,
    status_code=status.HTTP_201_CREATED,
)
def create_tenant_agent_job(
    tenant_id: uuid.UUID,
    payload: AgentJobCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentJob:
    _get_owned_tenant(db, tenant_id, current_user)

    job = create_agent_job(db, tenant_id=tenant_id, created_by_user_id=current_user.id, prompt=payload.prompt)
    run_agent_job_task.delay(str(job.id))
    return job


@router.get("/agent-jobs/{agent_job_id}", response_model=AgentJobRead)
def get_agent_job(
    agent_job_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentJob:
    job = db.get(AgentJob, agent_job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent job not found")

    _get_owned_tenant(db, job.tenant_id, current_user)
    return job
