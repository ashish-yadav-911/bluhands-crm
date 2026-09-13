from app.services.agent import repo as repo_module


def test_ensure_tenant_repo_scaffolds_inits_and_commits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_module.settings, "tenant_data_root", str(tmp_path))

    init_calls = []
    commit_calls = []

    monkeypatch.setattr(repo_module.git_runner, "init_repo", lambda path: init_calls.append(path))
    monkeypatch.setattr(repo_module.git_runner, "has_commits", lambda path: len(commit_calls) > 0)
    monkeypatch.setattr(
        repo_module.git_runner, "commit_all", lambda path, message: commit_calls.append((path, message)) or True
    )

    repo_path = repo_module.ensure_tenant_repo("acme-crm")

    assert repo_path == tmp_path / "acme-crm" / "repo"
    assert (repo_path / repo_module.settings.custom_app_name).exists()
    assert len(init_calls) == 1
    assert len(commit_calls) == 1

    # Second call against the same (already git-initialized, already
    # committed) repo must not re-init or re-commit the scaffold.
    (repo_path / ".git").mkdir(exist_ok=True)  # simulate a real git repo existing on disk
    repo_module.ensure_tenant_repo("acme-crm")

    assert len(init_calls) == 1
    assert len(commit_calls) == 1


def test_capture_diff_returns_none_when_nothing_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_module.git_runner, "commit_all", lambda path, message: False)

    result = repo_module.capture_diff(tmp_path, "no-op job")

    assert result is None


def test_capture_diff_returns_diff_when_committed(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_module.git_runner, "commit_all", lambda path, message: True)
    monkeypatch.setattr(repo_module.git_runner, "diff_last_commit", lambda path: "diff --git a/x b/x")

    result = repo_module.capture_diff(tmp_path, "added a thing")

    assert result == "diff --git a/x b/x"
