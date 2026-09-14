"""Symmetric encryption for tenant credentials held at rest.

A deliberate reversal of an earlier decision. Until now the tenant admin
password was never persisted -- generated, returned once in the create
response, and forgotten (see app/schemas/tenant.py). That worked when a
human was watching the one API call that produced it.

It stops working here. The console has to show credentials on a delivery
screen that survives a page reload, and the applier needs a site's API
secret on every later build. So the credentials are stored, encrypted,
and the security property becomes "an attacker needs the database *and*
the key" rather than "the secret does not exist".

For local dev the key lives in .env alongside DATABASE_URL, which means
that property does not actually hold yet -- anyone with the .env has both.
That is acceptable for localhost and must not survive contact with a real
deployment, where the key belongs in a separate secret store.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class CredentialEncryptionError(RuntimeError):
    pass


def generate_key() -> str:
    """A new urlsafe base64 Fernet key, for seeding .env."""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    key = settings.credential_encryption_key
    if not key:
        raise CredentialEncryptionError(
            "credential_encryption_key is not set. Generate one with "
            "`python -c \"from app.services.crypto import generate_key; print(generate_key())\"` "
            "and put it in .env as CREDENTIAL_ENCRYPTION_KEY."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise CredentialEncryptionError(f"credential_encryption_key is not a valid Fernet key: {exc}") from exc


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        # Almost always a rotated or mismatched key rather than tampering.
        # Say so, because the alternative is a silent empty password that
        # only fails much later at the point of use.
        raise CredentialEncryptionError(
            "Could not decrypt credential: the value was encrypted with a different key."
        ) from exc
