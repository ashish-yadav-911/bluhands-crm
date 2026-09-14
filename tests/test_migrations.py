"""Guards against model/migration drift.

Every other test builds its schema with `Base.metadata.create_all`, so a
migration that disagrees with the models -- or is missing entirely -- would
go completely unnoticed until a real deployment. These tests are the only
thing that actually executes the alembic chain.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app import models  # noqa: F401 - registers every model on Base.metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


@pytest.fixture()
def migrated_db(tmp_path):
    db_path = tmp_path / "migrations.sqlite"
    db_url = f"sqlite:///{db_path}"
    command.upgrade(_alembic_config(db_url), "head")
    engine = create_engine(db_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_migrations_run_to_head(migrated_db):
    tables = set(inspect(migrated_db).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables), (
        "Tables created by migrations differ from the models. "
        f"Only in DB: {tables - set(Base.metadata.tables)}. "
        f"Only in models: {set(Base.metadata.tables) - tables}."
    )


def test_every_model_column_exists_in_the_migrated_schema(migrated_db):
    """Catches the common half-done migration: a new model attribute that
    nobody added an add_column for."""
    inspector = inspect(migrated_db)
    missing: list[str] = []

    for table_name, table in Base.metadata.tables.items():
        actual = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in actual:
                missing.append(f"{table_name}.{column.name}")

    assert not missing, f"Columns on models but absent from migrations: {missing}"


def test_downgrade_reverses_cleanly(tmp_path):
    """A downgrade that throws leaves a half-migrated database, which is a
    worse position than not having tried."""
    db_url = f"sqlite:///{tmp_path / 'downgrade.sqlite'}"
    config = _alembic_config(db_url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(db_url)
    try:
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()

    assert remaining == set()
