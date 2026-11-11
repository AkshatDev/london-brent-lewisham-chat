"""The shapes of the JSON the API sends and receives."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints


class Health(BaseModel):
    """Response body of the health check."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    status: Literal["ok"]
    """Always "ok" while the service is running."""


class ChatStatus(BaseModel):
    """Response body of the chat status check: can the service reach OpenAI now?

    The same shape for success and failure, so a caller always gets the model and
    a reason; the HTTP status code says whether chat is usable (200) or not (503,
    504).
    """

    model_config = ConfigDict(use_attribute_docstrings=True)

    status: Literal["ok", "not_configured", "unavailable"]
    """``ok``: OpenAI answered and the model can be used. ``not_configured``: no
    OpenAI key is set. ``unavailable``: OpenAI could not be used right now
    (timeout, outage, rate limit, rejected key or model)."""

    model: str
    """The OpenAI model the chat uses (the ``OPENAI_MODEL`` setting)."""

    detail: str | None = None
    """A short, fixed explanation when chat is not usable; never OpenAI's own error
    text, which can include account details."""


class ChatRequest(BaseModel):
    """Request body of ``POST /chat``, exactly as the assignment specifies.

    Limits are checked here, at the edge, so bad input gets a 422 before any
    OpenAI or Datahub call.
    """

    model_config = ConfigDict(
        use_attribute_docstrings=True,
        json_schema_extra={
            "examples": [
                {
                    "question": "How many planning applications were validated in "
                    "Brent in 2024?",
                    "session_id": "demo-1",
                }
            ]
        },
    )

    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
    ]
    """The question, in plain English (1 to 1,000 characters)."""

    session_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    """Identifies the conversation, so follow-up questions ("how many of those were
    refused?") build on earlier answers. 1 to 64 letters, digits, ``-`` or ``_``;
    choose a new one to start a new conversation."""


class CitationOut(BaseModel):
    """One source behind an answer, built from a tool call the bot actually made."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    id: str
    """The marker the answer uses for this source, e.g. ``c1`` for ``[c1]``.
    Numbered across the session."""

    text: str
    """The citation: the source, what was counted or looked up, and when it was
    retrieved."""

    query: dict[str, Any]
    """The exact request sent to the Planning London Datahub, so the answer can be
    checked by running it again."""

    retrieved_at: datetime
    """When the Datahub answered (UTC). A cached answer keeps its original time."""


class ChatResponse(BaseModel):
    """Response body of ``POST /chat``: the answer and the sources behind it."""

    model_config = ConfigDict(use_attribute_docstrings=True)

    answer: str
    """The answer, with citation markers such as ``[c1]`` after facts from sources."""

    citations: list[CitationOut]
    """Every source the bot used for this answer; empty if it used none."""

    session_id: str
    """The session the answer belongs to (as sent)."""
