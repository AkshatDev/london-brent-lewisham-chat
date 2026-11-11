"""Central logging configuration for the whole service.

Every module logs through ``logging.getLogger(__name__)``; this module decides
where those records go and what they look like (one JSON object per line).
"""

import logging
from logging.config import dictConfig
from typing import Any


def configure_logging(level: str = "INFO", *, to_logfire: bool = False) -> None:
    """Configure JSON logging to stdout for the whole process.

    Every record becomes one JSON line with time, level, logger name, message, any
    ``extra`` fields and the request's correlation ID. Existing loggers keep working
    (``disable_existing_loggers`` is off) and propagate to the root handler;
    uvicorn's access log is silenced because the app logs its own request line.

    With ``to_logfire``, every record also goes to Logfire, attached to the span of
    the request it belongs to. Opt-in so the pipeline, which doesn't set Logfire up,
    keeps plain JSON logging.

    Safe to call more than once: each call replaces the previous configuration.

    Args:
        level: Minimum level to log, e.g. ``"INFO"`` or ``"DEBUG"``.
        to_logfire: Also send every record to Logfire (``configure_logfire`` first).
    """
    handlers: dict[str, dict[str, Any]] = {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["correlation_id"],
        }
    }
    if to_logfire:
        # Same filter, so a record's correlation ID is searchable in Logfire too.
        handlers["logfire"] = {
            "class": "logfire.LogfireLoggingHandler",
            "filters": ["correlation_id"],
            # Records logged while Logfire suppresses instrumentation (e.g. the
            # OpenAI SDK's "HTTP Request" line inside an OpenAI call) go to this
            # fallback, by default a plain-text copy on stderr. The stdout handler
            # already writes them as JSON, so the copy is dropped.
            "fallback": logging.NullHandler(),
        }

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "format": "%(levelname)s %(name)s %(message)s",
                # ISO 8601 in UTC under "time", e.g. 2026-11-05T14:42:41.491964+00:00
                # (asctime would be local time with no zone: "2026-11-05 14:41:03,677").
                "timestamp": "time",
            },
        },
        "handlers": handlers,
        "root": {"handlers": list(handlers), "level": level},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": False},
        },
        "filters": {
            "correlation_id": {"()": "asgi_correlation_id.CorrelationIdFilter"}
        },
    }

    dictConfig(config)
