import uuid

from jose import jwt

from app.config import settings
from app.db.session import get_db
from app.main import app
from app.models.user import User


def _signup(client, org_name: str, email: str) -> tuple[str, str]:
    """Returns (access_token, organization_id) for a freshly created org."""
    resp = client.post(
        "/auth/signup",
        json={"organization_name": org_name, "email": email, "password": "s3cret-pass"},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]

    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    db = next(app.dependency_overrides[get_db]())
    try:
        user = db.get(User, uuid.UUID(payload["sub"]))
        return token, str(user.organization_id)
    finally:
        db.close()


def test_user_can_read_own_organization(client):
    token, org_id = _signup(client, "Acme Inc", "owner@acme.example")

    resp = client.get(f"/organizations/{org_id}", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["id"] == org_id


def test_user_cannot_read_another_organization(client):
    """Regression: the route took the current user as `_current_user` and
    never compared it to the requested org, so any authenticated user could
    read any organization."""
    _token_a, org_a = _signup(client, "Acme Inc", "owner@acme.example")
    token_b, _org_b = _signup(client, "Globex Ltd", "owner@globex.example")

    resp = client.get(f"/organizations/{org_a}", headers={"Authorization": f"Bearer {token_b}"})

    # 404 rather than 403: the two cases are deliberately indistinguishable
    # so this can't be used to probe which organization ids exist.
    assert resp.status_code == 404


def test_get_organization_requires_auth(client):
    resp = client.get(f"/organizations/{uuid.uuid4()}")
    assert resp.status_code == 401
