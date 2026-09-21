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

    @property
    def database_path(self) -> Path:
        return self.data_dir / "meemee.sqlite3"
