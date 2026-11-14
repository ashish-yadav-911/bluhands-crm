"""Import-graph rules that keep the planner/applier boundary real.

The whole design rests on "the LLM proposes; it does not execute". That is a
property of who may import whom, so it is asserted here rather than left as a
convention someone can quietly break: the applier and verifier must be
testable with hand-written specs and no LLM, and the planner (when it exists)
with a fake LLM and no Docker.
"""

import ast
from pathlib import Path

CRM = Path(__file__).resolve().parents[2] / "app" / "services" / "crm"


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _modules(*names: str) -> list[Path]:
    return [CRM / f"{name}.py" for name in names if (CRM / f"{name}.py").exists()]


def _offending(paths: list[Path], forbidden: tuple[str, ...]) -> list[str]:
    """One entry per (file, forbidden module), however it was imported."""
    hits = []
    for path in paths:
        imported = _imports(path)
        for rule in forbidden:
            if any(module == rule or module.startswith(rule + ".") for module in imported):
                hits.append(f"{path.name} imports {rule}")
    return hits


def test_the_applier_and_verifier_never_import_a_planner():
    paths = _modules("applier", "verifier", "doc_builders", "build_service", "ledger")

    assert _offending(paths, ("app.services.crm.planner", "openai", "anthropic", "litellm")) == []


def test_the_planner_never_talks_to_frappe():
    """Vacuous until planner.py exists -- and then it starts protecting."""
    paths = _modules("planner")

    assert _offending(paths, ("httpx", "app.services.frappe", "app.services.crm.applier", "app.services.crm.verifier")) == []


def test_spec_layers_are_pure():
    """Spec, catalog, normalizer and validator do no I/O at all."""
    paths = _modules("catalog", "spec_models", "spec_normalizer", "spec_validator")

    assert _offending(paths, ("httpx", "sqlalchemy", "app.services.frappe", "app.db", "app.models", "subprocess")) == []


def test_doc_builders_are_pure():
    assert _offending(_modules("doc_builders"), ("httpx", "sqlalchemy", "app.services.frappe", "app.db", "subprocess")) == []


def test_the_architecture_test_can_actually_detect_a_violation(tmp_path):
    bad = tmp_path / "applier.py"
    bad.write_text("from app.services.crm.planner import plan_spec\nimport httpx\n", encoding="utf-8")

    assert _offending([bad], ("app.services.crm.planner",)) == ["applier.py imports app.services.crm.planner"]
