"""Application settings, read once from environment variables (and ``.env``).

Every setting the app needs is declared here with a type, so a missing or
invalid value stops the app at start-up instead of failing on the first request.
"""

from typing import Literal

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
