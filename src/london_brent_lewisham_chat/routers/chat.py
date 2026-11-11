"""Chat endpoints: ``POST /chat`` (the assignment's) and ``GET /v1/chat/status``.

``POST /chat`` sits at the root, exactly as the assignment specifies, so it has its
own router without the ``/v1`` prefix; the status check stays versioned.

Failures are logged with fixed messages and safe fields only: the error class,
OpenAI's HTTP status and OpenAI's request ID (for their support). Never the error
text, which can quote part of the API key; nor does any response carry it.
Datahub failures are logged once, in ``datahub.py``.

The Logfire span of the OpenAI call (``instrument_openai`` in ``app.py``) does keep
that text: an accepted trade-off, so this rule covers our own logs and responses.
"""

import logging
from dataclasses import asdict
from typing import Any, Literal

import openai
from fastapi import APIRouter, HTTPException, Request, Response

from ..chat import answer
from ..datahub import DatahubError, DatahubTimeout
from ..schemas import ChatRequest, ChatResponse, ChatStatus, CitationOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])
"""Versioned chat endpoints (the status check)."""

chat_router = APIRouter(tags=["chat"])
"""``POST /chat`` at the root, as the assignment specifies."""

NOT_CONFIGURED = "OpenAI API key is not set"
TIMED_OUT = "OpenAI did not answer in time"
UNREACHABLE = "OpenAI could not be reached"
REJECTED = "OpenAI rejected the API key or model"
UNAVAILABLE = "OpenAI is unavailable right now"
DATAHUB_NOT_CONFIGURED = "Planning London Datahub access is not set"
DATAHUB_TIMED_OUT = "The Planning London Datahub did not answer in time"
DATAHUB_UNAVAILABLE = "The Planning London Datahub is unavailable right now"

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    503: {"description": "Chat not configured, or OpenAI or the Datahub unavailable"},
    504: {"description": "OpenAI or the Datahub did not answer in time"},
}


@chat_router.post("/chat", responses=ERROR_RESPONSES)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    """Answer a question about planning applications in Brent, with citations.

    Send the same `session_id` for follow-up questions ("how many of those were
    refused?"); a new one starts a new conversation. Facts come from the live
    Planning London Datahub; each answer lists the sources it used. Lewisham and
    other areas are not covered yet.
    \f
    Args:
        body: The question and the session id (validated: 422 if invalid).
        request: The incoming request; the app holds the clients and settings.

    Returns:
        The answer, its citations and the session id.

    Raises:
        HTTPException: 503 if chat is not configured or OpenAI or the Datahub is
            unavailable; 504 if either did not answer in time.
    """  # noqa: D301 (\f hides the rest from /docs)
    state = request.app.state
    settings = state.settings
    if state.openai is None:
        raise HTTPException(503, f"Chat is not configured: {NOT_CONFIGURED}")
    if state.datahub is None:
        raise HTTPException(503, f"Chat is not configured: {DATAHUB_NOT_CONFIGURED}")

    session = state.sessions.get(body.session_id)
    async with session.lock:
        try:
            result = await answer(
                body.question,
                session,
                openai=state.openai,
                model=settings.openai_model,
                instructions=state.instructions,
                tools=state.tools,
                semantic=state.semantic,
                datahub=state.datahub,
                max_rounds=settings.chat_max_rounds,
                max_output_tokens=settings.openai_max_output_tokens,
            )
        except openai.OpenAIError as exc:
            code, detail = _openai_failure(exc, settings.openai_model, "chat")
            raise HTTPException(code, detail) from None
        except DatahubTimeout:
            raise HTTPException(504, DATAHUB_TIMED_OUT) from None
        except DatahubError:
            raise HTTPException(503, DATAHUB_UNAVAILABLE) from None
        # Kept only when the question was answered, so a failed turn leaves no
        # half-finished history behind.
        session.add_turn(result.turn, state.sessions.max_turns)

    logger.info(
        "chat finished",
        extra={
            "session_id": body.session_id,
            "rounds": result.rounds,
            "tool_calls": result.tool_calls,
            "citations": len(result.citations),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "datahub_cache_hits": result.datahub_cache_hits,
            # The count only: the summary text can restate the question, so it
            # goes to the Logfire trace (the "chat" span), never to this line.
            "reasoning_tokens": result.reasoning_tokens,
            "finished": result.finished,
        },
    )
    return ChatResponse(
        answer=result.answer,
        citations=[CitationOut(**asdict(c)) for c in result.citations],
        session_id=body.session_id,
    )


@router.get(
    "/chat/status",
    responses={503: {"model": ChatStatus}, 504: {"model": ChatStatus}},
)
async def chat_status(request: Request, response: Response) -> ChatStatus:
    """Check that chat can be used now: OpenAI answers and the model is available.

    200 if it can; 503 if it can't (no key, OpenAI down or rate-limited, key or
    model rejected); 504 if OpenAI did not answer in time. Costs no tokens.
    \f
    Args:
        request: The incoming request; the app holds the client and settings.
        response: Used to set the status code on failure.

    Returns:
        The status, the model and, on failure, a short reason.
    """  # noqa: D301 (\f hides the rest from /docs)
    client: openai.AsyncOpenAI | None = request.app.state.openai
    model: str = request.app.state.settings.openai_model

    def failed(
        code: int, status: Literal["not_configured", "unavailable"], detail: str
    ) -> ChatStatus:
        response.status_code = code
        return ChatStatus(status=status, model=model, detail=detail)

    if client is None:
        return failed(503, "not_configured", NOT_CONFIGURED)

    try:
        await client.models.retrieve(model)
    except openai.OpenAIError as exc:
        code, detail = _openai_failure(exc, model, "check")
        return failed(code, "unavailable", detail)

    return ChatStatus(status="ok", model=model)


def _openai_failure(
    exc: openai.OpenAIError, model: str, action: Literal["check", "chat"]
) -> tuple[int, str]:
    """Map an OpenAI error to a status code and a fixed detail, and log it once.

    Shared by both endpoints, so they treat OpenAI the same way. ``action`` makes
    the fixed log message say which endpoint failed (``openai chat timed out``).

    Args:
        exc: The error the SDK raised.
        model: The model that was used.
        action: ``check`` for the status endpoint, ``chat`` for ``POST /chat``.

    Returns:
        504 with the timeout detail, or 503 with the matching fixed detail.
    """
    fields = _safe_fields(exc, model)
    # Timeout first: it is a subclass of APIConnectionError.
    if isinstance(exc, openai.APITimeoutError):
        logger.warning(f"openai {action} timed out", extra=fields)
        return 504, TIMED_OUT
    if isinstance(exc, openai.APIConnectionError):
        logger.warning(f"openai {action} unreachable", extra=fields)
        return 503, UNREACHABLE
    if isinstance(
        exc,
        openai.AuthenticationError
        | openai.PermissionDeniedError
        | openai.NotFoundError,
    ):
        # ERROR, not WARNING: retrying won't help, a person must fix the config.
        logger.error(f"openai {action} rejected", extra=fields)
        return 503, REJECTED
    # Rate limits, OpenAI's 5xx and anything unexpected from the SDK.
    logger.warning(f"openai {action} failed", extra=fields)
    return 503, UNAVAILABLE


def _safe_fields(exc: openai.OpenAIError, model: str) -> dict[str, Any]:
    """Pick the log fields that are safe to keep from an OpenAI error.

    Args:
        exc: The error the SDK raised.
        model: The model that was used.

    Returns:
        The model, the error class and, when OpenAI answered, its HTTP status and
        request ID. Never the message, which can quote part of the API key.
    """
    fields: dict[str, Any] = {"model": model, "error": type(exc).__name__}
    if isinstance(exc, openai.APIStatusError):
        fields |= {
            "openai_status": exc.status_code,
            "openai_request_id": exc.request_id,
        }
    return fields
