from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Environment variables use the MEEMEE_ prefix."""

    model_config = SettingsConfigDict(env_prefix="MEEMEE_", env_file=".env", extra="ignore")

    model_base_url: str = "http://127.0.0.1:11434/v1"
    model_name: str = "qwen2.5-coder:14b"
    model_api_key: str = "local"
    model_routes: str | None = None
    model_profiles: str | None = None
    allow_paid_models: bool = False
    hf_token: str | None = None
    hf_router_base_url: str = "https://router.huggingface.co/v1"
    hf_inkling_model: str = "thinkingmachines/Inkling-Small"
    hf_fallback: bool = True
    transformers_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    transformers_device: str = "auto"
    transformers_max_new_tokens: int = 512
    inkling_base_url: str = "http://127.0.0.1:8000/v1"
    inkling_self_hosted_model: str = "thinkingmachines/Inkling-Small"
    inkling_api_key: str | None = None
    fugu_api_key: str | None = None
    fugu_model: str = "fugu-ultra"  # Sakana alias that tracks the latest Fugu Ultra (v1.1 as of 2026-09-24)
    reflection_interval_minutes: int = 360
    reflection_poll_seconds: float = 60.0
    github_token: str | None = None
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".meemee")
    persistence_backend: str = "sqlite"
    postgres_dsn: str | None = None
    workspace: Path = Field(default_factory=Path.cwd)
    max_steps: int = 12
    request_timeout: float = 60.0
    model_max_attempts: int = 3
    policy_file: Path | None = None
    api_token: str | None = None
    signup_enabled: bool = True
    resend_api_key: str | None = None
    email_from_address: str = "onboarding@resend.dev"
    email_reply_to: str | None = None
    gmail_access_token: str | None = None
    gmail_address: str = "me"
    public_url: str = "http://localhost:8787"
    shell_allowlist: str = "python3,pytest,ruff,git,ls,find,cat,wc"
    worker_poll_seconds: float = 1.0
    vault_key: str | None = None
    audit_anchor_key: str | None = None
    browser_headless: bool = True
    browser_allow_private_hosts: bool = False
    #: Deployment environment label. "production" refuses test-only overrides at startup.
    env: str = "development"
    #: TEST ONLY: let webhook URLs resolve to loopback/private hosts (local receivers in tests).
    #: Refused when env == "production". Never enable on a deployed instance.
    webhook_allow_private_hosts: bool = False
    browser_max_sessions: int = 4
    browser_idle_timeout_seconds: float = 900
    browser_takeover_ttl_seconds: float = 900
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    log_level: str = "INFO"
    log_json: bool = True
    readiness_min_free_bytes: int = 100_000_000
    readiness_require_model: bool = False
    default_daily_jobs: int = 100
    default_plan: str = "starter"
    retention_jobs_days: int = 30
    retention_memory_days: int = 90
    retention_runs_days: int = 90
    retention_audit_days: int = 365
    shutdown_grace_seconds: float = 30.0
    webhook_poll_seconds: float = 1.0
    webhook_max_payload_bytes: int = 256_000
    webhook_breaker_cooldown_seconds: int = 300
    oidc_role_claim: str = "roles"
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_authorization_endpoint: str | None = None
    oidc_token_endpoint: str | None = None
    oidc_redirect_uri: str | None = None
    session_key: str | None = None
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    hsts_enabled: bool = False
    hsts_max_age_seconds: int = 31_536_000
    companion_history_limit: int = 40
    companion_fact_limit: int = 12
    companion_model_temperature: float = 0.7
    companion_checkin_poll_seconds: float = 30.0
    companion_webhook_secret: str | None = None
    whatsapp_provider_url: str | None = None
    whatsapp_provider_token: str | None = None
    imessage_provider_url: str | None = None
    imessage_provider_token: str | None = None
    oidc_role_scopes: str = "admin=admin;operator=runs:write,jobs:read,jobs:write,companion:read,companion:write;viewer=jobs:read,companion:read"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "meemee.sqlite3"
