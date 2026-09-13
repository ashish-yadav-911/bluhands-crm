from app.models.deployment import Deployment
from app.models.tenant import Tenant
from app.services.provisioning.naming import (
    allocate_http_port,
    compose_project_name,
    generate_secret,
    generate_tenant_slug,
    site_name,
)


def _make_tenant(db_session, slug: str) -> Tenant:
    import uuid

    from app.models.application import Application
    from app.models.organization import Organization

    org = Organization(name="Acme", slug="acme")
    db_session.add(org)
    db_session.flush()

    app_row = Application(
        key="erpnext",
        display_name="ERPNext",
        compose_template_path="tenant-compose.yml.j2",
        default_version="version-15",
    )
    db_session.add(app_row)
    db_session.flush()

    tenant = Tenant(organization_id=org.id, application_id=app_row.id, name="Acme CRM", slug=slug)
    db_session.add(tenant)
    db_session.commit()
    return tenant


def test_generate_tenant_slug_deduplicates(db_session):
    _make_tenant(db_session, "acme-acme-crm")

    slug = generate_tenant_slug(db_session, "acme", "Acme CRM")

    assert slug == "acme-acme-crm-2"


def test_generate_tenant_slug_first_time(db_session):
    slug = generate_tenant_slug(db_session, "acme", "Acme CRM")

    assert slug == "acme-acme-crm"


def test_compose_project_name():
    assert compose_project_name("acme-crm") == "tenant-acme-crm"


def test_site_name():
    from app.config import settings

    assert site_name("acme-crm") == f"acme-crm.{settings.base_domain}"


def test_allocate_http_port_skips_taken_ports(db_session):
    tenant = _make_tenant(db_session, "acme-crm")
    db_session.add(Deployment(tenant_id=tenant.id, status="running", http_port=9000))
    db_session.commit()

    port = allocate_http_port(db_session)

    assert port == 9001


def test_generate_secret_is_random_and_correct_length():
    a = generate_secret(16)
    b = generate_secret(16)

    assert len(a) == 16
    assert a != b
