from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import settings

_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "docker"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))


@dataclass
class TenantComposeContext:
    tenant_slug: str
    compose_project_name: str
    site_name: str
    http_port: int
    image_version: str
    mariadb_root_password: str
    admin_password: str


def tenant_dir(tenant_slug: str) -> Path:
    # Resolved to absolute: docker_runner invokes `docker compose -f <this>`
    # with cwd set to this same directory, so a relative path here gets
    # re-resolved against that new cwd and doubles up (tenants_data/x/tenants_data/x/...).
    root = Path(settings.tenant_data_root).resolve()
    path = root / tenant_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_compose_file(template_path: str, ctx: TenantComposeContext) -> Path:
    """Renders the given Jinja2 template (relative to docker/) into
    tenants_data/{slug}/docker-compose.yml, and writes the generated
    secrets into a sibling .env file. Secrets are never written to the
    control-plane DB."""
    template = _env.get_template(Path(template_path).name)
    rendered = template.render(
        tenant_slug=ctx.tenant_slug,
        compose_project_name=ctx.compose_project_name,
        site_name=ctx.site_name,
        http_port=ctx.http_port,
        image_version=ctx.image_version,
        mariadb_root_password=ctx.mariadb_root_password,
        admin_password=ctx.admin_password,
    )

    directory = tenant_dir(ctx.tenant_slug)
    compose_path = directory / "docker-compose.yml"
    compose_path.write_text(rendered, encoding="utf-8")

    env_path = directory / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"MARIADB_ROOT_PASSWORD={ctx.mariadb_root_password}",
                f"ADMIN_PASSWORD={ctx.admin_password}",
                f"SITE_NAME={ctx.site_name}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return compose_path
