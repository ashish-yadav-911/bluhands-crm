"""Runs `bench` commands inside the bench's backend container.

Deliberately narrow. The applier does its work over the REST API, not
through here -- see rest_client for why. This client covers only what has
no REST equivalent: creating and dropping sites, installing apps, editing
site config, minting API keys, invoking whitelisted methods before any
credentials exist, and cache/backup maintenance.
"""

from __future__ import annotations

from app.config import settings
from app.services.provisioning.docker_runner import DockerCommandError, compose_exec
from app.services.runtime import TenantRuntime
from app.shared.process import CommandResult

# `bench --site all <cmd>` fans a command out across every site on the
# bench. On a dedicated stack that is merely broad; on a shared bench it is
# a multi-tenant outage. No caller here ever wants it, so it is rejected
# rather than left available.
_FORBIDDEN_SITES = {"all"}


class BenchCommandError(RuntimeError):
    def __init__(self, message: str, output: str):
        super().__init__(message)
        self.output = output


class BenchClient:
    def __init__(self, runtime: TenantRuntime):
        self.runtime = runtime

    def _exec(self, args: list[str], timeout: int) -> CommandResult:
        return compose_exec(
            self.runtime.compose_project,
            self.runtime.compose_path,
            service=self.runtime.backend_service,
            command=args,
            timeout=timeout,
            env_file=self.runtime.env_file,
        )

    def run(self, args: list[str], timeout: int = 600, check: bool = True) -> CommandResult:
        """Run `bench --site <site> <args>`."""
        site = (self.runtime.site_name or "").strip()
        if not site:
            raise ValueError("BenchClient requires a site name; refusing to run un-scoped bench")
        if site in _FORBIDDEN_SITES:
            raise ValueError(f"Refusing to run bench against the reserved site name {site!r}")

        result = self._exec(["bench", "--site", site, *args], timeout=timeout)
        if check and result.returncode != 0:
            raise BenchCommandError(
                f"bench --site {site} {' '.join(args)} failed (exit {result.returncode})",
                result.stdout + result.stderr,
            )
        return result

    def run_global(self, args: list[str], timeout: int = 600, check: bool = True) -> CommandResult:
        """Run a bench command with no `--site`, for bench-wide operations.

        Separate from `run` so that omitting the site is always a deliberate
        choice at the call site rather than an accident. Note that anything
        this writes with `set-config -g` lands in common_site_config.json and
        therefore affects every tenant.
        """
        result = self._exec(["bench", *args], timeout=timeout)
        if check and result.returncode != 0:
            raise BenchCommandError(
                f"bench {' '.join(args)} failed (exit {result.returncode})",
                result.stdout + result.stderr,
            )
        return result

    # --- site lifecycle -------------------------------------------------

    def new_site(self, admin_password: str, install_apps: tuple[str, ...] = ("erpnext",)) -> None:
        """Create the site. This is the slow step -- it builds ~1000 tables
        and, on a small box, can take far longer than the rest of
        provisioning combined, which is why it gets its own timeout budget."""
        args = [
            "new-site",
            self.runtime.site_name,
            "--db-root-password",
            settings.shared_bench_db_root_password,
            "--admin-password",
            admin_password,
            "--no-mariadb-socket",
        ]
        for app in install_apps:
            args += ["--install-app", app]

        # new-site takes the site as a positional argument, not via --site.
        result = self._exec(["bench", *args], timeout=settings.site_create_timeout_seconds)
        if result.returncode != 0:
            raise BenchCommandError(
                f"bench new-site {self.runtime.site_name} failed (exit {result.returncode})",
                result.stdout + result.stderr,
            )

    def drop_site(self) -> None:
        """Destroy the site and its database.

        This is the rollback for a *failed initial build*: the site is
        seconds old and holds nothing, so dropping it is clean and total.
        Never call it on a tenant that has data -- there, the rollback is a
        restore from the backup taken before the change.
        """
        self.run(
            [
                "drop-site",
                "--db-root-password",
                settings.shared_bench_db_root_password,
                "--force",
                "--no-backup",
            ],
            timeout=600,
        )

    def set_site_config(self, key: str, value: str) -> None:
        self.run(["set-config", key, value], timeout=120)

    def clear_cache(self) -> None:
        self.run(["clear-cache"], timeout=300)

    def backup(self) -> CommandResult:
        return self.run(["backup"], timeout=1200)

    def execute(self, dotted_path: str, args: str | None = None, kwargs: str | None = None) -> CommandResult:
        """`bench execute` a whitelisted/importable function.

        Used before API credentials exist -- completing the setup wizard and
        minting the first API key both have to happen before the REST client
        can authenticate at all.
        """
        cmd = ["execute", dotted_path]
        if args is not None:
            cmd += ["--args", args]
        if kwargs is not None:
            cmd += ["--kwargs", kwargs]
        return self.run(cmd, timeout=900)
