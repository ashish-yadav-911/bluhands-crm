import re
import uuid

from sqlalchemy.orm import Session

from app.models.organization import Organization
from app.models.user import User
from app.services.security import hash_password


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def create_organization_with_owner(db: Session, org_name: str, email: str, password: str) -> tuple[Organization, User]:
    base_slug = slugify(org_name)
    slug = base_slug
    suffix = 1
    while db.query(Organization).filter_by(slug=slug).first() is not None:
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    organization = Organization(name=org_name, slug=slug)
    db.add(organization)
    db.flush()

    user = User(
        organization_id=organization.id,
        email=email,
        hashed_password=hash_password(password),
        role="owner",
    )
    db.add(user)
    db.commit()
    db.refresh(organization)
    db.refresh(user)
    return organization, user


def create_organization(db: Session, name: str) -> Organization:
    base_slug = slugify(name)
    slug = base_slug
    suffix = 1
    while db.query(Organization).filter_by(slug=slug).first() is not None:
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    organization = Organization(name=name, slug=slug)
    db.add(organization)
    db.commit()
    db.refresh(organization)
    return organization
