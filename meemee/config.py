from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Environment variables use the MEEMEE_ prefix."""

    model_config = SettingsConfigDict(env_prefix="MEEMEE_", env_file=".env", extra="ignore")

    model_base_url: str = "http://127.0.0.1:11434/v1"
    model_name: str = "qwen2.5-coder:14b"
    model_api_key: str = "local"
    github_token: str | None = None
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".meemee")
    workspace: Path = Field(default_factory=Path.cwd)
    max_steps: int = 12
    request_timeout: float = 60.0
    model_max_attempts: int = 3
    policy_file: Path | None = None
    api_token: str | None = None
    shell_allowlist: str = "python3,pytest,ruff,git,ls,find,cat,wc"
    worker_poll_seconds: float = 1.0
    vault_key: str | None = None
    browser_headless: bool = True
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
    oidc_role_scopes: str = "admin=admin;operator=runs:write,jobs:read,jobs:write;viewer=jobs:read"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "meemee.sqlite3"
