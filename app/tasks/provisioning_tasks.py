import uuid

from app.celery_app import celery_app
from app.db.base import SessionLocal
from app.models.bench import Bench
from app.models.deployment import Deployment
from app.models.organization import Organization
from app.models.tenant import Tenant
from app.models.tenant_credential import TenantCredential
from app.services import crypto
from app.services.frappe.bench_client import BenchClient, BenchCommandError
from app.services.frappe.rest_client import FrappeClient
from app.services.provisioning import docker_runner, site_setup
from app.services.provisioning.locks import site_creation_lock
from app.services.runtime import shared_bench_runtime

# The shared bench is brought up once as infrastructure, not per tenant, so
# this is a precondition check rather than a provisioning step. mariadb
# declares a healthcheck; backend deliberately does not (see the compose
# file), so "running" is the strongest signal available for it.
REQUIRED_BENCH_SERVICES = ["mariadb", "backend"]


def _set_status(db, deployment: Deployment, status: str, detail: str | None = None) -> None:
    deployment.status = status
    deployment.status_detail = detail
    db.commit()


def _store_credentials(db, tenant_id, admin_password: str, api_key: str, api_secret: str) -> None:
    """Persist both credentials encrypted.

    Frappe hands back an API secret exactly once, so this has to happen in
    the same task that minted it -- there is no way to re-read it later,
    only to re-mint.
    """
    db.query(TenantCredential).filter_by(tenant_id=tenant_id).delete()
    db.add(
        TenantCredential(
            tenant_id=tenant_id,
            kind="admin",
            username="Administrator",
            password_encrypted=crypto.encrypt(admin_password),
        )
    )
    db.add(
        TenantCredential(
            tenant_id=tenant_id,
            kind="api",
            username="Administrator",
            api_key=api_key,
            api_secret_encrypted=crypto.encrypt(api_secret),
        )
    )
    db.commit()


@celery_app.task(bind=True)
def provision_tenant_task(self, deployment_id: str, admin_password: str) -> None:
    """Create and prepare one tenant's site on the shared bench.

    bench new-site -> setup wizard -> API credentials -> reachability check.
    Measured at roughly 3.7 minutes end to end, of which site creation is
    ~3 minutes; the rest is under a minute.

    On failure the site is DROPPED rather than left for inspection. That
    inverts the previous behaviour and is deliberate: a half-built site on a
    shared bench still consumes a database and a slot, the site is seconds
    old and holds nothing, and a clean retry is worth more than the wreckage.
    The failing output is preserved in status_detail either way.
    """
    db = SessionLocal()
    runtime = None
    site_created = False

    try:
        deployment = db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None:
            return

        deployment.celery_task_id = self.request.id
        _set_status(db, deployment, "provisioning", "checking shared bench")

        tenant = db.get(Tenant, deployment.tenant_id)
        organization = db.get(Organization, tenant.organization_id)
        bench_row = db.get(Bench, tenant.bench_id)

        runtime = shared_bench_runtime(tenant.site_name)
        docker_runner.wait_for_healthy(
            runtime.compose_project,
            runtime.compose_path,
            REQUIRED_BENCH_SERVICES,
            timeout_seconds=300,
            env_file=runtime.env_file,
        )

        bench = BenchClient(runtime)
        admin_email = _owner_email(db, organization)

        # Queued tenants wait here rather than building in parallel; see
        # locks.py for why. Everything that hammers MariaDB is under it.
        _set_status(db, deployment, "provisioning", "waiting for a build slot")
        with site_creation_lock():
            _set_status(db, deployment, "provisioning", "creating site (this is the slow step, ~3 min)")
            bench.new_site(admin_password=admin_password)
            site_created = True

            _set_status(db, deployment, "provisioning", "completing setup wizard")
            site_setup.complete_setup_wizard(
                bench,
                company_name=organization.name,
                admin_email=admin_email,
                admin_password=admin_password,
            )

            _set_status(db, deployment, "provisioning", "issuing API credentials")
            api_key, api_secret = site_setup.mint_api_keys(bench)
            _store_credentials(db, tenant.id, admin_password, api_key, api_secret)

        _set_status(db, deployment, "provisioning", "verifying the site responds")
        with FrappeClient.for_runtime(runtime, api_key, api_secret) as client:
            if not client.ping():
                raise RuntimeError(f"Site {tenant.site_name} did not respond to ping after provisioning")

        deployment.site_name = tenant.site_name
        deployment.url = runtime.public_url
        if bench_row is not None:
            deployment.bench_id = bench_row.id
        _set_status(db, deployment, "running", None)

    except (BenchCommandError, docker_runner.DockerCommandError, site_setup.SiteSetupError) as exc:
        _fail(db, deployment_id, f"{exc}\n\n{getattr(exc, 'output', '')}"[:8000], runtime, site_created)
        raise
    except Exception as exc:  # noqa: BLE001 - top-level task boundary, must record failure
        _fail(db, deployment_id, str(exc)[:8000], runtime, site_created)
        raise
    finally:
        db.close()


def _owner_email(db, organization: Organization) -> str:
    from app.models.user import User

    owner = db.query(User).filter_by(organization_id=organization.id).order_by(User.created_at).first()
    return owner.email if owner else f"admin@{organization.slug}.local"


def _fail(db, deployment_id: str, detail: str, runtime, site_created: bool) -> None:
    db.rollback()

    if site_created and runtime is not None:
        # Best-effort: a cleanup failure must never mask the real error that
        # brought us here, so it is appended to the detail rather than raised.
        try:
            BenchClient(runtime).drop_site()
        except Exception as cleanup_exc:  # noqa: BLE001
            detail = f"{detail}\n\n[cleanup] could not drop site: {cleanup_exc}"[:8000]

    deployment = db.get(Deployment, uuid.UUID(deployment_id))
    if deployment is not None:
        _set_status(db, deployment, "failed", detail)
