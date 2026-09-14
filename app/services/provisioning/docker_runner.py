import time
from pathlib import Path

from app.shared.process import CommandResult, CommandTimeoutError, run_command


class DockerCommandError(RuntimeError):
    def __init__(self, message: str, output: str):
        super().__init__(message)
        self.output = output


def _run(args: list[str], cwd: Path | None = None, timeout: int | None = None) -> CommandResult:
    try:
        return run_command(args, cwd=cwd, timeout=timeout)
    except CommandTimeoutError as exc:
        raise DockerCommandError(str(exc), exc.output) from None


def _compose(project_name: str, compose_path: Path, env_file: Path | None = None) -> list[str]:
    """The `docker compose ...` prefix shared by every call below.

    `--env-file` is not optional in practice for the shared bench: its
    compose file declares a required variable (the MariaDB root password),
    so *every* subcommand -- `ps` and `exec` just as much as `up` -- fails
    interpolation without it. Compose only auto-loads a `.env` sitting in
    the project directory, which is `docker/` here, not the repo root, so
    the path is passed explicitly rather than relied upon.
    """
    args = ["docker", "compose"]
    if env_file is not None:
        args += ["--env-file", str(env_file)]
    return args + ["-p", project_name, "-f", str(compose_path)]


def compose_pull(
    project_name: str, compose_path: Path, timeout: int = 1800, env_file: Path | None = None
) -> None:
    """Separate from compose_up so a slow/large first-time image pull (the
    real bottleneck for ERPNext's multi-GB image) gets generous headroom,
    while `up` itself -- once images are already local -- can stay fast."""
    result = _run(
        _compose(project_name, compose_path, env_file) + ["pull"],
        cwd=compose_path.parent,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise DockerCommandError(
            f"docker compose pull failed for project {project_name}",
            result.stdout + result.stderr,
        )


def compose_up(project_name: str, compose_path: Path, env_file: Path | None = None) -> None:
    result = _run(
        _compose(project_name, compose_path, env_file) + ["up", "-d"],
        cwd=compose_path.parent,
        timeout=180,
    )
    if result.returncode != 0:
        raise DockerCommandError(
            f"docker compose up failed for project {project_name}",
            result.stdout + result.stderr,
        )


def compose_ps_status(project_name: str, compose_path: Path, env_file: Path | None = None) -> str:
    result = _run(
        _compose(project_name, compose_path, env_file) + ["ps", "--format", "json"],
        cwd=compose_path.parent,
        timeout=30,
    )
    if result.returncode != 0:
        raise DockerCommandError(
            f"docker compose ps failed for project {project_name}",
            result.stdout + result.stderr,
        )
    return result.stdout


def wait_for_healthy(
    project_name: str,
    compose_path: Path,
    required_services: list[str],
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    env_file: Path | None = None,
) -> None:
    """Bounded poll of `docker compose ps` until every required service
    reports state=running and (if it declares one) health=healthy."""
    import json

    deadline = time.monotonic() + timeout_seconds
    last_status = ""

    while time.monotonic() < deadline:
        raw = compose_ps_status(project_name, compose_path, env_file=env_file)
        last_status = raw
        try:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except json.JSONDecodeError:
            rows = []

        by_service = {row.get("Service"): row for row in rows}
        all_ready = True
        for service in required_services:
            row = by_service.get(service)
            if row is None:
                all_ready = False
                break
            state = row.get("State", "").lower()
            health = row.get("Health", "").lower()
            if state != "running" or (health and health != "healthy"):
                all_ready = False
                break

        if all_ready:
            return

        time.sleep(poll_interval)

    raise DockerCommandError(
        f"Timed out waiting for services {required_services} to become healthy "
        f"for project {project_name}",
        last_status,
    )


def compose_exec(
    project_name: str,
    compose_path: Path,
    service: str,
    command: list[str],
    timeout: int = 600,
    env_file: Path | None = None,
) -> CommandResult:
    result = _run(
        _compose(project_name, compose_path, env_file) + ["exec", "-T", service, *command],
        cwd=compose_path.parent,
        timeout=timeout,
    )
    return result
