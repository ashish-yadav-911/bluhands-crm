from pathlib import Path

from app.shared.process import CommandResult, CommandTimeoutError, run_command


class GitCommandError(RuntimeError):
    def __init__(self, message: str, output: str):
        super().__init__(message)
        self.output = output


def _run(args: list[str], cwd: Path) -> CommandResult:
    try:
        return run_command(["git", *args], cwd=cwd, timeout=60)
    except CommandTimeoutError as exc:
        raise GitCommandError(str(exc), exc.output) from None


def init_repo(repo_path: Path) -> None:
    result = _run(["init"], cwd=repo_path)
    if result.returncode != 0:
        raise GitCommandError(f"git init failed in {repo_path}", result.stdout + result.stderr)

    # A bare `git init` has no identity configured on a fresh machine/CI
    # box; commits made by the agent pipeline are attributed to a fixed
    # service identity rather than depending on global git config.
    for key, value in (("user.email", "agent-pipeline@bluhands.local"), ("user.name", "bluhands agent pipeline")):
        result = _run(["config", key, value], cwd=repo_path)
        if result.returncode != 0:
            raise GitCommandError(f"git config {key} failed in {repo_path}", result.stdout + result.stderr)


def commit_all(repo_path: Path, message: str) -> bool:
    """Stages and commits every change under repo_path. Returns False (no
    error, no commit) if there was nothing to commit."""
    add_result = _run(["add", "-A"], cwd=repo_path)
    if add_result.returncode != 0:
        raise GitCommandError(f"git add failed in {repo_path}", add_result.stdout + add_result.stderr)

    status_result = _run(["status", "--porcelain"], cwd=repo_path)
    if not status_result.stdout.strip():
        return False

    commit_result = _run(["commit", "-m", message], cwd=repo_path)
    if commit_result.returncode != 0:
        raise GitCommandError(
            f"git commit failed in {repo_path}", commit_result.stdout + commit_result.stderr
        )
    return True


def diff_last_commit(repo_path: Path) -> str:
    result = _run(["show", "--no-color", "HEAD"], cwd=repo_path)
    if result.returncode != 0:
        raise GitCommandError(f"git show HEAD failed in {repo_path}", result.stdout + result.stderr)
    return result.stdout


def has_commits(repo_path: Path) -> bool:
    result = _run(["rev-parse", "--verify", "HEAD"], cwd=repo_path)
    return result.returncode == 0
