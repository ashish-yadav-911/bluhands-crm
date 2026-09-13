from datetime import datetime, timezone

from app.services.agent.base import AgentRunContext, AgentRunner, AgentRunResult


class StubAgentRunner(AgentRunner):
    """Placeholder AgentRunner that proves the orchestration pipeline
    (repo checkout -> run -> diff capture -> status tracking) without
    depending on an LLM API key or agent framework yet. It makes a real,
    deterministic file change so a real diff is produced end-to-end; it
    does not attempt to interpret the prompt as an actual code change."""

    def run(self, context: AgentRunContext) -> AgentRunResult:
        log_path = context.repo_path / "customization_requests.log"
        timestamp = datetime.now(timezone.utc).isoformat()
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] tenant={context.tenant_slug} prompt={context.prompt!r}\n")

        return AgentRunResult(
            success=True,
            summary=(
                "Stub agent: recorded the customization request in "
                "customization_requests.log. No real code generation yet -- "
                "replace StubAgentRunner with a real AgentRunner to make "
                "actual changes."
            ),
        )
