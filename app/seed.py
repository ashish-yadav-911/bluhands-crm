"""Run with `python -m app.seed` after migrations to register the
provisionable application types. Idempotent: safe to re-run."""

from app.db.base import SessionLocal
from app.models.application import Application


def seed_applications() -> None:
    db = SessionLocal()
    try:
        if db.query(Application).filter_by(key="erpnext").first() is None:
            db.add(
                Application(
                    key="erpnext",
                    display_name="ERPNext",
                    compose_template_path="tenant-compose.yml.j2",
                    default_version="version-15",
                    is_active=True,
                )
            )
            db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed_applications()
    print("Seeded application registry.")
