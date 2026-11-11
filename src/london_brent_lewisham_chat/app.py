"""FastAPI application: wiring, middleware and the health check.

``create_app`` builds the app (settings, logging, request logging, correlation
IDs, the ``/v1`` routers). ``lifespan`` loads the semantic layer and creates and
closes the shared OpenAI and Datahub clients. ``app`` at the bottom is what the
server runs.
"""

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import logfire
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI

from .chat import build_instructions, build_tools
from .config import Settings
from .datahub import build_datahub_client
from .llm import build_openai_client
from .log_config import configure_logging
from .observability import configure_logfire
from .routers import chat
from .schemas import Health
from .semantic import SemanticError, load_semantic
from .sessions import SessionStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Run start-up and shutdown code for the app.

    Logging is configured here rather than in ``create_app()``: the server applies
    its own logging setup after importing the app, which would overwrite ours.
    Records also go to Logfire, which ``create_app()`` has configured already.

    The semantic layer (the borough's YAMLs) is loaded next, once. An invalid or
    inconsistent YAML is logged at ERROR and stops the app: answering with a broken
    description of the data would give wrong answers, which is worse than not
    starting. Brent only for now; Lewisham is added when its YAMLs exist. The chat
    instructions and tools are built from it once, here, so every request sends the
    same prompt (which OpenAI's prompt caching can reuse). The session store starts
    empty: sessions live as long as the process.

    The OpenAI and Datahub clients are created here (``None`` without their key or
    header) and closed at shutdown, so their pools of HTTPS connections (and the
    Datahub cache) live exactly as long as the app. Creating them makes no network
    call, so start-up never waits for either service.

    One ``app started`` line then says where the app thinks it runs and what is
    configured, so a deploy that forgot ``ENVIRONMENT=production``, the OpenAI key
    or the Datahub header shows it in its first log line.

    Args:
        app: The application being started; its settings give the log level, the
            environment, the semantic folder and the OpenAI and Datahub settings.

    Yields:
        Control to FastAPI while the app is serving requests.

    Raises:
        SemanticError: If the semantic YAMLs are missing, invalid or inconsistent.
    """
    settings = app.state.settings
    configure_logging(settings.log_level, to_logfire=True)
    try:
        app.state.semantic = load_semantic("brent", settings.semantic_dir)
    except SemanticError as exc:
        logger.error("semantic layer invalid", extra={"error": str(exc)})
        raise
    app.state.instructions = build_instructions(app.state.semantic)
    app.state.tools = build_tools(app.state.semantic)
    app.state.sessions = SessionStore(
        idle_ttl_s=settings.session_idle_ttl_s,
        max_count=settings.session_max_count,
        max_turns=settings.session_max_turns,
    )
    # Kept in locals too, so shutdown closes the clients created here even if
    # app.state was changed meanwhile (tests put fake clients there).
    openai_client = build_openai_client(settings)
    datahub = build_datahub_client(settings, "brent")
    app.state.openai = openai_client
    app.state.datahub = datahub
    logger.info(
        "app started",
        extra={
            "environment": settings.environment,
            # Whether traces leave the machine; the token itself is never logged.
            "sending_to_logfire": settings.logfire_token is not None,
            # Whether chat can work; the key itself is never logged.
            "openai_configured": openai_client is not None,
            "openai_model": settings.openai_model,
            # Whether the tools can reach the Datahub; the header is never logged.
            "datahub_configured": datahub is not None,
            "semantic_fields": len(app.state.semantic.fields),
        },
    )
    try:
        yield
    finally:
        if openai_client is not None:
            await openai_client.close()
        if datahub is not None:
            await datahub.aclose()


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

    # POST /chat at the root, exactly as the assignment specifies; the status
    # check stays under /v1.
    app.include_router(chat.chat_router)
    app.include_router(chat.router)

    # Added last so it runs first: every request gets an ID before it is logged,
    # and the ID is returned in the X-Request-ID response header.
    app.add_middleware(CorrelationIdMiddleware)

    # One span per request (route, status, duration). Here, not in lifespan: it adds
    # middleware, which can't be added once the app has started, and Logfire must be
    # configured before it. (The server's logging setup doesn't touch Logfire.)
    configure_logfire(app.state.settings)
    logfire.instrument_fastapi(app)

    # A span per OpenAI call (model, duration, tokens; for chat also the prompt
    # and answer) inside the request's trace. The class, not one client, so every
    # client is covered, fakes in tests too; repeating it adds no duplicate spans.
    # Accepted trade-off: a failed call's span keeps OpenAI's error text, which
    # Logfire never scrubs; for a wrong key that includes the key's last 4 chars.
    logfire.instrument_openai(AsyncOpenAI)
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
