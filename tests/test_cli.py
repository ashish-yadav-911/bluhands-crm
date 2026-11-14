import json
from pathlib import Path

from app.cli import main

GOLDEN = Path(__file__).parent / "fixtures" / "specs" / "field_service_crm.json"


def test_validate_spec_accepts_the_golden_spec(capsys):
    assert main(["validate-spec", "--spec", str(GOLDEN)]) == 0

    out = capsys.readouterr().out
    assert "Valid: Sparkle HVAC Services" in out
    assert "Service Visit" in out
    # Unsupported requests are surfaced, never dropped.
    assert "NOT INCLUDED (tier2)" in out


def test_validate_spec_reports_every_problem_and_exits_nonzero(tmp_path, capsys):
    spec = json.loads(GOLDEN.read_text(encoding="utf-8"))
    spec["doctypes"][0]["fields"][0]["options"] = "Custmer"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(spec), encoding="utf-8")

    assert main(["validate-spec", "--spec", str(path)]) == 1

    assert "Custmer" in capsys.readouterr().err
