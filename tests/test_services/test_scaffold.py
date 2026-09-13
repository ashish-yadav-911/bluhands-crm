from app.config import settings
from app.services.agent.scaffold import scaffold_custom_app


def test_scaffold_custom_app_creates_expected_files(tmp_path):
    scaffold_custom_app(tmp_path)

    app_name = settings.custom_app_name
    package_dir = tmp_path / app_name

    assert (package_dir / "__init__.py").exists()
    assert (package_dir / "hooks.py").read_text(encoding="utf-8").__contains__(f'app_name = "{app_name}"')
    assert (package_dir / "modules.txt").exists()
    assert (package_dir / "config" / "desktop.py").exists()
    assert (tmp_path / "setup.py").exists()
    assert (tmp_path / "license.txt").exists()


def test_scaffold_custom_app_is_idempotent(tmp_path):
    scaffold_custom_app(tmp_path)
    package_dir = tmp_path / settings.custom_app_name
    marker = package_dir / "hooks.py"
    marker.write_text("# user-modified\n", encoding="utf-8")

    scaffold_custom_app(tmp_path)

    # Second call must not clobber existing (possibly agent-modified) files.
    assert marker.read_text(encoding="utf-8") == "# user-modified\n"
