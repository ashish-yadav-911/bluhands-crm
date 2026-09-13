from app.services.agent.base import AgentRunContext
from app.services.agent.stub_runner import StubAgentRunner


def test_stub_runner_writes_log_and_succeeds(tmp_path):
    runner = StubAgentRunner()
    context = AgentRunContext(tenant_slug="acme-crm", repo_path=tmp_path, prompt="Add a WhatsApp button")

    result = runner.run(context)

    assert result.success is True
    assert "Stub agent" in result.summary

    log_path = tmp_path / "customization_requests.log"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "acme-crm" in content
    assert "Add a WhatsApp button" in content
