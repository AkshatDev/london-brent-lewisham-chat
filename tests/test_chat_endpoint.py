"""POST /chat through the whole app, with fake OpenAI and Datahub clients."""

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from london_brent_lewisham_chat.app import create_app
from london_brent_lewisham_chat.config import Settings
from london_brent_lewisham_chat.datahub import DatahubClient, TTLCache
from london_brent_lewisham_chat.routers.chat import (
    DATAHUB_TIMED_OUT,
    DATAHUB_UNAVAILABLE,
    TIMED_OUT,
    UNAVAILABLE,
)
from london_brent_lewisham_chat.scope import scope_filter
from london_brent_lewisham_chat.tools import Area
from tests.test_chat import FakeDatahub, FakeOpenAI, call

Q1 = "How many planning applications were validated in Brent in 2024?"
Q2 = "How many of those were refused?"


def post(
    capsys: pytest.CaptureFixture[str],
    *questions: tuple[str, str],
    openai_client: openai.AsyncOpenAI | None = None,
    datahub: DatahubClient | None = None,
) -> tuple[list[httpx.Response], list[dict[str, Any]], str]:
    """Start the app, put the fakes in place, POST each (question, session) in turn.

    Returns:
        The responses, the parsed JSON log lines and the raw stdout.
    """
    app = create_app(Settings())
    with TestClient(app) as client:
        app.state.openai = openai_client
        app.state.areas = (
            {"brent": Area(app.state.layers["brent"], datahub)} if datahub else None
        )
        responses = [
            client.post("/chat", json={"question": q, "session_id": s})
            for q, s in questions
        ]
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return responses, lines, out


def raising_openai(error: Exception) -> openai.AsyncOpenAI:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise error

    return openai.AsyncOpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def timing_out_datahub() -> DatahubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    http = httpx.AsyncClient(
        base_url="https://d.test", transport=httpx.MockTransport(handler)
    )
    return DatahubClient(http, "brent", TTLCache(600, 8), now=lambda: datetime.now(UTC))


# --- Answers -----------------------------------------------------------------------


def test_a_question_is_answered_with_its_citations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeOpenAI([[call(2024)], "3,140 applications were validated in 2024 [c1]."])

    [response], _, _ = post(
        capsys,
        (Q1, "demo-1"),
        openai_client=fake.client,
        datahub=FakeDatahub(3140).client,
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    body = response.json()
    assert body["answer"] == "3,140 applications were validated in 2024 [c1]."
    assert body["session_id"] == "demo-1"
    [citation] = body["citations"]
    assert citation["id"] == "c1"
    assert citation["text"].startswith("Planning London Datahub API (live): Brent")
    assert citation["query"]["query"]["bool"]["filter"][0] == scope_filter("brent")
    assert citation["retrieved_at"]


def test_a_follow_up_in_the_same_session_builds_on_the_first_answer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeOpenAI(
        [
            [call(2024)],
            "3,140 [c1].",
            [call(2024, decision="Refused")],
            "722 of those were refused [c2].",
            "Which question do you mean?",  # a different session: no history
        ]
    )

    first, second, other = post(
        capsys,
        (Q1, "demo-1"),
        (Q2, "demo-1"),
        (Q2, "demo-2"),
        openai_client=fake.client,
        datahub=FakeDatahub(722).client,
    )[0]

    assert [r.status_code for r in (first, second, other)] == [200, 200, 200]
    follow_up_input = fake.bodies[2]["input"]
    assert follow_up_input[0] == {"role": "user", "content": Q1}  # turn 1 sent first
    assert follow_up_input[-1] == {"role": "user", "content": Q2}
    assert [c["id"] for c in second.json()["citations"]] == ["c2"]
    assert fake.bodies[4]["input"] == [{"role": "user", "content": Q2}]


# --- Errors ----------------------------------------------------------------------


def test_chat_is_503_when_openai_is_not_configured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    [response], _, _ = post(capsys, (Q1, "demo-1"))  # no key: no client

    assert response.status_code == 503
    assert "OpenAI API key is not set" in response.json()["detail"]
    assert response.headers["X-Request-ID"]


def test_chat_is_503_when_the_datahub_is_not_configured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    [response], _, _ = post(capsys, (Q1, "demo-1"), openai_client=FakeOpenAI([]).client)

    assert response.status_code == 503
    assert "Datahub access is not set" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"question": "", "session_id": "a"},
        {"question": "   ", "session_id": "a"},
        {"question": "x" * 1001, "session_id": "a"},
        {"question": "q", "session_id": "has space"},
        {"question": "q", "session_id": "x" * 65},
        {"question": "q"},
        {"session_id": "a"},
    ],
    ids=[
        "empty",
        "blank",
        "too-long",
        "space-in-id",
        "id-too-long",
        "no-id",
        "no-question",
    ],
)
def test_invalid_requests_get_422_before_any_call(
    capsys: pytest.CaptureFixture[str], body: dict[str, str]
) -> None:
    fake = FakeOpenAI([])
    app = create_app(Settings())
    with TestClient(app) as client:
        app.state.openai = fake.client
        response = client.post("/chat", json=body)

    assert response.status_code == 422
    assert fake.bodies == []


@pytest.mark.parametrize(
    ("openai_client", "datahub", "code", "detail"),
    [
        (
            lambda: FakeOpenAI([], status=500).client,
            lambda: FakeDatahub().client,
            503,
            UNAVAILABLE,
        ),
        (
            lambda: raising_openai(httpx2.ReadTimeout("slow")),
            lambda: FakeDatahub().client,
            504,
            TIMED_OUT,
        ),
        (
            lambda: FakeOpenAI([[call(2024)]]).client,
            lambda: FakeDatahub(status=502).client,
            503,
            DATAHUB_UNAVAILABLE,
        ),
        (
            lambda: FakeOpenAI([[call(2024)]]).client,
            timing_out_datahub,
            504,
            DATAHUB_TIMED_OUT,
        ),
    ],
    ids=["openai-down", "openai-timeout", "datahub-down", "datahub-timeout"],
)
def test_upstream_failures_become_503_or_504_with_a_fixed_detail(
    capsys: pytest.CaptureFixture[str],
    openai_client: Any,  # noqa: ANN401 (factory)
    datahub: Any,  # noqa: ANN401 (factory)
    code: int,
    detail: str,
) -> None:
    [response], _, _ = post(
        capsys, (Q1, "demo-1"), openai_client=openai_client(), datahub=datahub()
    )

    assert response.status_code == code
    assert response.json() == {"detail": detail}
    assert response.headers["X-Request-ID"]


def test_a_failed_question_leaves_no_history_behind(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        app.state.openai = FakeOpenAI([[call(2024)]]).client
        app.state.areas = {
            "brent": Area(app.state.layers["brent"], FakeDatahub(status=502).client)
        }
        failed = client.post("/chat", json={"question": Q1, "session_id": "demo-1"})

        fake = FakeOpenAI(["Fine."])
        app.state.openai = fake.client
        client.post("/chat", json={"question": Q2, "session_id": "demo-1"})

    assert failed.status_code == 503
    assert fake.bodies[0]["input"] == [{"role": "user", "content": Q2}]


# --- Logging ---------------------------------------------------------------------


def test_each_question_logs_one_chat_finished_line_without_its_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeOpenAI([[call(2024)], "3,140 [c1].", [call(2024)], "Again 3,140 [c2]."])

    responses, lines, out = post(
        capsys,
        (Q1, "demo-1"),
        (Q1, "demo-1"),
        openai_client=fake.client,
        datahub=FakeDatahub(3140).client,
    )

    finished = [line for line in lines if line["message"] == "chat finished"]
    assert len(finished) == 2
    first, second = finished
    assert first["session_id"] == "demo-1"
    assert first["tool_calls"] == ["count_applications"]
    assert (first["rounds"], first["citations"], first["finished"]) == (2, 1, True)
    assert (first["input_tokens"], first["output_tokens"]) == (200, 20)
    assert (first["datahub_cache_hits"], second["datahub_cache_hits"]) == (0, 1)
    assert first["correlation_id"] == responses[0].headers["X-Request-ID"]
    assert Q1 not in out  # no question text in any log line


def test_reasoning_is_logged_as_a_count_never_as_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = "Need the refused share: 722 of 3,140 is 23.0%."
    fake = FakeOpenAI(
        [[call(2024, decision="Refused")], "23.0% were refused [c1]."],
        reasoning={2: (summary, 146)},
    )

    [response], lines, out = post(
        capsys,
        (Q2, "demo-1"),
        openai_client=fake.client,
        datahub=FakeDatahub(722).client,
    )

    assert response.status_code == 200
    [finished] = [line for line in lines if line["message"] == "chat finished"]
    assert finished["reasoning_tokens"] == 146
    assert summary not in out  # the summary goes to the Logfire trace only


def test_a_follow_up_shows_the_earlier_citations_its_answer_mentions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeOpenAI(
        [
            [call(2024)],
            "3,140 were validated [c1].",
            [call(2024, decision="Refused")],
            "722 of those [c2], out of 3,140 [c1]; see also [c9].",
        ]
    )

    first, second = post(
        capsys,
        (Q1, "demo-1"),
        (Q2, "demo-1"),
        openai_client=fake.client,
        datahub=FakeDatahub(722).client,
    )[0]

    assert [c["id"] for c in first.json()["citations"]] == ["c1"]
    # This turn's c2 first, then the earlier c1 its answer repeats; c9 doesn't exist.
    assert [c["id"] for c in second.json()["citations"]] == ["c2", "c1"]
