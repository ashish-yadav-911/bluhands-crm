"""Taking a freshly created Frappe site to "usable by a customer".

`bench new-site` leaves a site that boots but is not yet usable: ERPNext
shows a blocking onboarding wizard, no Company exists (dozens of standard
doctypes require one), and there is no machine credential for the applier
to authenticate with. This module closes both gaps, unattended.

Everything here goes through `bench`, not REST, because none of it can be
done over REST: the wizard has to run before the desk is reachable, and the
API key has to exist before REST can authenticate at all.
"""

from __future__ import annotations

import json
import re
from datetime import date

from app.config import settings
from app.services.frappe.bench_client import BenchClient

# Verified against erpnext/setup/setup_wizard/operations/install_fixtures.py:
# install_company() reaches for these by attribute, so a missing key is an
# AttributeError deep inside the wizard rather than a clear error. The
# frappe-level setup_complete runs parse_args() to build the _dict this
# needs -- which is why we call that one and not ERPNext's.
_SETUP_WIZARD_METHOD = "frappe.desk.page.setup_wizard.setup_wizard.setup_complete"


class SiteSetupError(RuntimeError):
    def __init__(self, message: str, output: str = ""):
        super().__init__(message)
        self.output = output


def _financial_year(today: date | None = None) -> tuple[str, str]:
    """April-to-March, matching the default_country default. Chosen over a
    calendar year because ERPNext validates that the dates span exactly one
    year and the Company's fiscal year is awkward to change afterwards."""
    today = today or date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{start_year}-04-01", f"{start_year + 1}-03-31"


def build_setup_args(company_name: str, admin_email: str, admin_password: str) -> dict:
    fy_start, fy_end = _financial_year()
    return {
        "language": settings.default_language,
        "country": settings.default_country,
        "timezone": settings.default_timezone,
        "currency": settings.default_currency,
        "full_name": "Administrator",
        "email": admin_email,
        "password": admin_password,
        "company_name": company_name,
        "company_abbr": _company_abbr(company_name),
        "chart_of_accounts": settings.default_chart_of_accounts,
        "fy_start_date": fy_start,
        "fy_end_date": fy_end,
        "domain": "Services",
    }


def _company_abbr(company_name: str) -> str:
    """ERPNext requires a short abbreviation and rejects an empty one. Take
    the initials, fall back to the first letters of the name."""
    initials = "".join(word[0] for word in re.findall(r"[A-Za-z0-9]+", company_name))[:5]
    return (initials or "CO").upper()


def complete_setup_wizard(bench: BenchClient, company_name: str, admin_email: str, admin_password: str) -> None:
    """Run the onboarding wizard headlessly.

    Measured at ~27s and synchronous: the wizard only defers to background
    workers when `trigger_site_setup_in_background` is set in conf, which we
    never set. It is also idempotent -- Frappe short-circuits with
    {"status": "ok"} when `is_setup_complete()` -- so a retried provision
    does not redo the work.
    """
    args = build_setup_args(company_name, admin_email, admin_password)
    # Passed as a Python literal because `bench execute --kwargs` eval()s it.
    result = bench.execute(_SETUP_WIZARD_METHOD, kwargs=repr({"args": args}))

    if not is_setup_complete(bench):
        raise SiteSetupError(
            "Setup wizard reported no error but System Settings.setup_complete is still 0",
            result.stdout + result.stderr,
        )


def is_setup_complete(bench: BenchClient) -> bool:
    """Read the flag back rather than trusting the wizard's exit code -- a
    half-completed wizard leaves a site that looks fine until a customer
    hits the blocking onboarding screen."""
    result = bench.run(
        [
            "execute",
            "frappe.client.get_value",
            "--kwargs",
            repr({"doctype": "System Settings", "fieldname": "setup_complete"}),
        ],
        timeout=120,
    )
    payload = _last_json_object(result.stdout)
    return str(payload.get("setup_complete", "0")) == "1"


def mint_api_keys(bench: BenchClient, user: str = "Administrator") -> tuple[str, str]:
    """Generate the machine credential the applier authenticates with.

    Frappe stores the secret hashed and returns the plaintext exactly once,
    here. If it is lost it cannot be recovered, only re-minted -- which is
    why the caller encrypts and persists it immediately.
    """
    result = bench.execute(
        "frappe.core.doctype.user.user.generate_keys",
        args=repr([user]),
    )
    payload = _last_json_object(result.stdout)

    api_key = payload.get("api_key")
    api_secret = payload.get("api_secret")
    if not api_key or not api_secret:
        raise SiteSetupError(
            f"Could not parse API credentials for {user} from bench output",
            result.stdout + result.stderr,
        )
    return str(api_key), str(api_secret)


def _last_json_object(stdout: str) -> dict:
    """`bench execute` prints the return value after any incidental output
    (deprecation notices, "Updating Dashboard for ..."), so take the last
    JSON object in the stream rather than trying to parse the whole thing."""
    candidates = re.findall(r"\{.*?\}", stdout, flags=re.DOTALL)
    for raw in reversed(candidates):
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}
