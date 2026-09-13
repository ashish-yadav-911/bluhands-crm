import uuid

from app.celery_app import celery_app
from app.db.base import SessionLocal
from app.models.application import Application
from app.models.deployment import Deployment
from app.models.tenant import Tenant
from app.services.provisioning import docker_runner, frappe_bootstrap, proxy_registrar
from app.services.provisioning.compose_writer import TenantComposeContext, render_compose_file
from app.services.provisioning.naming import (
    allocate_http_port,
    compose_project_name,
    generate_secret,
    site_name as build_site_name,
)

REQUIRED_HEALTHY_SERVICES = ["mariadb", "backend"]


def _set_status(db, deployment: Deployment, status: str, detail: str | None = None) -> None:
    deployment.status = status
    deployment.status_detail = detail
    db.commit()


@celery_app.task(bind=True)
def provision_tenant_task(self, deployment_id: str, admin_password: str) -> None:
    """End-to-end tenant provisioning: render compose -> docker up -> wait
    for health -> bootstrap the Frappe site -> register routing -> mark
    running. Any failure at any step sets status=failed with the failing
    step + error captured in status_detail; partial resources are left in
    place for debugging rather than auto-torn-down (v1 tradeoff)."""
    db = SessionLocal()
    try:
        deployment = db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None:
            return

        deployment.celery_task_id = self.request.id
        _set_status(db, deployment, "provisioning", "allocating resources")

        tenant = db.get(Tenant, deployment.tenant_id)
        application = db.get(Application, tenant.application_id)

        project_name = compose_project_name(tenant.slug)
        http_port = allocate_http_port(db)
        site = build_site_name(tenant.slug)
        mariadb_root_password = generate_secret()

        deployment.compose_project_name = project_name
        deployment.http_port = http_port
        deployment.site_name = site
        db.commit()

        _set_status(db, deployment, "provisioning", "rendering tenant compose file")
        ctx = TenantComposeContext(
            tenant_slug=tenant.slug,
            compose_project_name=project_name,
            site_name=site,
            http_port=http_port,
            image_version=application.default_version,
            mariadb_root_password=mariadb_root_password,
            admin_password=admin_password,
        )
        compose_path = render_compose_file(application.compose_template_path, ctx)

        _set_status(db, deployment, "provisioning", "pulling container images (first run can take a while)")
        docker_runner.compose_pull(project_name, compose_path)

        _set_status(db, deployment, "provisioning", "starting containers")
        docker_runner.compose_up(project_name, compose_path)

        _set_status(db, deployment, "provisioning", "waiting for containers to become healthy")
        docker_runner.wait_for_healthy(project_name, compose_path, REQUIRED_HEALTHY_SERVICES)

        _set_status(db, deployment, "provisioning", "bootstrapping Frappe site (bench new-site)")
        frappe_bootstrap.create_site(
            project_name,
            compose_path,
            site_name=site,
            mariadb_root_password=mariadb_root_password,
            admin_password=admin_password,
        )

        _set_status(db, deployment, "provisioning", "registering tenant route")
        proxy_registrar.register_tenant_route(tenant.slug, site, http_port)

        deployment.url = f"http://{site}"
        _set_status(db, deployment, "running", None)

    except docker_runner.DockerCommandError as exc:
        db.rollback()
        deployment = db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is not None:
            _set_status(db, deployment, "failed", f"{exc}\n\n{exc.output}"[:8000])
        raise
    except Exception as exc:  # noqa: BLE001 - top-level task boundary, must record failure
        db.rollback()
        deployment = db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is not None:
            _set_status(db, deployment, "failed", str(exc)[:8000])
        raise
    finally:
        db.close()
