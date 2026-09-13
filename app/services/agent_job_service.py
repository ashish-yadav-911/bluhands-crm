import uuid

from sqlalchemy.orm import Session

from app.models.agent_job import AgentJob


def create_agent_job(db: Session, tenant_id: uuid.UUID, created_by_user_id: uuid.UUID, prompt: str) -> AgentJob:
    job = AgentJob(
        tenant_id=tenant_id,
        created_by_user_id=created_by_user_id,
        prompt=prompt,
        status="pending",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job
