"""Application configuration, read from environment variables with the prefix ``DBMCP_``.

Why environment variables: Kubernetes injects values and secrets this way without config files
in the image [BSI DEV.2.5]. Secrets have no defaults, so a missing value stops the start instead
of silently running with an example password [BSI DEV.4.8]. Insecure options are never the
default (e.g. ``db_sslmode`` defaults to ``verify-full``) [BSI DEV.4.9].
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SslMode = Literal["disable", "require", "verify-ca", "verify-full"]


class DatabaseSettings(BaseSettings):
    """Connection settings shared by every database role."""

    model_config = SettingsConfigDict(env_prefix="DBMCP_DB_", extra="ignore")

    host: str
    port: int = Field(default=5432, ge=1, le=65535)
    name: str = "titanic"
    sslmode: SslMode = "verify-full"
    sslrootcert: Path | None = None
    """CA bundle used to verify the server certificate; system CAs when unset."""


class ServerSettings(BaseSettings):
    """Settings for the running MCP server (``database-mcp serve``)."""

    model_config = SettingsConfigDict(env_prefix="DBMCP_", extra="ignore")

    host: str = "127.0.0.1"
    """Bind address. In containers set ``0.0.0.0`` explicitly; localhost is the safe default."""
    port: int = Field(default=8000, ge=1, le=65535)
    metrics_port: int = Field(default=9090, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    db_app_user: str = "titanic_app"
    db_app_password: SecretStr
    db_sql_user: str = "titanic_sql"
    db_sql_password: SecretStr

    api_keys_file: Path

    allowed_hosts: list[str] = ["localhost:*", "127.0.0.1:*"]
    """Accepted ``Host`` headers (DNS-rebinding protection of the MCP SDK)."""
    allowed_origins: list[str] = []
    """Accepted browser ``Origin`` headers; agents are not browsers, so none by default."""

    rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_burst: int = Field(default=20, ge=1)

    sql_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    sql_max_concurrency: int = Field(default=4, ge=1, le=32)

    client_ip_header: str = "x-forwarded-for"
    """Header set by the ingress with the original client IP; only used for logging."""


class InitSettings(BaseSettings):
    """Settings for ``database-mcp db-init`` (migrations, roles, seed)."""

    model_config = SettingsConfigDict(env_prefix="DBMCP_", extra="ignore")

    db_owner_user: str = "titanic_owner"
    db_owner_password: SecretStr
    db_app_user: str = "titanic_app"
    db_app_password: SecretStr | None = None
    """Only needed with ``--create-roles``; otherwise the roles already exist."""
    db_sql_user: str = "titanic_sql"
    db_sql_password: SecretStr | None = None
    data_file: Path = Path("data/titanic.csv")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
