from pathlib import Path

from app.config import settings
from app.services.agent import git_runner
from app.services.agent.scaffold import scaffold_custom_app


def tenant_repo_path(tenant_slug: str) -> Path:
    # Resolved to absolute -- git_runner shells out with cwd set to this
    # directory, so keeping it absolute avoids the same doubled-path class
    # of bug a relative tenant_data_root caused in the provisioning path.
    return Path(settings.tenant_data_root).resolve() / tenant_slug / "repo"


def ensure_tenant_repo(tenant_slug: str) -> Path:
    """Returns the tenant's custom-app repo, scaffolding and git-initing it
    on first use. Idempotent -- safe to call at the start of every agent
    job, not just the first one for a tenant."""
    repo_path = tenant_repo_path(tenant_slug)
    is_new = not repo_path.exists()
    repo_path.mkdir(parents=True, exist_ok=True)

    scaffold_custom_app(repo_path)

    if not (repo_path / ".git").exists():
        git_runner.init_repo(repo_path)

    if is_new or not git_runner.has_commits(repo_path):
        git_runner.commit_all(repo_path, "Initial custom app scaffold")

    return repo_path


def capture_diff(repo_path: Path, commit_message: str) -> str | None:
    """Commits whatever the agent run changed and returns the diff for
    that commit, or None if the run made no changes at all."""
    committed = git_runner.commit_all(repo_path, commit_message)
    if not committed:
        return None
    return git_runner.diff_last_commit(repo_path)
