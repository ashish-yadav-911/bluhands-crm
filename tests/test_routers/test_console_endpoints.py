import uuid

from app.db.session import get_db
from app.main import app
from app.models.deployment import Deployment
from app.models.tenant_credential import TenantCredential
from app.services import crypto


def _signup(client, org_name: str, email: str) -> dict:
    resp = client.post(
        "/auth/signup",
        json={"organization_name": org_name, "email": email, "password": "s3cret-pass"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _org_id(client, headers) -> str:
    return client.get("/me", headers=headers).json()["organization_id"]


def _create_tenant(client, headers, name="Acme CRM") -> dict:
    resp = client.post(
        "/tenants",
        json={"organization_id": _org_id(client, headers), "name": name, "application_key": "erpnext"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _db():
    return next(app.dependency_overrides[get_db]())


def _store_admin_credential(tenant_id: str, password: str) -> None:
    db = _db()
    try:
        db.add(
            TenantCredential(
                tenant_id=uuid.UUID(tenant_id),
                kind="admin",
                username="Administrator",
                password_encrypted=crypto.encrypt(password),
            )
        )
        db.add(
            TenantCredential(
                tenant_id=uuid.UUID(tenant_id),
                kind="api",
                username="Administrator",
                api_key="KEY",
                api_secret_encrypted=crypto.encrypt("MACHINE-SECRET"),
            )
        )
        db.commit()
    finally:
        db.close()


def test_me_returns_the_current_user(client):
    headers = _signup(client, "Acme Inc", "owner@acme.example")

    resp = client.get("/me", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["email"] == "owner@acme.example"
    assert "hashed_password" not in resp.json()


def test_me_requires_auth(client):
    assert client.get("/me").status_code == 401


def test_list_applications_hides_server_side_paths(client):
    headers = _signup(client, "Acme Inc", "owner@acme.example")

    resp = client.get("/applications", headers=headers)

    assert resp.status_code == 200
    assert [a["key"] for a in resp.json()] == ["erpnext"]
    assert "compose_template_path" not in resp.json()[0]


def test_list_applications_requires_auth(client):
    assert client.get("/applications").status_code == 401


def test_new_tenant_carries_its_site_identity(client):
    headers = _signup(client, "Acme Inc", "owner@acme.example")
    created = _create_tenant(client, headers)

    tenant = client.get(f"/tenants/{created['tenant_id']}", headers=headers).json()

    assert tenant["hosting_mode"] == "shared_bench"
    assert tenant["site_name"].endswith(".tenants.localhost")
    assert tenant["site_name"].startswith(tenant["slug"])


def test_list_tenants_is_scoped_to_the_callers_organization(client):
    acme = _signup(client, "Acme Inc", "owner@acme.example")
    globex = _signup(client, "Globex Ltd", "owner@globex.example")
    acme_tenant = _create_tenant(client, acme, "Acme CRM")
    _create_tenant(client, globex, "Globex CRM")

    listed = client.get("/tenants", headers=acme).json()

    assert [t["id"] for t in listed] == [acme_tenant["tenant_id"]]
    assert listed[0]["latest_deployment"]["status"] == "pending"


def test_list_tenants_requires_auth(client):
    assert client.get("/tenants").status_code == 401


def test_credentials_return_the_admin_login_and_never_the_api_secret(client):
    headers = _signup(client, "Acme Inc", "owner@acme.example")
    created = _create_tenant(client, headers)
    _store_admin_credential(created["tenant_id"], "admin-secret")

    resp = client.get(f"/tenants/{created['tenant_id']}/credentials", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "Administrator"
    assert body["password"] == "admin-secret"
    assert "MACHINE-SECRET" not in resp.text
    assert "api_secret" not in resp.text


def test_credentials_report_whether_they_were_shown_before(client):
    headers = _signup(client, "Acme Inc", "owner@acme.example")
    created = _create_tenant(client, headers)
    _store_admin_credential(created["tenant_id"], "admin-secret")
    url = f"/tenants/{created['tenant_id']}/credentials"

    first = client.get(url, headers=headers).json()
    second = client.get(url, headers=headers).json()

    assert first["revealed_before"] is False
    assert second["revealed_before"] is True


def test_credentials_include_the_url_once_running(client):
    headers = _signup(client, "Acme Inc", "owner@acme.example")
    created = _create_tenant(client, headers)
    _store_admin_credential(created["tenant_id"], "admin-secret")
    db = _db()
    try:
        deployment = db.get(Deployment, uuid.UUID(created["deployment_id"]))
        deployment.url = "http://acme-crm.tenants.localhost"
        db.commit()
    finally:
        db.close()

    body = client.get(f"/tenants/{created['tenant_id']}/credentials", headers=headers).json()

    assert body["url"] == "http://acme-crm.tenants.localhost"


def test_credentials_are_not_available_before_provisioning_reaches_them(client):
    headers = _signup(client, "Acme Inc", "owner@acme.example")
    created = _create_tenant(client, headers)

    resp = client.get(f"/tenants/{created['tenant_id']}/credentials", headers=headers)

    assert resp.status_code == 404


def test_credentials_of_another_organization_are_not_found(client):
    acme = _signup(client, "Acme Inc", "owner@acme.example")
    globex = _signup(client, "Globex Ltd", "owner@globex.example")
    created = _create_tenant(client, acme)
    _store_admin_credential(created["tenant_id"], "admin-secret")

    resp = client.get(f"/tenants/{created['tenant_id']}/credentials", headers=globex)

    # 404, not 403 -- and never the password.
    assert resp.status_code == 404
    assert "admin-secret" not in resp.text


def test_credentials_require_auth(client):
    assert client.get(f"/tenants/{uuid.uuid4()}/credentials").status_code == 401
