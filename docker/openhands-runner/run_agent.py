"""Entrypoint for the bluhands-agent-runner image. Runs entirely inside its
own container, mounted only against one tenant's customization repo at
/workspace -- it never sees the control-plane host, other tenants' data, or
any credentials beyond the LLM key it was given.

Communicates its outcome back to the host via a JSON file rather than the
process exit code, since the host side (app/services/agent/openhands_runner.py)
needs a summary string either way and a single well-known file is simpler
to parse reliably than trying to infer success from exit codes / stderr.
"""

import json
import os
import traceback
from pathlib import Path

WORKSPACE = Path("/workspace")
RESULT_PATH = WORKSPACE / ".agent_result.json"
SUMMARY_PATH = WORKSPACE / ".agent_summary.txt"

SUMMARY_INSTRUCTION = (
    "\n\nWhen you are completely done, write a short (2-4 sentence) summary "
    "of exactly what you changed to a file at .agent_summary.txt in the "
    "repo root (do not include this file in any commit message context, "
    "it is only for the operator reading job status)."
)


def write_result(success: bool, summary: str, error: str | None) -> None:
    RESULT_PATH.write_text(
        json.dumps({"success": success, "summary": summary, "error": error}),
        encoding="utf-8",
    )


def main() -> None:
    prompt = os.environ.get("PROMPT", "").strip()
    if not prompt:
        write_result(False, "", "No PROMPT provided to agent runner")
        return

    model = os.environ["LLM_MODEL"]
    api_key = os.environ["LLM_API_KEY"]
    base_url = os.environ.get("LLM_BASE_URL") or None

    if SUMMARY_PATH.exists():
        SUMMARY_PATH.unlink()

    try:
        from openhands.sdk import LLM, Agent, Conversation, Tool
        from openhands.tools.file_editor import FileEditorTool
        from openhands.tools.task_tracker import TaskTrackerTool
        from openhands.tools.terminal import TerminalTool

        llm = LLM(model=model, api_key=api_key, base_url=base_url)
        agent = Agent(
            llm=llm,
            tools=[
                Tool(name=TerminalTool.name),
                Tool(name=FileEditorTool.name),
                Tool(name=TaskTrackerTool.name),
            ],
        )

        conversation = Conversation(agent=agent, workspace=str(WORKSPACE))
        conversation.send_message(prompt + SUMMARY_INSTRUCTION)
        conversation.run()

    except Exception as exc:  # noqa: BLE001 - top-level entrypoint boundary
        write_result(False, "", f"{exc}\n\n{traceback.format_exc()}"[:8000])
        return

    summary = (
        SUMMARY_PATH.read_text(encoding="utf-8").strip()
        if SUMMARY_PATH.exists()
        else "Agent run completed but did not write a summary file."
    )
    if SUMMARY_PATH.exists():
        SUMMARY_PATH.unlink()

    write_result(True, summary, None)


if __name__ == "__main__":
    main()
