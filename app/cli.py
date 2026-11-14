"""Operator commands. `python -m app.cli --help`.

    python -m app.cli validate-spec  --spec specs/crm.json
    python -m app.cli apply-spec     --tenant acme-inc-acme-crm --spec specs/crm.json

`validate-spec` needs no database and no site; it is the fastest way to see
what the normalizer and validator make of a spec.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services.crm.spec_validator import SpecValidationError, load_spec


def _read_spec(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(_read_spec(args.spec))
    except SpecValidationError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Valid: {spec.meta.name}")
    print(f"  doctypes: {', '.join(d.name for d in spec.doctypes) or '-'}")
    print(f"  roles: {', '.join(r.name for r in spec.roles) or '-'}")
    print(f"  workflows: {len(spec.workflows)}  notifications: {len(spec.notifications)}  seed rows: {len(spec.seed_data)}")
    for item in spec.unsupported_requests:
        print(f"  NOT INCLUDED ({item.kind}): {item.request} -- {item.reason}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    from app.db.base import SessionLocal
    from app.models.tenant import Tenant
    from app.services.crm.build_service import MissingCredentialsError, apply_spec_to_tenant

    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter_by(slug=args.tenant).first()
        if tenant is None:
            print(f"No tenant with slug {args.tenant!r}", file=sys.stderr)
            return 2

        try:
            outcome = apply_spec_to_tenant(
                db, tenant, _read_spec(args.spec), on_progress=lambda stage, msg: print(f"  {stage}: {msg}")
            )
        except SpecValidationError as exc:
            print(exc, file=sys.stderr)
            return 1
        except MissingCredentialsError as exc:
            print(exc, file=sys.stderr)
            return 2

        result, report = outcome.result, outcome.report
        print(f"\nApply: {result.status}  (spec v{outcome.spec_record.version})")
        print(f"  created {sum(r.action == 'created' for r in result.records)}, "
              f"updated {sum(r.action == 'updated' for r in result.records)}, "
              f"unchanged {sum(r.action == 'unchanged' for r in result.records)}")
        for error in result.errors:
            print(f"  ERROR {error}")
        if report is not None:
            print(f"Verify: {'ok' if report.ok else 'FAILED'}  ({len(report.checks)} checks)")
            for check in report.failures + report.warnings:
                print(f"  {check.status.upper()} {check.name}: {check.detail}")
        print("Deliverable" if outcome.deliverable else "NOT deliverable")
        return 0 if outcome.deliverable else 1
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-spec", help="check a spec without touching any site")
    validate.add_argument("--spec", required=True)
    validate.set_defaults(func=cmd_validate)

    apply = sub.add_parser("apply-spec", help="apply a spec to a tenant's site and verify it")
    apply.add_argument("--tenant", required=True, help="tenant slug")
    apply.add_argument("--spec", required=True)
    apply.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
