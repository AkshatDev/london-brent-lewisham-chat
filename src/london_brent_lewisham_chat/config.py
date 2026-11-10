"""Application settings, read once from environment variables (and ``.env``).

Every setting the app needs is declared here with a type, so a missing or
invalid value stops the app at start-up instead of failing on the first request.
"""

from pathlib import Path
from typing import Literal

from psycopg.conninfo import make_conninfo
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Typed configuration for the service.

    Values come from environment variables (case-insensitive), then a local
    ``.env`` file, then the defaults below. ``.env`` is for local development only:
    it is never committed or deployed.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: LogLevel = "INFO"
    """Minimum level of log records written to stdout."""


class PipelineSettings(BaseSettings):
    """Typed configuration for the data pipeline (download, clean, load).

    Separate from ``Settings`` so the web app does not need database settings to
    start. Every field without a default is required: a missing value stops the
    pipeline before it makes a single request.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: LogLevel = "INFO"
    """Minimum level of log records written to stdout."""

    datahub_url: str = "https://planningdata.london.gov.uk/api-guest"
    """Base URL of the Planning London Datahub guest API."""

    datahub_allow_request: SecretStr
    """Value of the ``X-API-AllowRequest`` header the Datahub requires."""

    postgres_host: str = "127.0.0.1"
    """Host of the local PostGIS (see ``compose.yaml``)."""

    postgres_port: int = 5433
    """Port of the local PostGIS on the host."""

    postgres_user: str
    """Database user."""

    postgres_password: SecretStr
    """Database password."""

    postgres_db: str
    """Database name."""

    neon_database_url: SecretStr | None = None
    """Connection URL of the hosted database ``push`` copies the bot's tables to
    (e.g. Neon). Optional: only ``push`` needs it. Contains the password, so it is
    read from ``.env`` and never logged or passed on the command line."""

    data_dir: Path = Path("data")
    """Where raw snapshots and external reference files are kept (not committed)."""

    def conninfo(self) -> str:
        """Build a libpq connection string; the password never appears in logs.

        Returns:
            A ``key=value`` connection string for ``psycopg.connect``.
        """
        return make_conninfo(
            host=self.postgres_host,
            port=self.postgres_port,
            user=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            dbname=self.postgres_db,
        )
