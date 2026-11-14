"""Read-only checks that an applied spec actually works on the site.

Runs after the applier and blocks delivery on any failure: a customer is
never handed a URL that has not passed. Everything is over REST and nothing
here writes.

`applied without an error` is deliberately not trusted. The nastiest silent
failure is a DocType row that saved while the `tab<name>` DDL failed, so the
doctype exists in metadata but cannot be listed -- which is why "listable" and
"form renders" are separate checks from "exists". "Form renders" calls the
same endpoint the desk calls before drawing a form, so a doctype that would
show the customer a broken form fails here instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.services.crm import doc_builders as build
from app.services.crm.applier import SiteClient
from app.services.crm.spec_models import CrmSpec

# A missing doctype, custom field, role or workflow is a FAIL: the CRM is not
# what was approved. A missing workspace, number card, notification or seed row
# is only a WARN: the CRM is usable and the customer can be told what is
# absent. Only FAILs block delivery.
PASS, WARN, FAIL = "pass", "warn", "fail"

EPOCH = "1970-01-01 00:00:00"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class VerifyReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    @property
    def ok(self) -> bool:
        return not self.failures


class Verifier:
    def __init__(self, client: SiteClient, error_log_watermark: str | None = None):
        """`error_log_watermark` is the newest Error Log timestamp *before*
        the apply started (see `error_log_watermark`); anything logged after
        it is attributed to the run. It is read from the site itself rather
        than taken from our clock because the site keeps its own timezone.
        Omit it to skip that check."""
        self.client = client
        self.watermark = error_log_watermark
        self._report = VerifyReport()

    @staticmethod
    def error_log_watermark(client: SiteClient) -> str:
        """Call this immediately before applying."""
        newest = client.get_list("Error Log", fields=["creation"], limit=1, order_by="creation desc")
        return str(newest[0]["creation"]) if newest else EPOCH

    def verify(self, spec: CrmSpec) -> VerifyReport:
        self._report = VerifyReport()

        self._check("bench-responds", self._bench_responds)
        for doctype in spec.doctypes:
            self._verify_doctype(doctype)
        self._verify_mods(spec)
        self._verify_objects(spec)
        if self.watermark:
            self._check("error-log-clean", self._error_log_delta)
        return self._report

    # -------------------------------------------------------------- plumbing

    def _check(self, name: str, fn: Callable[[], str | None], severity: str = FAIL) -> None:
        """One check. `fn` returns a problem message, or None for a pass; an
        exception counts as a problem too, never a crash of the verifier.
        `severity` is what a problem is reported as (FAIL blocks delivery)."""
        try:
            problem = fn()
        except Exception as exc:  # noqa: BLE001 - a broken site must yield a report, not a traceback
            problem = str(exc)
        self._report.checks.append(Check(name, severity, problem) if problem else Check(name, PASS))

    def _exists(self, doctype: str, name: str) -> str | None:
        return None if self.client.get_doc(doctype, name) is not None else f"{doctype} {name!r} is missing from the site"

    # ---------------------------------------------------------------- checks

    def _bench_responds(self) -> str | None:
        """An apply that wedges the shared workers is a cross-tenant incident,
        and nothing else would notice."""
        return None if self.client.ping() else "the site stopped answering after the apply"

    def _verify_doctype(self, doctype) -> None:
        name = doctype.name

        def exists_and_complete() -> str | None:
            doc = self.client.get_doc("DocType", name)
            if doc is None:
                return f"DocType {name!r} is missing from the site"
            if not doc.get("custom"):
                return f"DocType {name!r} is not a custom doctype"
            on_site = {f.get("fieldname") for f in doc.get("fields", [])}
            missing = [f.fieldname for f in doctype.fields if f.fieldname not in on_site]
            return f"fields missing from {name!r}: {', '.join(missing)}" if missing else None

        self._check(f"doctype-exists:{name}", exists_and_complete)

        if not doctype.is_child_table:
            # Catches "the DocType row saved but its table did not get created".
            self._check(
                f"listable:{name}",
                lambda: None if self.client.get_list(name, limit=1) is not None else "list returned nothing",
            )

        def form_renders() -> str | None:
            payload = self.client.call_method("frappe.desk.form.load.getdoctype", {"doctype": name, "with_parent": 1})
            return None if (payload or {}).get("docs") else f"the desk could not load the form definition for {name!r}"

        self._check(f"form-renders:{name}", form_renders)

    def _verify_mods(self, spec: CrmSpec) -> None:
        for entry in spec.standard_doctype_mods.custom_fields:
            name = build.custom_field_name(entry.doctype, entry.field.fieldname)
            self._check(f"custom-field:{name}", lambda n=name: self._exists("Custom Field", n))
        for setter in spec.standard_doctype_mods.property_setters:
            name = build.property_setter_name(setter)
            self._check(f"property-setter:{name}", lambda n=name: self._exists("Property Setter", n))

    def _verify_objects(self, spec: CrmSpec) -> None:
        for role in spec.roles:
            self._check(f"role:{role.name}", lambda n=role.name: self._exists("Role", n))

        for workflow in spec.workflows:
            def active(w=workflow) -> str | None:
                doc = self.client.get_doc("Workflow", w.name)
                if doc is None:
                    return f"Workflow {w.name!r} is missing from the site"
                return None if doc.get("is_active") else f"Workflow {w.name!r} exists but is not active"

            self._check(f"workflow:{workflow.name}", active)

        for workspace in spec.workspaces:
            self._check(f"workspace:{workspace.title}", lambda t=workspace.title: self._exists("Workspace", t), WARN)
            for card in workspace.number_cards:
                self._check(f"number-card:{card.label}", lambda n=card.label: self._exists("Number Card", n), WARN)

        for notification in spec.notifications:
            self._check(f"notification:{notification.name}", lambda n=notification.name: self._exists("Notification", n), WARN)

        for row in spec.seed_data:
            label = ", ".join(f"{k}={row.values[k]}" for k in row.match_on)

            def seeded(r=row) -> str | None:
                filters = [[r.doctype, key, "=", r.values[key]] for key in r.match_on]
                return None if self.client.get_list(r.doctype, filters=filters, limit=1) else f"seed row {r.doctype} ({label}) is missing"

            self._check(f"seed:{row.doctype}:{label}", seeded, WARN)

    def _error_log_delta(self) -> str | None:
        """Server-side exceptions that an HTTP 200 can hide, in one call."""
        entries = self.client.get_list(
            "Error Log",
            filters=[["Error Log", "creation", ">", self.watermark]],
            fields=["name", "method"],
            limit=5,
        )
        if not entries:
            return None
        methods = ", ".join(sorted({str(e.get("method") or e["name"]) for e in entries}))
        return f"{len(entries)} server error(s) were logged during the apply: {methods}"
