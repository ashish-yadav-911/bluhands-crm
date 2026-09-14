import json
from pathlib import Path

import httpx
import pytest

from app.services.frappe.bench_client import BenchClient, BenchCommandError
from app.services.frappe.rest_client import FrappeApiError, FrappeClient, _parse_server_messages
from app.services.runtime import TenantRuntime
from app.shared.process import CommandResult


def _runtime(site: str = "acme.tenants.localhost") -> TenantRuntime:
    return TenantRuntime(
        site_name=site,
        hosting_mode="shared_bench",
        rest_base_url="http://127.0.0.1:8080",
        host_header=site,
        compose_project="bluhands-shared-bench",
        compose_path=Path("/repo/docker/shared-bench-compose.yml"),
        backend_service="backend",
        env_file=Path("/repo/.env"),
    )


def _client(handler) -> FrappeClient:
    client = FrappeClient("http://127.0.0.1:8080", "acme.tenants.localhost", "k", "s")
    client._client = httpx.Client(
        base_url="http://127.0.0.1:8080",
        transport=httpx.MockTransport(handler),
        headers={"Host": "acme.tenants.localhost", "Authorization": "token k:s"},
    )
    return client


# --- rest client ---------------------------------------------------------


def test_requests_carry_host_and_token_headers():
    seen = {}

    def handler(request):
        seen["host"] = request.headers.get("Host")
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"message": "pong"})

    assert _client(handler).ping() is True
    # Site selection is by Host header, so this is the load-bearing detail:
    # the client connects to 127.0.0.1 and never relies on DNS.
    assert seen["host"] == "acme.tenants.localhost"
    assert seen["auth"] == "token k:s"


def test_get_doc_returns_none_for_missing_document():
    def handler(request):
        return httpx.Response(404, json={"exc_type": "DoesNotExistError"})

    assert _client(handler).get_doc("DocType", "Nope") is None


def test_get_doc_reraises_errors_that_are_not_absence():
    def handler(request):
        return httpx.Response(403, json={"exc_type": "PermissionError"})

    with pytest.raises(FrappeApiError) as excinfo:
        _client(handler).get_doc("DocType", "Secret")
    assert excinfo.value.exc_type == "PermissionError"


def test_frappe_validation_error_is_mapped_with_readable_message():
    """Frappe signals validation failures with 417, not 4xx-as-you'd-expect,
    and buries the human-readable text in triple-encoded _server_messages.
    Callers branch on exc_type; the console shows `messages`."""
    body = {
        "exc_type": "MandatoryError",
        "_server_messages": json.dumps(
            [json.dumps({"message": "Error: Value missing for Service Visit: Subject"})]
        ),
    }

    def handler(request):
        return httpx.Response(417, json=body)

    with pytest.raises(FrappeApiError) as excinfo:
        _client(handler).insert("Service Visit", {})

    exc = excinfo.value
    assert exc.status_code == 417
    assert exc.exc_type == "MandatoryError"
    assert exc.messages == ["Error: Value missing for Service Visit: Subject"]


@pytest.mark.parametrize(
    "raw",
    [None, "", "not json", json.dumps(["not-json-either"]), json.dumps("a bare string")],
)
def test_server_message_parsing_never_raises_on_malformed_input(raw):
    # Parsing the error must never itself become the error.
    assert isinstance(_parse_server_messages({"_server_messages": raw}), list)


def test_upsert_creates_then_updates():
    """The applier's idempotency primitive: re-applying a spec must converge,
    not duplicate. The returned action drives the rollback ledger, which may
    delete what it created but never what it merely updated."""
    existing: set[str] = set()
    calls: list[str] = []

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            if name in existing:
                return httpx.Response(200, json={"data": {"name": name}})
            return httpx.Response(404, json={"exc_type": "DoesNotExistError"})
        if request.method == "POST":
            existing.add(json.loads(request.content)["name"])
            calls.append("POST")
            return httpx.Response(200, json={"data": {"name": name}})
        calls.append("PUT")
        return httpx.Response(200, json={"data": {"name": name}})

    client = _client(handler)
    _doc, first = client.upsert("Custom Field", "Customer-cf_x", {"dt": "Customer"})
    _doc, second = client.upsert("Custom Field", "Customer-cf_x", {"dt": "Customer"})

    assert (first, second) == ("created", "updated")
    assert calls == ["POST", "PUT"]


# --- bench client --------------------------------------------------------


def test_bench_commands_are_always_site_scoped(monkeypatch):
    captured = {}

    def fake_exec(project, compose_path, service, command, timeout, env_file):
        captured.update(command=command, env_file=env_file, project=project)
        return CommandResult(0, "", "")

    monkeypatch.setattr("app.services.frappe.bench_client.compose_exec", fake_exec)
    BenchClient(_runtime()).run(["clear-cache"])

    assert captured["command"][:3] == ["bench", "--site", "acme.tenants.localhost"]
    # The shared bench compose declares a required variable, so every
    # subcommand -- not just `up` -- fails interpolation without --env-file.
    assert captured["env_file"] == Path("/repo/.env")


@pytest.mark.parametrize("site", ["all", "", "   "])
def test_bench_client_refuses_dangerous_or_missing_site(site, monkeypatch):
    """`bench --site all migrate` on a shared bench is a multi-tenant
    outage. No caller wants it, so it is unrepresentable rather than merely
    discouraged."""
    monkeypatch.setattr(
        "app.services.frappe.bench_client.compose_exec",
        lambda *a, **k: pytest.fail("must not execute"),
    )
    with pytest.raises(ValueError):
        BenchClient(_runtime(site)).run(["migrate"])


def test_bench_client_raises_with_output_on_failure(monkeypatch):
    monkeypatch.setattr(
        "app.services.frappe.bench_client.compose_exec",
        lambda *a, **k: CommandResult(1, "stdout here", "stderr here"),
    )
    with pytest.raises(BenchCommandError) as excinfo:
        BenchClient(_runtime()).run(["migrate"])
    assert "stderr here" in excinfo.value.output
