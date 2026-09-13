import uuid

from jose import jwt

from app.config import settings
from app.db.session import get_db
from app.main import app
from app.models.user import User

def _current_user_organization_id(access_token: str) -> str:
    payload = jwt.decode(access_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    db_gen = app.dependency_overrides[get_db]()
    db = next(db_gen)
    try:
        user = db.get(User, uuid.UUID(payload["sub"]))
        return str(user.organization_id)
    finally:
        db.close()


def test_signup_and_login(client):
    signup_resp = client.post(
        "/auth/signup",
        json={"organization_name": "Acme Inc", "email": "owner@acme.example", "password": "s3cret-pass"},
    )
    assert signup_resp.status_code == 201, signup_resp.text
    assert signup_resp.json()["access_token"]

    login_resp = client.post("/auth/login", json={"email": "owner@acme.example", "password": "s3cret-pass"})
    assert login_resp.status_code == 200
    assert login_resp.json()["access_token"]

    bad_login_resp = client.post("/auth/login", json={"email": "owner@acme.example", "password": "wrong"})
    assert bad_login_resp.status_code == 401


def test_get_tenant_requires_auth(client):
    resp = client.get(f"/tenants/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_create_tenant_returns_pending_deployment(client):
    signup_resp = client.post(
        "/auth/signup",
        json={"organization_name": "Acme Inc", "email": "owner2@acme.example", "password": "s3cret-pass"},
    )
    assert signup_resp.status_code == 201
    token = signup_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    organization_id = _current_user_organization_id(token)

    create_resp = client.post(
        "/tenants",
        json={"organization_id": organization_id, "name": "Acme CRM", "application_key": "erpnext"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert body["status"] == "pending"
    assert body["admin_password"]

    deployment_resp = client.get(f"/deployments/{body['deployment_id']}", headers=headers)
    assert deployment_resp.status_code == 200
    assert deployment_resp.json()["status"] == "pending"

    tenant_resp = client.get(f"/tenants/{body['tenant_id']}", headers=headers)
    assert tenant_resp.status_code == 200
    assert tenant_resp.json()["latest_deployment"]["status"] == "pending"


def test_create_tenant_rejects_other_organization(client):
    signup_resp = client.post(
        "/auth/signup",
        json={"organization_name": "Acme Inc", "email": "owner3@acme.example", "password": "s3cret-pass"},
    )
    token = signup_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post(
        "/tenants",
        json={"organization_id": str(uuid.uuid4()), "name": "Not Mine", "application_key": "erpnext"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_create_tenant_rejects_unknown_application(client):
    signup_resp = client.post(
        "/auth/signup",
        json={"organization_name": "Acme Inc", "email": "owner4@acme.example", "password": "s3cret-pass"},
    )
    token = signup_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    organization_id = _current_user_organization_id(token)

    resp = client.post(
        "/tenants",
        json={"organization_id": organization_id, "name": "Acme ERP", "application_key": "not-a-real-app"},
        headers=headers,
    )
    assert resp.status_code == 400
