"""Run with `python -m app.seed` after migrations. Idempotent: safe to re-run.

Registers the provisionable application types and the shared bench the
control plane provisions sites onto. The bench must already be up:

    docker compose --env-file .env -f docker/shared-bench-compose.yml \
        -p bluhands-shared-bench up -d
"""

from app.config import settings
from app.db.base import SessionLocal
from app.models.application import Application
from app.models.bench import Bench

# Pinned by digest in the compose file; recorded here so a tenant carries
# the version its site was built against and a bench upgrade is visible.
DEFAULT_IMAGE_VERSION = "erpnext-15.120.0/frappe-15.118.0"


def seed_applications(db) -> None:
    if db.query(Application).filter_by(key="erpnext").first() is None:
        db.add(
            Application(
                key="erpnext",
                display_name="ERPNext",
                # Only consulted in hosting_mode="dedicated". Shared-bench
                # tenants are sites on an already-running bench, so nothing
                # is rendered per tenant.
                compose_template_path="dedicated-tenant-compose.yml.j2",
                default_version="version-15",
                is_active=True,
            )
        )


def seed_default_bench(db) -> None:
    if db.query(Bench).filter_by(key="default").first() is not None:
        return
    db.add(
        Bench(
            key="default",
            compose_project_name=settings.shared_bench_project,
            compose_file_path=settings.shared_bench_compose,
            backend_service=settings.shared_bench_backend_service,
            frontend_http_port=settings.shared_bench_http_port,
            base_domain=settings.base_domain,
            image_version=DEFAULT_IMAGE_VERSION,
            status="ready",
            max_sites=25,
            is_default=True,
        )
    )


def seed_all() -> None:
    db = SessionLocal()
    try:
        seed_applications(db)
        seed_default_bench(db)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_all()
    print("Seeded application registry and default bench.")
