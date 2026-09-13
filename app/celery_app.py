from celery import Celery

from app.config import settings

celery_app = Celery(
    "bluhands_crm",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.provisioning_tasks", "app.tasks.agent_tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Agent jobs (a real coding-agent run can take a long time) get their
    # own queue so they can never block tenant provisioning behind them --
    # run a dedicated `celery worker -Q agent_jobs` process to consume it.
    task_routes={
        "app.tasks.agent_tasks.run_agent_job_task": {"queue": "agent_jobs"},
    },
)
