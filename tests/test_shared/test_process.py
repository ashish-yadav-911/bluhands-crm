import sys

from app.shared.process import run_command


def test_run_command_survives_output_the_locale_codec_cannot_decode():
    """Regression: run_command used text=True with no encoding, so on Windows
    subprocess output was decoded as cp1252 and a stray byte raised
    UnicodeDecodeError inside Popen's reader thread. That surfaced
    out-of-band -- the Celery task went on to report success while its
    captured output was silently lost."""
    result = run_command(
        [sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'ok\x90\xff\xfedone')"],
        timeout=30,
    )

    assert result.returncode == 0
    # errors="replace" degrades the undecodable bytes rather than discarding
    # the whole stream, so the surrounding diagnostic text still survives.
    assert result.stdout.startswith("ok")
    assert result.stdout.endswith("done")


def test_run_command_captures_stderr_and_returncode():
    result = run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        timeout=30,
    )

    assert result.returncode == 3
    assert "boom" in result.stderr
