import copy
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "specs"


@pytest.fixture()
def golden_raw() -> dict:
    """A fresh, mutable copy of the golden spec as the planner would emit it
    (un-normalized)."""
    return copy.deepcopy(json.loads((FIXTURES / "field_service_crm.json").read_text(encoding="utf-8")))


@pytest.fixture()
def golden_spec(golden_raw):
    from app.services.crm.spec_validator import load_spec

    return load_spec(golden_raw)


@pytest.fixture()
def tenant(db_session):
    from app.models.application import Application
    from app.models.organization import Organization
    from app.models.tenant import Tenant

    org = Organization(name="Acme Inc", slug="acme")
    app_row = Application(
        key="erpnext", display_name="ERPNext", compose_template_path="x.j2", default_version="version-15"
    )
    db_session.add_all([org, app_row])
    db_session.flush()
    row = Tenant(
        organization_id=org.id,
        application_id=app_row.id,
        name="Acme CRM",
        slug="acme-crm",
        site_name="acme-crm.tenants.localhost",
    )
    db_session.add(row)
    db_session.commit()
    return row
