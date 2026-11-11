"""The live-Datahub client: scoped, cached, bounded. A fake Datahub, never the real one."""

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from london_brent_lewisham_chat.config import Settings
from london_brent_lewisham_chat.datahub import (
    MAX_SEARCH_SIZE,
    DatahubClient,
    DatahubRejected,
    DatahubTimeout,
    DatahubUnavailable,
    TTLCache,
    build_datahub_client,
)
from london_brent_lewisham_chat.scope import scope_filter

HEADER = "test-allow-request-value"
OUTSIDE = "Elsewhere"  # stands in for any place outside the two boroughs
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
TTL_S = 600.0

Handler = Callable[[httpx.Request], httpx.Response]


class FakeClock:
    """A steady clock the test moves by hand, so expiry needs no sleeping."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


class FakeDatahub:
    """A Datahub client wired to ``handler``; records every request it sends."""

    def __init__(self, handler: Handler, max_entries: int = 256) -> None:
        self.requests: list[httpx.Request] = []
        self.clock = FakeClock()

        def recording(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        http = httpx.AsyncClient(
            base_url="https://datahub.test",
            headers={"X-API-AllowRequest": HEADER},
            transport=httpx.MockTransport(recording),
        )
        cache = TTLCache(TTL_S, max_entries, clock=self.clock)
        self.client = DatahubClient(http, "brent", cache, now=lambda: T0)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def counted(n: int) -> Handler:
    return lambda request: httpx.Response(200, json={"count": n})


def found(*records: dict[str, Any], total: int | None = None) -> Handler:
    hits = [{"_source": record} for record in records]
    body = {
        "hits": {
            "total": {"value": len(records) if total is None else total},
            "hits": hits,
        }
    }
    return lambda request: httpx.Response(200, json=body)


BRENT = {"id": "Brent-24_0001", "borough": "Brent", "lpa_app_no": "24/0001"}


# --- Scope -----------------------------------------------------------------------


def test_every_query_starts_with_the_scope_filter_and_sends_the_header() -> None:
    datahub = FakeDatahub(
        lambda r: (counted(1) if r.url.path.endswith("_count") else found(BRENT))(r)
    )
    extra = {"term": {"decision.raw": "Refused"}}

    run(datahub.client.count(extra))
    run(datahub.client.search(extra))

    for request, body in zip(datahub.requests, datahub.bodies(), strict=True):
        assert body["query"]["bool"]["filter"] == [scope_filter("brent"), extra]
        assert request.headers["X-API-AllowRequest"] == HEADER
    assert [r.url.path for r in datahub.requests] == [
        "/applications/_count",
        "/applications/_search",
    ]


def test_search_drops_out_of_scope_records_and_logs_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    outsider = {"id": "X-1", "borough": OUTSIDE}
    datahub = FakeDatahub(found(BRENT, outsider))

    result = run(datahub.client.search())

    assert result.records == [BRENT]
    assert any(
        r.levelname == "WARNING" and r.getMessage() == "out-of-scope record dropped"
        for r in caplog.records
    )


def test_search_asks_for_the_exact_total_and_caps_the_size() -> None:
    datahub = FakeDatahub(found(BRENT, total=3140))
    sort = [{"valid_date": "desc"}]

    result = run(datahub.client.search(size=50, sort=sort, fields=["id", "borough"]))

    [body] = datahub.bodies()
    assert body["size"] == MAX_SEARCH_SIZE
    assert body["track_total_hits"] is True
    assert body["sort"] == sort
    assert body["_source"] == ["id", "borough"]
    assert result.total == 3140
    assert result.retrieved_at == T0


# --- Cache -----------------------------------------------------------------------


def test_a_repeated_query_is_served_from_the_cache_with_its_original_time() -> None:
    datahub = FakeDatahub(counted(42))

    first = run(datahub.client.count())
    datahub.clock.seconds += TTL_S - 1
    second = run(datahub.client.count())

    assert len(datahub.requests) == 1
    assert (first.cached, second.cached) == (False, True)
    assert second.count == 42
    assert second.retrieved_at == first.retrieved_at


def test_different_queries_are_cached_separately() -> None:
    datahub = FakeDatahub(counted(1))

    run(datahub.client.count({"term": {"decision.raw": "Refused"}}))
    run(datahub.client.count({"term": {"decision.raw": "Approved"}}))

    assert len(datahub.requests) == 2


def test_cache_entries_expire() -> None:
    datahub = FakeDatahub(counted(1))

    run(datahub.client.count())
    datahub.clock.seconds += TTL_S
    again = run(datahub.client.count())

    assert len(datahub.requests) == 2
    assert again.cached is False


def test_the_oldest_entry_is_dropped_when_the_cache_is_full() -> None:
    cache = TTLCache(TTL_S, max_entries=2, clock=FakeClock())
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)

    assert (cache.get("a"), cache.get("b"), cache.get("c")) == (None, 2, 3)


# --- Failures ----------------------------------------------------------------------


def timed_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("too slow", request=request)


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no route to host", request=request)


@pytest.mark.parametrize(
    ("handler", "error", "level"),
    [
        (timed_out, DatahubTimeout, "WARNING"),
        (unreachable, DatahubUnavailable, "WARNING"),
        (lambda r: httpx.Response(502), DatahubUnavailable, "WARNING"),
        (lambda r: httpx.Response(429), DatahubUnavailable, "WARNING"),
        (lambda r: httpx.Response(403), DatahubRejected, "ERROR"),
    ],
    ids=["timeout", "unreachable", "server-error", "rate-limited", "header-rejected"],
)
def test_failures_raise_a_fixed_error_are_logged_once_and_never_cached(
    caplog: pytest.LogCaptureFixture,
    handler: Handler,
    error: type[Exception],
    level: str,
) -> None:
    datahub = FakeDatahub(handler)

    for _ in range(2):
        with pytest.raises(error):
            run(datahub.client.count())

    assert len(datahub.requests) == 2  # the failure was not cached
    logged = [r for r in caplog.records if r.name.endswith("datahub")]
    assert [r.levelname for r in logged] == [level, level]


def test_an_unexpected_4xx_is_raised_as_a_bug() -> None:
    datahub = FakeDatahub(lambda r: httpx.Response(400, json={"error": "bad query"}))

    with pytest.raises(httpx.HTTPStatusError):
        run(datahub.client.count())


def test_the_header_value_never_appears_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    for handler in (timed_out, unreachable, lambda r: httpx.Response(403)):
        datahub = FakeDatahub(handler)
        with pytest.raises(Exception):  # noqa: B017 (each raises its own error)
            run(datahub.client.count())

    assert caplog.records
    assert HEADER not in caplog.text
    assert all(HEADER not in str(vars(r)) for r in caplog.records)


# --- Building the client -------------------------------------------------------------


def test_without_the_header_no_client_is_built() -> None:
    assert build_datahub_client(Settings(datahub_allow_request=None), "brent") is None


def test_with_the_header_the_client_is_built_from_settings() -> None:
    client = build_datahub_client(Settings(datahub_allow_request=HEADER), "brent")
    assert isinstance(client, DatahubClient)
    run(client.aclose())


def test_tests_never_read_the_datahub_header_from_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("DATAHUB_ALLOW_REQUEST=would-hit-the-real-api\n")
    monkeypatch.chdir(tmp_path)
    assert Settings().datahub_allow_request is None
