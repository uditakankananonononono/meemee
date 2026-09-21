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
    oidc_role_claim: str = "roles"
    oidc_role_scopes: str = "admin=admin;operator=runs:write,jobs:read,jobs:write;viewer=jobs:read"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "meemee.sqlite3"
