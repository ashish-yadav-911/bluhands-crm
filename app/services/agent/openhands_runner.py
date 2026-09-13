import json

from app.config import settings
from app.services.agent.base import AgentRunContext, AgentRunner, AgentRunResult
from app.shared.process import CommandTimeoutError, run_command

RESULT_FILENAME = ".agent_result.json"


class OpenHandsRunner(AgentRunner):
    """Runs the real OpenHands coding agent, entirely inside its own
    Docker container (see docker/openhands-runner/) -- never in this
    Celery worker's own process. This gets us two things at once: the
    agent's arbitrary shell/file operations are sandboxed away from the
    control-plane host, and openhands-sdk's Python >=3.12 requirement
    doesn't force the control plane itself onto a newer Python."""

    def run(self, context: AgentRunContext) -> AgentRunResult:
        result_path = context.repo_path / RESULT_FILENAME
        if result_path.exists():
            result_path.unlink()

        cmd = [
            "docker",
            "run",
            "--rm",
            "--add-host",
            "host.docker.internal:host-gateway",
            "-v",
            f"{context.repo_path}:/workspace",
            "-e",
            f"PROMPT={context.prompt}",
            "-e",
            f"LLM_MODEL={settings.llm_model}",
            "-e",
            f"LLM_API_KEY={settings.llm_api_key}",
        ]
        if settings.llm_base_url:
            cmd += ["-e", f"LLM_BASE_URL={settings.llm_base_url}"]
        cmd.append(settings.agent_runner_image)

        try:
            result = run_command(cmd, timeout=settings.agent_job_timeout_seconds)
        except CommandTimeoutError as exc:
            return AgentRunResult(success=False, summary="", error=str(exc))

        if not result_path.exists():
            return AgentRunResult(
                success=False,
                summary="",
                error=(
                    "Agent container exited without producing a result file "
                    f"(exit {result.returncode}).\n\n{result.stdout}\n{result.stderr}"
                )[:4000],
            )

        data = json.loads(result_path.read_text(encoding="utf-8"))
        result_path.unlink()

        return AgentRunResult(
            success=data["success"],
            summary=data.get("summary", ""),
            error=data.get("error"),
        )
