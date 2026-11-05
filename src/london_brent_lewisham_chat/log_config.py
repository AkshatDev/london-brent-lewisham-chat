"""Central logging configuration for the whole service.

Every module logs through ``logging.getLogger(__name__)``; this module decides
where those records go and what they look like (one JSON object per line).
"""

from logging.config import dictConfig


def configure_logging(level: str = "INFO") -> None:
    """Configure JSON logging to stdout for the whole process.

    Every record becomes one JSON line with time, level, logger name, message, any
    ``extra`` fields and the request's correlation ID. Existing loggers keep working
    (``disable_existing_loggers`` is off) and propagate to the root handler;
    uvicorn's access log is silenced because the app logs its own request line.

    Safe to call more than once: each call replaces the previous configuration.

    Args:
        level: Minimum level to log, e.g. ``"INFO"`` or ``"DEBUG"``.
    """
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
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "json",
                "filters": ["correlation_id"],
            }
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": False},
        },
        "filters": {
            "correlation_id": {"()": "asgi_correlation_id.CorrelationIdFilter"}
        },
    }

    dictConfig(config)
