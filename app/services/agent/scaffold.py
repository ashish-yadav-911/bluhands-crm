from pathlib import Path

from app.config import settings


def scaffold_custom_app(repo_path: Path) -> None:
    """Lays down a minimal, valid Frappe custom-app skeleton (the shape
    `bench new-app` produces) if one doesn't already exist at repo_path.
    This is the app the coding agent edits and that gets installed into
    the tenant's ERPNext site via `bench install-app` -- core ERPNext is
    never touched. Idempotent: safe to call on an already-scaffolded repo."""
    app_name = settings.custom_app_name
    package_dir = repo_path / app_name

    if package_dir.exists():
        return

    package_dir.mkdir(parents=True)
    (package_dir / "config").mkdir()

    (package_dir / "__init__.py").write_text('__version__ = "0.0.1"\n', encoding="utf-8")

    (package_dir / "hooks.py").write_text(
        f'''app_name = "{app_name}"
app_title = "Bluhands Custom"
app_publisher = "bluhands"
app_description = "Tenant-specific customizations, managed by the bluhands_crm AI agent pipeline."
app_email = "agent-pipeline@bluhands.local"
app_license = "MIT"
''',
        encoding="utf-8",
    )

    (package_dir / "modules.txt").write_text(f"{app_name}\n", encoding="utf-8")
    (package_dir / "patches.txt").write_text("", encoding="utf-8")
    (package_dir / "config" / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "config" / "desktop.py").write_text(
        "def get_data():\n    return []\n",
        encoding="utf-8",
    )

    (repo_path / "setup.py").write_text(
        f'''from setuptools import find_packages, setup

setup(
    name="{app_name}",
    version="0.0.1",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
)
''',
        encoding="utf-8",
    )
    (repo_path / "MANIFEST.in").write_text("recursive-include {app_name} *\n".format(app_name=app_name), encoding="utf-8")
    (repo_path / "license.txt").write_text("MIT License\n", encoding="utf-8")
