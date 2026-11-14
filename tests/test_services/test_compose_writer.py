from app.services.provisioning.compose_writer import TenantComposeContext, render_compose_file


def test_render_compose_file_writes_compose_and_env(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.provisioning.compose_writer.settings.tenant_data_root", str(tmp_path))

    ctx = TenantComposeContext(
        tenant_slug="acme-crm",
        compose_project_name="tenant-acme-crm",
        site_name="acme-crm.tenants.localhost",
        http_port=9001,
        image_version="version-15",
        mariadb_root_password="root-secret",
        admin_password="admin-secret",
    )

    compose_path = render_compose_file("dedicated-tenant-compose.yml.j2", ctx)

    assert compose_path.exists()
    content = compose_path.read_text(encoding="utf-8")
    assert "tenant-acme-crm" in content
    assert "9001:8080" in content
    assert "root-secret" in content

    env_path = compose_path.parent / ".env"
    assert env_path.exists()
    env_content = env_path.read_text(encoding="utf-8")
    assert "ADMIN_PASSWORD=admin-secret" in env_content
    assert "SITE_NAME=acme-crm.tenants.localhost" in env_content
