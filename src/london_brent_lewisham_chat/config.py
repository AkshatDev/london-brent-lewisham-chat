"""Application settings, read once from environment variables (and ``.env``).

Every setting the app needs is declared here with a type, so a missing or
invalid value stops the app at start-up instead of failing on the first request.
"""

from pathlib import Path
from typing import Literal

from psycopg.conninfo import make_conninfo
from pydantic import (
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

Environment = Literal["local", "production"]
"""Where the app runs. A fixed list, so a typo such as ``prod`` stops the app at
start-up instead of tagging traces with a name nobody filters on."""


class Settings(BaseSettings):
    """Typed configuration for the service.

    Values come from environment variables (case-insensitive), then a local
    ``.env`` file, then the defaults below. ``.env`` is for local development only:
    it is never committed or deployed.

    One deliberate exception to "missing config stops the app": the OpenAI key is
    optional, so the app (and ``/health``) runs without it and the chat endpoints
    say that chat is not configured. An invalid value still stops the app.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    log_level: LogLevel = "INFO"
    """Minimum level of log records written to stdout."""

    environment: Environment = "local"
    """Where the app runs; tags every trace so local and deployed runs can be told
    apart in Logfire. Defaults to ``local`` so a fresh clone needs no setup; set
    ``ENVIRONMENT=production`` on FastAPI Cloud (it is logged at start-up, so a
    missing value shows in the first log line)."""

    logfire_token: SecretStr | None = None
    """Write token of the Logfire project. Without it nothing is sent to Logfire
    (local runs, tests). A secret: kept in ``.env`` locally, a secret env var when
    deployed, never logged."""

    logfire_console: bool = False
    """Also print Logfire's spans and logs to the terminal, as readable text. For
    trying Logfire out locally: the text is mixed into the JSON log lines on stdout,
    so it stays off in production and in tests."""

    openai_api_key: SecretStr | None = None
    """OpenAI API key. Optional: without it the app runs, and the chat endpoints
    answer 503 "not configured". A secret: kept in ``.env`` locally, a secret env
    var when deployed, never logged."""

    openai_model: str = Field(default="gpt-6-luna", min_length=1)
    """OpenAI model the chat uses. A setting so it can change without a code
    change; ``/v1/chat/status`` checks that the key can use it."""

    openai_timeout_s: PositiveFloat = 30.0
    """Seconds to wait for one OpenAI request before giving up (504). The SDK's own
    default is 10 minutes, far too long for someone waiting for an answer."""

    openai_max_retries: NonNegativeInt = 2
    """How often the SDK retries a failed request (connection errors, 429, 5xx),
    with backoff. The worst-case wait is about (retries + 1) x the timeout."""

    openai_max_output_tokens: PositiveInt = 1000
    """Most tokens the model may write per call. Answers are a few sentences; the
    limit bounds the cost of one call if the model runs on."""

    chat_max_rounds: PositiveInt = 5
    """Most model calls for one question (each tool round is one). A confused model
    stops here and gets a fixed, honest answer instead of looping."""

    session_max_turns: PositiveInt = 10
    """Questions and answers kept per session for follow-ups; older ones are
    dropped, so the model's input (and cost) stays bounded."""

    session_idle_ttl_s: PositiveFloat = 3600.0
    """A session unused this long (seconds) is forgotten."""

    session_max_count: PositiveInt = 1000
    """Most sessions kept in memory; the least recently used goes first."""

    semantic_dir: Path = Path("semantic")
    """Folder with the semantic YAMLs, one subfolder per borough (``brent/``). They
    are read once at start-up; an invalid file stops the app. Relative to the
    working directory: the project root locally, ``/app`` in the Docker image."""

    datahub_url: str = Field(
        default="https://planningdata.london.gov.uk/api-guest", min_length=1
    )
    """Base URL of the Planning London Datahub guest API, which the chat tools query
    live (the brief requires it)."""

    datahub_allow_request: SecretStr | None = None
    """Value of the ``X-API-AllowRequest`` header the Datahub requires. Optional, like
    the OpenAI key: without it the app runs and the tools answer "not configured".
    Kept as a secret and never logged."""

    datahub_timeout_s: PositiveFloat = 10.0
    """Seconds to wait for one Datahub request (504 after). Much shorter than the
    pipeline's bulk-download timeout: someone is waiting for the answer."""

    datahub_cache_ttl_s: PositiveFloat = 600.0
    """How long a Datahub answer is reused for the same query (the brief's "basic
    caching"). The data changes slowly; 10 minutes keeps answers fresh enough."""

    datahub_cache_max_entries: PositiveInt = 256
    """Most answers kept in the cache; the oldest goes first, so memory stays bounded."""

    @field_validator(
        "logfire_token", "openai_api_key", "datahub_allow_request", mode="before"
    )
    @classmethod
    def _blank_secret_is_no_secret(cls, value: object) -> object:
        """Treat a blank secret (e.g. ``LOGFIRE_TOKEN=`` from ``.env.example``) as not set.

        Without this an empty value becomes an empty secret, and the client would
        try to use it instead of knowing there is none.

        Args:
            value: The raw value from the environment or ``.env``.

        Returns:
            ``None`` for a blank value, otherwise the value unchanged.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value


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
