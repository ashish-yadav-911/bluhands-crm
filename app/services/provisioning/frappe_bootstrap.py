"""DORMANT -- hosting_mode="dedicated" only.

Superseded for shared-bench tenants by app/services/frappe/bench_client.py
and app/services/provisioning/site_setup.py, which run bench against the
shared backend container and scope every command with --site.

Kept, not deleted: a tenant needing real server-side code has to move to
its own stack (installing a custom app on a shared bench would load that
tenant's Python into workers serving everyone), and this is the path that
provisions one.
"""

from pathlib import Path

from app.services.provisioning.docker_runner import DockerCommandError, compose_exec


def create_site(
    project_name: str,
    compose_path: Path,
    site_name: str,
    mariadb_root_password: str,
    admin_password: str,
) -> None:
    """Runs `bench new-site` inside the backend container to bootstrap the
    Frappe/ERPNext site. This is the slowest and most failure-prone step, so
    callers should capture DockerCommandError.output verbatim into the
    deployment's status_detail for debugging."""
    result = compose_exec(
        project_name,
        compose_path,
        service="backend",
        command=[
            "bench",
            "new-site",
            site_name,
            "--mariadb-root-password",
            mariadb_root_password,
            "--admin-password",
            admin_password,
            "--install-app",
            "erpnext",
            "--no-mariadb-socket",
        ],
        timeout=900,
    )
    if result.returncode != 0:
        raise DockerCommandError(
            f"bench new-site failed for site {site_name}",
            result.stdout + result.stderr,
        )
