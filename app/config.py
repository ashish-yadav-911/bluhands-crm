from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://bluhands:bluhands@localhost:5432/bluhands_control"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    base_domain: str = "tenants.localhost"
    tenant_data_root: str = "./tenants_data"
    tenant_port_range_start: int = 9000
    tenant_port_range_end: int = 9999
    caddy_sites_dir: str = "./caddy/sites-enabled"
    caddy_admin_api: str = "http://localhost:2019"

    # --- Shared ERPNext bench -------------------------------------------
    # One bench hosting many tenant sites, resolved per-request by Host
    # header. Replaces the per-tenant compose stack: there is no per-tenant
    # port in this mode, so `tenant_port_range_*` and `allocate_http_port`
    # above are dormant, kept only for a future hosting_mode="dedicated".
    shared_bench_project: str = "bluhands-shared-bench"
    shared_bench_compose: str = "docker/shared-bench-compose.yml"
    shared_bench_backend_service: str = "backend"
    shared_bench_http_port: int = 8080
    shared_bench_db_root_password: str = ""
    # Encrypts tenant credentials at rest. See app/services/crypto.py for why
    # they are now stored at all, and why keeping this in .env is a local-dev
    # compromise rather than the intended end state.
    credential_encryption_key: str = ""

    # Seed values for the ERPNext setup wizard. Completing the wizard is not
    # optional -- an incomplete site shows a blocking onboarding screen -- and
    # it creates the Company that dozens of standard doctypes require.
    default_country: str = "India"
    default_currency: str = "INR"
    default_timezone: str = "Asia/Kolkata"
    default_language: str = "English"
    default_chart_of_accounts: str = "Standard"
    # bench new-site can take a long time on a small box -- measured, not
    # assumed; see the provisioning task for how the budget is spent.
    site_create_timeout_seconds: int = 2400
    # How long a queued tenant waits for the site-creation lock. Long enough
    # to sit behind a few builds, short enough to fail visibly, not hang.
    site_create_lock_wait_seconds: int = 3600

    custom_app_name: str = "bluhands_custom"
    agent_runner: str = "stub"

    # OpenHands runs in its own Docker image (Python 3.12+, isolated from
    # both the control-plane host and the tenant's other containers) --
    # not in-process in the Celery worker. model/api_key/base_url are
    # passed straight through to openhands.sdk.LLM, so any OpenAI-compatible
    # endpoint works: OpenRouter, a self-hosted vLLM/Ollama server, etc.
    agent_runner_image: str = "bluhands-agent-runner:latest"
    agent_job_timeout_seconds: int = 1800
    llm_model: str = "openai/qwen2.5-coder-32b-instruct"
    llm_api_key: str = ""
    llm_base_url: str = ""


settings = Settings()
