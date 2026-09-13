import json

import pytest

from app.services.agent.base import AgentRunContext
from app.services.agent.openhands_runner import OpenHandsRunner, RESULT_FILENAME
from app.shared.process import CommandResult, CommandTimeoutError


def test_openhands_runner_success_reads_and_clears_result_file(tmp_path, monkeypatch):
    result_path = tmp_path / RESULT_FILENAME

    def fake_run_command(cmd, timeout=None):
        result_path.write_text(json.dumps({"success": True, "summary": "did stuff", "error": None}), encoding="utf-8")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.services.agent.openhands_runner.run_command", fake_run_command)

    runner = OpenHandsRunner()
    result = runner.run(AgentRunContext(tenant_slug="acme-crm", repo_path=tmp_path, prompt="add a button"))

    assert result.success is True
    assert result.summary == "did stuff"
    assert not result_path.exists()


def test_openhands_runner_missing_result_file_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.openhands_runner.run_command",
        lambda cmd, timeout=None: CommandResult(returncode=1, stdout="oops", stderr="bad"),
    )

    runner = OpenHandsRunner()
    result = runner.run(AgentRunContext(tenant_slug="acme-crm", repo_path=tmp_path, prompt="add a button"))

    assert result.success is False
    assert "oops" in result.error


def test_openhands_runner_timeout_is_a_failure(tmp_path, monkeypatch):
    def _boom(cmd, timeout=None):
        raise CommandTimeoutError("timed out after 1800s", "partial output")

    monkeypatch.setattr("app.services.agent.openhands_runner.run_command", _boom)

    runner = OpenHandsRunner()
    result = runner.run(AgentRunContext(tenant_slug="acme-crm", repo_path=tmp_path, prompt="add a button"))

    assert result.success is False
    assert "timed out" in result.error


def test_openhands_runner_clears_stale_result_file_before_running(tmp_path, monkeypatch):
    stale_path = tmp_path / RESULT_FILENAME
    stale_path.write_text(json.dumps({"success": True, "summary": "stale", "error": None}), encoding="utf-8")

    calls = []

    def fake_run_command(cmd, timeout=None):
        calls.append(cmd)
        assert not stale_path.exists()  # cleared before the "container" ran
        stale_path.write_text(json.dumps({"success": True, "summary": "fresh", "error": None}), encoding="utf-8")
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.services.agent.openhands_runner.run_command", fake_run_command)

    runner = OpenHandsRunner()
    result = runner.run(AgentRunContext(tenant_slug="acme-crm", repo_path=tmp_path, prompt="add a button"))

    assert result.summary == "fresh"
    assert len(calls) == 1
