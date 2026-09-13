from app.config import settings
from app.services.agent.base import AgentRunner
from app.services.agent.openhands_runner import OpenHandsRunner
from app.services.agent.stub_runner import StubAgentRunner


def get_agent_runner() -> AgentRunner:
    """Single switch point for which AgentRunner implementation is active."""
    if settings.agent_runner == "stub":
        return StubAgentRunner()
    if settings.agent_runner == "openhands":
        return OpenHandsRunner()
    raise ValueError(f"Unknown AGENT_RUNNER: {settings.agent_runner}")
