from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentRunContext:
    tenant_slug: str
    repo_path: Path
    prompt: str


@dataclass
class AgentRunResult:
    success: bool
    summary: str
    error: str | None = None


class AgentRunner(ABC):
    """Pluggable coding-agent interface. `run` is handed a checked-out
    per-tenant custom-app repo and a customization prompt, and is expected
    to make its edits directly on disk under `context.repo_path` -- the
    caller (agent_tasks.py) captures whatever changed as a git diff
    afterwards. Swapping in a real agent (OpenHands SDK, a custom LLM loop,
    etc.) later means adding a new AgentRunner subclass and pointing
    app.services.agent.factory at it -- no orchestration code changes."""

    @abstractmethod
    def run(self, context: AgentRunContext) -> AgentRunResult: ...
