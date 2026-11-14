import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.models.bench import Bench


def get_default_bench(db: Session) -> Bench:
    bench = db.query(Bench).filter_by(is_default=True, status="ready").first()
    if bench is None:
        raise ValueError(
            "No ready default bench is registered. Run `python -m app.seed` after "
            "bringing the shared bench up with docker compose."
        )
    return bench


def get_bench(db: Session, bench_id: uuid.UUID | None) -> Bench | None:
    return db.get(Bench, bench_id) if bench_id else None
