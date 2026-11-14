import uuid

from sqlalchemy.orm import Session

from app.models.crm import AppliedObject


class DbLedger:
    """The applier's ledger, backed by `crm_applied_objects`.

    Each hash is committed as it is recorded rather than at the end of the run:
    if the worker dies halfway, the ledger still reflects what was actually
    applied, and the next run skips exactly that much instead of redoing it.
    """

    def __init__(self, db: Session, tenant_id: uuid.UUID):
        self._db = db
        self._tenant_id = tenant_id

    def _row(self, doctype: str, name: str) -> AppliedObject | None:
        return (
            self._db.query(AppliedObject)
            .filter_by(tenant_id=self._tenant_id, doctype=doctype, name=name)
            .first()
        )

    def get_hash(self, doctype: str, name: str) -> str | None:
        row = self._row(doctype, name)
        return row.content_hash if row else None

    def set_hash(self, doctype: str, name: str, content_hash: str) -> None:
        row = self._row(doctype, name)
        if row is None:
            self._db.add(
                AppliedObject(tenant_id=self._tenant_id, doctype=doctype, name=name, content_hash=content_hash)
            )
        else:
            row.content_hash = content_hash
        self._db.commit()
