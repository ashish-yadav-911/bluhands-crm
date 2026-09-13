from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader

from app.config import settings

_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "caddy"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))


def register_tenant_route(tenant_slug: str, site_name: str, http_port: int) -> None:
    """Renders a per-tenant Caddyfile snippet and asks Caddy's admin API to
    reload config so the route is live without a full restart. Falls back to
    a no-op with a clear error if Caddy's admin API isn't reachable (e.g. in
    a unit-test context) -- callers decide whether that's fatal."""
    template = _env.get_template("Caddyfile.j2")
    rendered = template.render(site_name=site_name, http_port=http_port)

    sites_dir = Path(settings.caddy_sites_dir)
    sites_dir.mkdir(parents=True, exist_ok=True)
    snippet_path = sites_dir / f"{tenant_slug}.caddy"
    snippet_path.write_text(rendered, encoding="utf-8")

    _reload_caddy()


def _reload_caddy() -> None:
    """Assembles the global Caddyfile block plus every rendered per-tenant
    snippet into one flat config and pushes it whole via Caddy's admin API.
    We build the full text ourselves (rather than relying on the `import`
    directive resolving relative paths through the admin API) so reload
    doesn't depend on how Caddy interprets a config's on-disk origin."""
    sites_dir = Path(settings.caddy_sites_dir)
    global_config_path = sites_dir.parent / "Caddyfile"

    global_block = ""
    if global_config_path.exists():
        for line in global_config_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("import "):
                continue
            global_block += line + "\n"

    snippets = sorted(sites_dir.glob("*.caddy")) if sites_dir.exists() else []
    site_blocks = "\n".join(path.read_text(encoding="utf-8") for path in snippets)

    full_config = f"{global_block}\n{site_blocks}\n"

    with httpx.Client(timeout=10) as client:
        response = client.post(
            f"{settings.caddy_admin_api}/load",
            headers={"Content-Type": "text/caddyfile"},
            content=full_config.encode("utf-8"),
        )
        response.raise_for_status()
