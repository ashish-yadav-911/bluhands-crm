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
