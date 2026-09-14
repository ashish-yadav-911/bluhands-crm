import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# "admin"  -- the site's Administrator login, shown to the customer once on
#             the delivery screen and available on request afterwards.
# "api"    -- the machine credential the applier and verifier authenticate
#             with. Never shown to the customer.
CREDENTIAL_KINDS = ("admin", "api")


class TenantCredential(Base):
    """A credential for a tenant's ERPNext site, encrypted at rest.

    Secrets live in `*_encrypted` columns and are only ever decrypted at the
    point of use, via app/services/crypto.py. `api_key` is deliberately
    plaintext: it is an identifier, useless without its secret, and keeping
    it readable makes it possible to correlate a key seen in Frappe's own
    logs with the tenant it belongs to.

    Frappe returns an API secret exactly once, at generation. If the stored
    ciphertext is ever lost or the encryption key rotates, the credential
    cannot be recovered -- it has to be re-minted against the site.
    """

    __tablename__ = "tenant_credentials"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False, default="Administrator")

    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When the customer was last shown this credential -- so the console can
    # say "revealed 3 days ago" rather than implying it is still a secret.
    revealed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    rotated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="credentials")
