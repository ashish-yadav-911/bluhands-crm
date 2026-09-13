import uuid

from jose import jwt

from app.config import settings
from app.db.session import get_db
from app.main import app
from app.models.user import User

def _signup_and_create_tenant(client) -> tuple[dict, str]:
    signup_resp = client.post(
        "/auth/signup",
        json={"organization_name": "Acme Inc", "email": "owner@acme.example", "password": "s3cret-pass"},
    )
    token = signup_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    from jose import jwt

    from app.config import settings
    from app.models.user import User

    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    db_gen = app.dependency_overrides[get_db]()
    db = next(db_gen)
    user = db.get(User, uuid.UUID(payload["sub"]))
    organization_id = str(user.organization_id)
    db.close()

    create_resp = client.post(
        "/tenants",
        json={"organization_id": organization_id, "name": "Acme CRM", "application_key": "erpnext"},
        headers=headers,
    )
    tenant_id = create_resp.json()["tenant_id"]
    return headers, tenant_id


def test_create_and_poll_agent_job(client):
    headers, tenant_id = _signup_and_create_tenant(client)

    create_resp = client.post(
        f"/tenants/{tenant_id}/agent-jobs",
        json={"prompt": "Add a quotation approval workflow above 1 lakh"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert body["status"] == "pending"
    assert body["tenant_id"] == tenant_id

    poll_resp = client.get(f"/agent-jobs/{body['id']}", headers=headers)
    assert poll_resp.status_code == 200
    assert poll_resp.json()["status"] == "pending"


def test_agent_job_for_unowned_tenant_is_not_found(client):
    headers, _tenant_id = _signup_and_create_tenant(client)

    resp = client.post(
        f"/tenants/{uuid.uuid4()}/agent-jobs",
        json={"prompt": "Add a WhatsApp button"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_get_agent_job_requires_auth(client):
    resp = client.get(f"/agent-jobs/{uuid.uuid4()}")
    assert resp.status_code == 401
