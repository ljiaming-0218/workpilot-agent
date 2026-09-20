"""Load and validate local configuration without exposing credentials."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    mysql_host: str = Field(default="127.0.0.1", min_length=1)
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_user: str = Field(min_length=1)
    mysql_password: SecretStr
    mysql_database: str = Field(default="workpilot_agent", min_length=1)
    mysql_connect_timeout: int = Field(default=5, ge=1, le=30, description="Seconds to wait for a database connection before timing out.")
    mysql_ssl_ca: str | None = None
    ingestion_api_key: SecretStr | None = Field(default=None, min_length=16)

    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_timeout: float = Field(default=30, ge=1, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_enable_thinking: bool = True

    @field_validator("mysql_ssl_ca", "llm_base_url", "llm_model", mode="before")
    @classmethod
    def empty_string_as_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("llm_api_key", "ingestion_api_key", mode="before")
    @classmethod
    def empty_api_key_as_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @property
    def database_url(self) -> URL:
        # URL.create handles special characters in passwords without manual escaping.
        return URL.create(
            drivername="mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password.get_secret_value(),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )
