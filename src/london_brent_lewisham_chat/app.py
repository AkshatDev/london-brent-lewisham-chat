"""FastAPI application: wiring, middleware and the health check.

``create_app`` builds the app (settings, logging, request logging, correlation
IDs). ``app`` at the bottom is what the server runs.
"""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import logfire
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import Settings
from .log_config import configure_logging
from .observability import configure_logfire
from .schemas import Health

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Run start-up and shutdown code for the app.

    Logging is configured here rather than in ``create_app()``: the server applies
    its own logging setup after importing the app, which would overwrite ours.
    Records also go to Logfire, which ``create_app()`` has configured already.

    One ``app started`` line then says where the app thinks it runs, so a deploy
    that forgot ``ENVIRONMENT=production`` shows ``local`` in its first log line.

    Args:
        app: The application being started; its settings give the log level and
            the environment.

    Yields:
        Control to FastAPI while the app is serving requests.
    """
    settings = app.state.settings
    configure_logging(settings.log_level, to_logfire=True)
    logger.info(
        "app started",
        extra={
            "environment": settings.environment,
            # Whether traces leave the machine; the token itself is never logged.
            "sending_to_logfire": settings.logfire_token is not None,
        },
    )
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    A factory rather than a global so tests can build an app with any settings.

    Args:
        settings: Configuration to use. ``None`` reads it from the environment.

    Returns:
        The configured application.
    """
    app = FastAPI(title="london-brent-lewisham-chat", lifespan=lifespan)
    app.state.settings = settings if settings is not None else Settings()

    @app.middleware("http")
    async def log_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Log one line per request with method, path, status code and duration.

        Unhandled exceptions are caught here, logged at ERROR with the traceback
        and turned into a plain 500. Without this, the error would reach
        Starlette's outermost ``ServerErrorMiddleware``: the request would not be
        logged and the 500 would carry no ``X-Request-ID``.

        Args:
            request: The incoming request.
            call_next: Passes the request to the rest of the app and returns its response.

        Returns:
            The response from the rest of the app, or a 500 if it raised.
        """
        start = time.perf_counter()
        fields = {"method": request.method, "path": request.url.path}
        try:
            response = await call_next(request)
        except Exception:
            fields |= {"status_code": 500, "duration_ms": _ms_since(start)}
            logger.exception("request failed", extra=fields)
            return JSONResponse(
                status_code=500, content={"detail": "Internal Server Error"}
            )

        fields |= {"status_code": response.status_code, "duration_ms": _ms_since(start)}
        logger.info("request finished", extra=fields)
        return response

    @app.get("/health")
    async def health() -> Health:
        """Report that the process is up and serving requests (liveness).

        Does not call the external API, so it stays green during an upstream outage.
        \f
        Returns:
            Always ``{"status": "ok"}``.
        """  # noqa: D301 (\f hides the rest from /docs)
        return Health(status="ok")

    # Added last so it runs first: every request gets an ID before it is logged,
    # and the ID is returned in the X-Request-ID response header.
    app.add_middleware(CorrelationIdMiddleware)

    # One span per request (route, status, duration). Here, not in lifespan: it adds
    # middleware, which can't be added once the app has started, and Logfire must be
    # configured before it. (The server's logging setup doesn't touch Logfire.)
    configure_logfire(app.state.settings)
    logfire.instrument_fastapi(app)
    return app


def _ms_since(start: float) -> float:
    """Return the milliseconds elapsed since ``start``.

    Args:
        start: A value from ``time.perf_counter()``.

    Returns:
        Elapsed time in milliseconds.
    """
    return (time.perf_counter() - start) * 1000


app = create_app()
