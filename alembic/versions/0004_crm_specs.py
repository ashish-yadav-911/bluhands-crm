"""CRM specs, apply runs and the applier ledger

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24

Purely additive: three new tables, nothing existing is touched.

"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_specs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("spec", sa.JSON, nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "version", name="uq_crm_specs_tenant_version"),
    )
    op.create_index("ix_crm_specs_tenant_id", "crm_specs", ["tenant_id"])

    op.create_table(
        "crm_apply_runs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("spec_id", sa.Uuid, sa.ForeignKey("crm_specs.id"), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("records", sa.JSON, nullable=False),
        sa.Column("errors", sa.JSON, nullable=False),
        sa.Column("verification", sa.JSON, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_crm_apply_runs_tenant_id", "crm_apply_runs", ["tenant_id"])

    op.create_table(
        "crm_applied_objects",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("doctype", sa.String(140), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "doctype", "name", name="uq_crm_applied_objects_identity"),
    )
    op.create_index("ix_crm_applied_objects_tenant_id", "crm_applied_objects", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_crm_applied_objects_tenant_id", table_name="crm_applied_objects")
    op.drop_table("crm_applied_objects")
    op.drop_index("ix_crm_apply_runs_tenant_id", table_name="crm_apply_runs")
    op.drop_table("crm_apply_runs")
    op.drop_index("ix_crm_specs_tenant_id", table_name="crm_specs")
    op.drop_table("crm_specs")
