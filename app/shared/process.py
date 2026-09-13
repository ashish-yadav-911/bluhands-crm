import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class CommandTimeoutError(RuntimeError):
    def __init__(self, message: str, output: str):
        super().__init__(message)
        self.output = output


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _kill_process_tree(pid: int) -> None:
    """Popen.kill() alone can leave a subprocess's own child processes
    alive on Windows (observed with `docker compose` first-time image
    pulls), which then keep the stdout/stderr pipes open -- so
    communicate() hangs waiting for them to close even though the
    top-level process was "killed". taskkill /T kills the whole tree."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        import os
        import signal

        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_command(args: list[str], cwd: Path | None = None, timeout: int | None = None) -> CommandResult:
    """Shared subprocess wrapper for every CLI we shell out to (docker,
    docker compose, git, ...). Used instead of subprocess.run(timeout=...)
    directly because that alone doesn't reliably kill the full process
    tree on Windows -- see _kill_process_tree."""
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Every CLI we shell into (docker, git, bench-inside-a-container)
        # emits UTF-8, but text=True alone decodes with the locale codec --
        # cp1252 on Windows -- which raises UnicodeDecodeError inside
        # Popen's reader thread. That failure lands out-of-band: the task
        # itself still reports success while its captured output is lost.
        # errors="replace" degrades one byte instead of an 8KB error log.
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc.pid)
        stdout, stderr = proc.communicate()
        raise CommandTimeoutError(
            f"Command timed out after {timeout}s: {' '.join(args)}",
            stdout + stderr,
        ) from None
    return CommandResult(proc.returncode, stdout, stderr)
