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
