"""The chat loop with a scripted fake OpenAI and a fake Datahub; no real calls."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import httpx2
import openai
import pytest

from london_brent_lewisham_chat.chat import (
    NO_REASONING,
    NO_SUMMARY,
    ROUNDS_USED_UP,
    ChatResult,
    answer,
    build_instructions,
    build_tools,
)
from london_brent_lewisham_chat.datahub import (
    DatahubClient,
    DatahubUnavailable,
    TTLCache,
)
from london_brent_lewisham_chat.scope import scope_filter
from london_brent_lewisham_chat.semantic import SemanticLayer, load_semantic
from london_brent_lewisham_chat.sessions import Session
from london_brent_lewisham_chat.tools import Area

SEMANTIC = Path(__file__).parents[1] / "semantic"
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

Plan = list[dict[str, Any]] | str
"""One scripted model response: tool calls (their arguments) or the final text."""


@pytest.fixture(scope="module")
def brent() -> SemanticLayer:
    return load_semantic("brent", SEMANTIC)


def call(year: int, **extra: str) -> dict[str, Any]:
    return {
        "name": "count_applications",
        "arguments": {"local_authority": "Brent", "year": year, **extra},
    }


class FakeOpenAI:
    """Answers each request with the next scripted plan; records the requests.

    ``reasoning`` maps a response number (1 for the first) to the reasoning that
    response reports: (summary text, reasoning tokens). By default none: no summary,
    0 reasoning tokens, as the real model does for a plain lookup.
    """

    def __init__(
        self,
        plans: list[Plan],
        status: int = 200,
        reasoning: dict[int, tuple[str, int]] | None = None,
    ) -> None:
        self.plans = list(plans)
        self.reasoning = reasoning or {}
        self.bodies: list[dict[str, Any]] = []
        self.calls_made = 0

        def handler(request: httpx2.Request) -> httpx2.Response:
            self.bodies.append(json.loads(request.content))
            if status != 200:
                return httpx2.Response(status, json={"error": {"message": "fake"}})
            return httpx2.Response(200, json=self._response(self.plans.pop(0)))

        self.client = openai.AsyncOpenAI(
            api_key="sk-test",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )

    def _response(self, plan: Plan) -> dict[str, Any]:
        n = len(self.bodies)
        summary, reasoning_tokens = self.reasoning.get(n, ("", 0))
        reasoning_item = {
            "type": "reasoning",
            "id": f"rs_{n}",
            "summary": [{"type": "summary_text", "text": summary}] if summary else [],
            "encrypted_content": f"enc_{n}",
        }
        if isinstance(plan, str):
            output = ([reasoning_item] if summary else []) + [
                {
                    "type": "message",
                    "id": f"msg_{n}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": plan, "annotations": []}
                    ],
                }
            ]
        else:
            output: list[dict[str, Any]] = [reasoning_item]
            for c in plan:
                self.calls_made += 1
                output.append(
                    {
                        "type": "function_call",
                        "id": f"fc_{self.calls_made}",
                        "call_id": f"call_{self.calls_made}",
                        "name": c["name"],
                        "arguments": json.dumps(c["arguments"]),
                        "status": "completed",
                    }
                )
        return {
            "id": f"resp_{n}",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "gpt-6-luna",
            "output": output,
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            },
        }


class FakeDatahub:
    """Answers every count with ``count``; records the request bodies."""

    def __init__(self, count: int = 3140, status: int = 200) -> None:
        self.bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.bodies.append(json.loads(request.content))
            if status != 200:
                return httpx.Response(status)
            return httpx.Response(200, json={"count": count})

        http = httpx.AsyncClient(
            base_url="https://datahub.test", transport=httpx.MockTransport(handler)
        )
        self.client = DatahubClient(http, "brent", TTLCache(600, 16), now=lambda: T0)


def ask(
    brent: SemanticLayer,
    question: str,
    fake_openai: FakeOpenAI,
    fake_datahub: FakeDatahub | None = None,
    session: Session | None = None,
    max_rounds: int = 5,
) -> ChatResult:
    return asyncio.run(
        answer(
            question,
            session or Session(id="demo"),
            openai=fake_openai.client,
            model="gpt-6-luna",
            instructions=build_instructions([brent]),
            tools=build_tools([brent]),
            areas={"brent": Area(brent, (fake_datahub or FakeDatahub()).client)},
            max_rounds=max_rounds,
            max_output_tokens=1000,
        )
    )


def outputs_sent(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        json.loads(i["output"])
        for i in body["input"]
        if i.get("type") == "function_call_output"
    ]


# --- Answers ---------------------------------------------------------------------


def test_a_question_needing_no_tool_is_answered_directly(brent: SemanticLayer) -> None:
    fake = FakeOpenAI(["Lewisham is not covered yet."])

    result = ask(brent, "What about Lewisham?", fake)

    assert result.answer == "Lewisham is not covered yet."
    assert result.citations == []
    assert result.rounds == 1
    assert result.finished is True
    assert result.turn == [
        {"role": "user", "content": "What about Lewisham?"},
        {"role": "assistant", "content": "Lewisham is not covered yet."},
    ]


def test_a_tool_call_runs_its_result_goes_back_and_the_answer_is_cited(
    brent: SemanticLayer,
) -> None:
    fake, datahub = (
        FakeOpenAI([[call(2024)], "3,140 were validated in 2024 [c1]."]),
        FakeDatahub(3140),
    )

    result = ask(brent, "How many were validated in Brent in 2024?", fake, datahub)

    [datahub_body] = datahub.bodies
    assert datahub_body["query"]["bool"]["filter"][0] == scope_filter("brent")
    [sent] = outputs_sent(fake.bodies[1])
    assert (sent["count"], sent["citation"]) == (3140, "c1")
    assert result.answer == "3,140 were validated in 2024 [c1]."
    assert [c.id for c in result.citations] == ["c1"]
    assert result.tool_calls == ["count_applications"]
    assert (result.rounds, result.input_tokens, result.output_tokens) == (2, 200, 20)


def test_calls_made_together_run_together_and_are_cited_in_order(
    brent: SemanticLayer,
) -> None:
    fake = FakeOpenAI([[call(2023), call(2024)], "Compared [c1] [c2]."])

    result = ask(brent, "2023 vs 2024?", fake)

    assert [c.id for c in result.citations] == ["c1", "c2"]
    assert ["2023" in c.text for c in result.citations] == [True, False]
    sent = outputs_sent(fake.bodies[1])
    assert [s["citation"] for s in sent] == ["c1", "c2"]


def test_invalid_arguments_go_back_to_the_model_without_a_datahub_call(
    brent: SemanticLayer,
) -> None:
    fake, datahub = (
        FakeOpenAI([[call(2024, local_authority="Lewisham")], "Not covered."]),
        FakeDatahub(),
    )

    result = ask(brent, "Lewisham in 2024?", fake, datahub)

    [error] = outputs_sent(fake.bodies[1])
    assert error["error"] == "invalid arguments"
    assert any(p.startswith("local_authority:") for p in error["problems"])
    assert datahub.bodies == []
    assert result.citations == []
    assert result.finished is True


def test_an_unknown_tool_goes_back_to_the_model_as_an_error(
    brent: SemanticLayer,
) -> None:
    fake = FakeOpenAI(
        [[{"name": "count_homes", "arguments": {}}], "I can't count homes."]
    )

    ask(brent, "How many homes?", fake)

    [error] = outputs_sent(fake.bodies[1])
    assert error == {"error": "there is no tool named 'count_homes'"}


def test_the_round_limit_gives_a_fixed_answer_without_citations(
    brent: SemanticLayer,
) -> None:
    fake = FakeOpenAI([[call(2023)], [call(2024)], "never reached"])

    result = ask(brent, "Keep going", fake, max_rounds=2)

    assert result.answer == ROUNDS_USED_UP
    assert result.citations == []
    assert result.finished is False
    assert len(fake.bodies) == 2
    assert result.turn[-1] == {"role": "assistant", "content": ROUNDS_USED_UP}


# --- Requests and history --------------------------------------------------------


def test_every_request_stores_nothing_at_openai_and_carries_prompt_and_tools(
    brent: SemanticLayer,
) -> None:
    fake = FakeOpenAI([[call(2024)], "Done [c1]."])

    ask(brent, "q", fake)

    for body in fake.bodies:
        assert body["store"] is False
        assert body["include"] == ["reasoning.encrypted_content"]
        assert body["instructions"] == build_instructions([brent])
        assert [t["name"] for t in body["tools"]] == [
            "count_applications",
            "search_applications",
            "get_application_details",
        ]
        assert body["max_output_tokens"] == 1000


def test_within_a_question_the_models_reasoning_is_sent_back(
    brent: SemanticLayer,
) -> None:
    fake = FakeOpenAI([[call(2024)], "Done [c1]."])

    ask(brent, "q", fake)

    second = fake.bodies[1]["input"]
    assert {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "enc_1",
    }.items() <= next(i for i in second if i.get("type") == "reasoning").items()


def test_a_follow_up_sends_the_earlier_turn_without_openai_ids(
    brent: SemanticLayer,
) -> None:
    session = Session(id="demo")
    first = ask(
        brent,
        "How many validated in 2024?",
        FakeOpenAI([[call(2024)], "3,140 [c1]."]),
        session=session,
    )
    session.add_turn(first.turn, max_turns=10)
    fake = FakeOpenAI([[call(2024, decision="Refused")], "722 of those [c2]."])

    second = ask(brent, "How many of those were refused?", fake, session=session)

    sent = fake.bodies[0]["input"]
    assert sent[: len(first.turn)] == first.turn  # turn 1 first, as kept
    assert sent[-1] == {"role": "user", "content": "How many of those were refused?"}
    assert not any(i.get("type") == "reasoning" for i in sent)  # not kept across turns
    assert all("id" not in i for i in sent if i.get("type") == "function_call")
    assert [c.id for c in second.citations] == ["c2"]  # numbering continues


def test_a_repeated_count_comes_from_the_cache_and_is_reported(
    brent: SemanticLayer,
) -> None:
    session, datahub = Session(id="demo"), FakeDatahub(3140)
    first = ask(
        brent, "q1", FakeOpenAI([[call(2024)], "3,140 [c1]."]), datahub, session
    )
    second = ask(
        brent, "q2", FakeOpenAI([[call(2024)], "3,140 [c2]."]), datahub, session
    )

    assert len(datahub.bodies) == 1  # the second count never reached the Datahub
    assert (first.datahub_cache_hits, second.datahub_cache_hits) == (0, 1)
    # A cached answer is still cited, with its original retrieval time.
    assert second.citations[0].retrieved_at == first.citations[0].retrieved_at


# --- Reasoning -------------------------------------------------------------------


def test_every_request_asks_for_a_reasoning_summary(brent: SemanticLayer) -> None:
    fake = FakeOpenAI([[call(2024)], "Done [c1]."])

    ask(brent, "q", fake)

    assert [body["reasoning"] for body in fake.bodies] == [{"summary": "auto"}] * 2


def test_without_reasoning_the_trace_says_no_reported_reasoning(
    brent: SemanticLayer,
) -> None:
    result = ask(brent, "q", FakeOpenAI([[call(2024)], "3,140 [c1]."]))

    assert result.reasoning_summary == NO_REASONING
    assert result.reasoning_tokens == 0


def test_reasoning_without_a_summary_is_reported_as_such(
    brent: SemanticLayer,
) -> None:
    # As seen live: an off-topic question, 65 reasoning tokens, no summary text.
    fake = FakeOpenAI(
        ["I can only answer about planning in Brent."], reasoning={1: ("", 65)}
    )

    result = ask(brent, "Who is the president of the United States?", fake)

    assert result.reasoning_tokens == 65
    assert result.reasoning_summary == NO_SUMMARY.format(tokens=65)
    assert result.reasoning_summary != NO_REASONING


def test_reasoning_summaries_and_tokens_are_collected_over_all_rounds(
    brent: SemanticLayer,
) -> None:
    fake = FakeOpenAI(
        [[call(2024, decision="Refused")], "23.0% were refused [c1]."],
        reasoning={
            1: ("Counting the refused ones first.", 40),
            2: ("722 of 3,140 is 23.0%.", 106),
        },
    )

    result = ask(brent, "What share were refused?", fake)

    assert result.reasoning_summary == (
        "Counting the refused ones first.\n722 of 3,140 is 23.0%."
    )
    assert result.reasoning_tokens == 146


# --- Failures --------------------------------------------------------------------


def test_openai_errors_reach_the_caller_unchanged(brent: SemanticLayer) -> None:
    with pytest.raises(openai.InternalServerError):
        ask(brent, "q", FakeOpenAI([], status=500))


def test_datahub_errors_reach_the_caller_unchanged(brent: SemanticLayer) -> None:
    with pytest.raises(DatahubUnavailable):
        ask(brent, "q", FakeOpenAI([[call(2024)]]), FakeDatahub(status=502))
