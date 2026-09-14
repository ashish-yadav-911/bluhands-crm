"""shared bench hosting: benches, tenant credentials, site identity

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14

Purely additive. Deployment.http_port and .compose_project_name are now
unwritten in shared-bench mode but are deliberately NOT dropped: they are
exactly what a future hosting_mode="dedicated" needs, they are already
nullable, and they cost nothing to keep.

"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# SQLite's batch mode recreates the table, so it must be able to name every
# constraint it reflects -- including the unnamed foreign keys created back
# in 0001. Supplying a convention lets it derive names instead of failing
# with "Constraint must have a name". Unused on Postgres, which alters in
# place.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


def upgrade() -> None:
    op.create_table(
        "benches",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("key", sa.String(100), nullable=False, unique=True),
        sa.Column("compose_project_name", sa.String(255), nullable=False),
        sa.Column("compose_file_path", sa.String(500), nullable=False),
        sa.Column("backend_service", sa.String(100), nullable=False, server_default="backend"),
        sa.Column("frontend_http_port", sa.Integer, nullable=False),
        sa.Column("base_domain", sa.String(255), nullable=False),
        sa.Column("image_version", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="ready"),
        sa.Column("max_sites", sa.Integer, nullable=False, server_default="25"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_benches_key", "benches", ["key"])

    op.create_table(
        "tenant_credentials",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("tenant_id", sa.Uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("username", sa.String(255), nullable=False, server_default="Administrator"),
        # Plaintext by design: an api_key is an identifier, useless without
        # its secret, and readable keys let a key in Frappe's logs be traced
        # back to a tenant.
        sa.Column("api_key", sa.String(255), nullable=True),
        sa.Column("api_secret_encrypted", sa.Text, nullable=True),
        sa.Column("password_encrypted", sa.Text, nullable=True),
        sa.Column("revealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tenant_credentials_tenant_id", "tenant_credentials", ["tenant_id"])

    # batch_alter_table, not a bare add_column: SQLite cannot ALTER in a
    # foreign key, and it is the target for the migration tests. On Postgres
    # batch mode emits ordinary ALTERs, so this costs nothing there.
    #
    # Nullable + no backfill: there are no shared-bench tenants yet, and any
    # pre-existing row genuinely has no site.
    with op.batch_alter_table("tenants", naming_convention=NAMING_CONVENTION) as batch:
        batch.add_column(sa.Column("site_name", sa.String(255), nullable=True))
        batch.add_column(
            sa.Column("bench_id", sa.Uuid, sa.ForeignKey("benches.id", name="fk_tenants_bench_id"), nullable=True)
        )
        batch.add_column(
            sa.Column("hosting_mode", sa.String(50), nullable=False, server_default="shared_bench")
        )
    op.create_index("ix_tenants_site_name", "tenants", ["site_name"], unique=True)

    with op.batch_alter_table("deployments", naming_convention=NAMING_CONVENTION) as batch:
        batch.add_column(
            sa.Column("bench_id", sa.Uuid, sa.ForeignKey("benches.id", name="fk_deployments_bench_id"), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("deployments", naming_convention=NAMING_CONVENTION) as batch:
        batch.drop_column("bench_id")
    op.drop_index("ix_tenants_site_name", table_name="tenants")
    with op.batch_alter_table("tenants", naming_convention=NAMING_CONVENTION) as batch:
        batch.drop_column("hosting_mode")
        batch.drop_column("bench_id")
        batch.drop_column("site_name")
    op.drop_index("ix_tenant_credentials_tenant_id", table_name="tenant_credentials")
    op.drop_table("tenant_credentials")
    op.drop_index("ix_benches_key", table_name="benches")
    op.drop_table("benches")
