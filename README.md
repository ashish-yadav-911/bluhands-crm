# bluhands_crm

Control plane that provisions hosted ERPNext sites and (in progress) turns a
plain-language description into a customized CRM on one.

## Architecture

- **FastAPI** control plane: auth, organizations, tenants, deployments, agent jobs.
- **Celery** workers: provisioning (`provisioning` queue) and coding-agent jobs (`agent_jobs` queue).
- **One shared ERPNext bench, one Frappe site per tenant.** The bench's nginx picks the
  site from the `Host` header (`FRAPPE_SITE_NAME_HEADER: $$host`). A new tenant is
  `bench new-site` (~3 min), not nine more containers.
- **`app/services/runtime.py`** resolves a tenant to where its site lives. Everything that
  touches a site goes through it, so a per-tenant `dedicated` hosting mode is one branch
  there. The dedicated machinery (`compose_writer`, `frappe_bootstrap`, `proxy_registrar`,
  `docker/dedicated-tenant-compose.yml.j2`) is dormant, not deleted.
- **Customization is spec-driven.** An LLM proposes a schema-validated CRM spec, the customer
  approves it, and deterministic code applies it over Frappe's REST API (`custom=1`
  DocTypes need no custom app and no image build). The LLM proposes; it never executes.

## Running locally

```sh
# 1. Control-plane infra: postgres, redis, caddy
docker compose -f docker-compose.dev.yml up -d

# 2. The shared bench (needs SHARED_BENCH_DB_ROOT_PASSWORD in .env)
docker compose --env-file .env -f docker/shared-bench-compose.yml -p bluhands-shared-bench up -d

# 3. Secrets: CREDENTIAL_ENCRYPTION_KEY, SHARED_BENCH_DB_ROOT_PASSWORD, JWT_SECRET_KEY in .env
python -c "from app.services.crypto import generate_key; print(generate_key())"

# 4. Schema + registry rows (application types and the default bench)
alembic upgrade head
python -m app.seed

# 5. API and workers
uvicorn app.main:app --reload
celery -A app.celery_app worker -Q celery -c 1 -n provisioning@%h
celery -A app.celery_app worker -Q agent_jobs -c 1 -n agent@%h

pytest
```

Tenants are served at `http://<slug>.tenants.localhost` (RFC 6761: `*.localhost` resolves
without a hosts entry). Caddy's single wildcard block forwards them to the bench.

Keep the machine awake during a first build. Windows Modern Standby stalls wall-clock
timings by hours; measure container CPU if you need real numbers.

## CRM specs (spec -> applier -> verifier)

`app/services/crm/` is the deterministic half of "describe your CRM, get your CRM". There is
no LLM in it yet; a hand-written spec drives the whole thing.

```
raw JSON -> spec_models (shapes) -> spec_normalizer (fills in boilerplate)
         -> spec_validator (cross-references, budgets) -> applier -> verifier
```

- `catalog.py` is the closed vocabulary (field types, link targets, roles, caps). One file,
  read by the validator and, later, the planner prompt, so they cannot drift.
- `spec_normalizer.py` derives fieldnames, `naming_series`, `workflow_state`, the System
  Manager permission, undeclared roles and a sidebar workspace, so a planner never has to.
- `applier.py` applies in dependency order over REST. Idempotent (natural keys + content
  hash + the `crm_applied_objects` ledger; the live site wins over the ledger). Critical
  stages fail fast, cosmetic ones (workspaces, notifications, seed) end `partial`.
  It never removes anything on re-apply, and `rollback()` deletes only what a run *created*.
- `verifier.py` is read-only: exists, listable, form renders, Error Log delta. Missing
  essentials **block** delivery; missing cosmetics only **warn**.
- The applier and verifier may not import a planner (`tests/test_crm/test_architecture.py`).

```sh
python -m app.cli validate-spec --spec tests/fixtures/specs/field_service_crm.json
python -m app.cli apply-spec --tenant <tenant-slug> --spec path/to/spec.json
```

**Verified only against an in-memory fake site** (`tests/support/fake_frappe.py`), which
enforces the Frappe rules the applier is built around but is not Frappe. The payload shapes
for Workspace, Number Card and Notification, and the `custom=1` DocType round trip, have not
yet been run against a live bench; do that first when Docker is up.

## Isolation caveats of the shared bench

Accepted for local dev. They are the reason a tenant needing real server-side code should
move to `hosting_mode="dedicated"` rather than get a custom app installed here.

- **One MariaDB server.** Data isolation is schema-level and real; resource isolation is
  zero, and `max_connections` becomes a cross-tenant limit.
- **Shared workers and scheduler.** One tenant's slow background job delays everyone.
- **Shared installed-app set.** No per-tenant apps and no independent upgrades. A custom
  app would load that tenant's Python into processes serving every other tenant.
- **Shared `common_site_config.json`.** Per-tenant config must use
  `bench --site <site> set-config`; `set-config -g` hits every site.
- **One bad `migrate` takes down every tenant.** `BenchClient` refuses an empty site or the
  literal `all` so a bench-wide command cannot be issued by accident.
- **Server Scripts are left disabled.** RestrictedPython would be the only boundary between
  one tenant's script and every other tenant's worker.
- **Site creation is serialized** by a Redis lock (`app/services/provisioning/locks.py`);
  two concurrent `bench new-site` runs thrash a small machine.

## Credentials

Tenant credentials are stored encrypted (Fernet) in `tenant_credentials`. The Administrator
password is shown to the customer via `GET /tenants/{id}/credentials`; the API key/secret
the applier authenticates with never leaves the server. Frappe returns an API secret
exactly once, so a lost or unreadable secret is re-minted, not recovered. In local dev the
encryption key sits in `.env` next to `DATABASE_URL`, so "an attacker needs the database
*and* the key" does not yet hold; that must change before any real deployment.
