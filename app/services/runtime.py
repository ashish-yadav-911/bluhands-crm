"""Resolves a Tenant to the concrete place its ERPNext site actually lives.

Every component that touches a tenant's site -- the provisioning task, the
bench client, the REST client, the applier, the verifier -- asks this module
rather than reading settings directly. That indirection is the whole point:
today there is one hosting mode (a shared bench, many sites, routed by Host
header), but adding a per-tenant dedicated stack later is one branch here
instead of a change in every caller. It is also why the dedicated-mode
machinery (compose_writer, allocate_http_port, the per-tenant compose
template) is kept in the tree rather than deleted.
"""

from dataclasses import dataclass
from pathlib import Path

from app.config import settings

SHARED_BENCH = "shared_bench"
DEDICATED = "dedicated"


def _project_root() -> Path:
    # app/services/runtime.py -> app/services -> app -> repo root
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TenantRuntime:
    """Everything needed to reach one tenant's site, in either hosting mode."""

    site_name: str
    hosting_mode: str

    # How to reach the site over HTTP. In shared-bench mode every tenant
    # shares one base URL and is distinguished purely by `host_header` --
    # which is also why the applier never depends on DNS resolving
    # *.tenants.localhost: it connects to 127.0.0.1 and sets Host itself.
    rest_base_url: str
    host_header: str

    # How to run bench commands against the site.
    compose_project: str
    compose_path: Path
    backend_service: str
    env_file: Path | None

    @property
    def public_url(self) -> str:
        """What the customer types. Needs DNS (or a hosts entry) to resolve,
        unlike `rest_base_url`."""
        return f"http://{self.host_header}"


def resolve(tenant, deployment=None) -> TenantRuntime:
    """Build the runtime for a tenant. `deployment` is accepted for the
    dedicated mode that will need its per-tenant port and project name."""
    hosting_mode = getattr(tenant, "hosting_mode", SHARED_BENCH) or SHARED_BENCH

    if hosting_mode == SHARED_BENCH:
        return shared_bench_runtime(tenant.site_name)

    raise NotImplementedError(
        f"hosting_mode={hosting_mode!r} is not implemented yet. The seam is here: "
        "resurrect compose_writer + naming.allocate_http_port and return a runtime "
        "pointing at the tenant's own compose project."
    )


def shared_bench_runtime(site_name: str) -> TenantRuntime:
    """The runtime for a site on the shared bench. Also used before a Tenant
    row exists -- during provisioning, the site is created before anything
    is persisted."""
    root = _project_root()
    env_file = root / ".env"

    return TenantRuntime(
        site_name=site_name,
        hosting_mode=SHARED_BENCH,
        rest_base_url=f"http://127.0.0.1:{settings.shared_bench_http_port}",
        host_header=site_name,
        compose_project=settings.shared_bench_project,
        compose_path=(root / settings.shared_bench_compose).resolve(),
        backend_service=settings.shared_bench_backend_service,
        env_file=env_file if env_file.exists() else None,
    )
