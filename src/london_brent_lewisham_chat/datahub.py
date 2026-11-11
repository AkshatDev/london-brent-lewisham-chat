"""The live Planning London Datahub, as the chat tools use it: scoped, cached, bounded.

Every request body is built here with ``scoped_query``, so no query can leave
without the borough filter, and every record that comes back is checked again with
``in_scope``. Answers are cached in memory for a short time (the brief's "basic
caching"). What a query *means* (dates, decisions, default filters) is decided by
the tools, from the semantic layer; cleaning happens there too.
"""

import json
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import httpx
import logfire

from .config import Settings
from .scope import Borough, in_scope, scoped_query

logger = logging.getLogger(__name__)

MAX_SEARCH_SIZE = 10
"""Most records one search returns: enough to list examples, small enough to keep
the model's input (and cost) low."""


class DatahubError(Exception):
    """The Datahub could not answer; the message is fixed, never upstream text."""


class DatahubTimeout(DatahubError):
    """The Datahub did not answer in time (the chat endpoint answers 504)."""


class DatahubUnavailable(DatahubError):
    """The Datahub could not be reached, failed, or rate-limited us (503)."""


class DatahubRejected(DatahubError):
    """The Datahub refused the request header: a configuration problem (503)."""


@dataclass(frozen=True)
class CountResult:
    """How many in-scope applications match a query."""

    count: int
    query: dict[str, Any]
    """The exact query sent, for the citation."""
    retrieved_at: datetime
    """When the Datahub answered (kept when the answer is served from the cache)."""
    cached: bool


@dataclass(frozen=True)
class SearchResult:
    """Up to ``MAX_SEARCH_SIZE`` matching records, raw but in scope, and the total."""

    total: int
    records: list[dict[str, Any]]
    query: dict[str, Any]
    retrieved_at: datetime
    cached: bool


class TTLCache:
    """A small in-memory cache: entries expire, and the oldest goes when it is full.

    Per process and lost on restart. Two identical requests arriving together may
    both reach the Datahub; that's accepted for basic caching (no locking).
    """

    def __init__(
        self,
        ttl_s: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an empty cache.

        Args:
            ttl_s: Seconds an entry stays valid.
            max_entries: Most entries kept; the oldest is dropped beyond that.
            clock: Seconds from a steady clock; tests pass a fake one.
        """
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:  # noqa: ANN401 (results of any type)
        """Return the value stored under ``key``, or ``None`` if absent or expired.

        Args:
            key: The cache key.

        Returns:
            The stored value, or ``None``.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    def put(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Store ``value`` under ``key``, dropping the oldest entry if full.

        Args:
            key: The cache key.
            value: The value to keep.
        """
        self._entries.pop(key, None)
        self._entries[key] = (self._clock() + self._ttl_s, value)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class DatahubClient:
    """Counts and searches applications for one borough on the live Datahub."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        borough: Borough,
        cache: TTLCache,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Wrap an HTTP client that already has the base URL, header and timeout.

        Args:
            http: The HTTP client (tests pass one with a fake transport).
            borough: The borough every query is limited to.
            cache: Where answers are kept between identical queries.
            now: The current time, for ``retrieved_at``; tests pass a fixed one.
        """
        self._http = http
        self._borough = borough
        self._cache = cache
        self._now = now

    async def count(self, *filters: dict[str, Any]) -> CountResult:
        """Count the in-scope applications matching ``filters``.

        Args:
            *filters: Elasticsearch filter clauses, added after the scope filter.

        Returns:
            The count, the query sent and when it was retrieved.

        Raises:
            DatahubTimeout: If the Datahub did not answer in time.
            DatahubUnavailable: If it could not be reached, failed or rate-limited.
            DatahubRejected: If it refused the request header.
        """
        body = {"query": scoped_query(self._borough, *filters)}
        with logfire.span("datahub {endpoint}", endpoint="_count", query=body) as span:
            key = _cache_key("_count", body)
            if (hit := self._cache.get(key)) is not None:
                span.set_attributes({"cache_hit": True, "count": hit.count})
                return replace(hit, cached=True)
            data = await self._post("_count", body)
            result = CountResult(
                count=data["count"], query=body, retrieved_at=self._now(), cached=False
            )
            self._cache.put(key, result)
            span.set_attributes({"cache_hit": False, "count": result.count})
            return result

    async def search(
        self,
        *filters: dict[str, Any],
        size: int = MAX_SEARCH_SIZE,
        sort: list[dict[str, Any]] | None = None,
        fields: list[str] | None = None,
    ) -> SearchResult:
        """Find in-scope applications matching ``filters``, with the total count.

        Args:
            *filters: Elasticsearch filter clauses, added after the scope filter.
            size: Records wanted; capped at ``MAX_SEARCH_SIZE``.
            sort: Elasticsearch sort clauses, e.g. newest first.
            fields: Raw fields to download (``_source``); ``None`` means all.

        Returns:
            The in-scope records, the total number of matches, the query sent and
            when it was retrieved. Out-of-scope records are dropped and logged.

        Raises:
            DatahubTimeout: If the Datahub did not answer in time.
            DatahubUnavailable: If it could not be reached, failed or rate-limited.
            DatahubRejected: If it refused the request header.
        """
        body: dict[str, Any] = {
            "query": scoped_query(self._borough, *filters),
            "size": max(0, min(size, MAX_SEARCH_SIZE)),
            # Without this, Elasticsearch stops counting the total at 10,000.
            "track_total_hits": True,
        }
        if sort:
            body["sort"] = sort
        if fields is not None:
            body["_source"] = fields
        with logfire.span("datahub {endpoint}", endpoint="_search", query=body) as span:
            key = _cache_key("_search", body)
            if (hit := self._cache.get(key)) is not None:
                span.set_attributes({"cache_hit": True, "total": hit.total})
                return replace(hit, cached=True)
            data = await self._post("_search", body)
            result = SearchResult(
                total=data["hits"]["total"]["value"],
                records=self._in_scope(
                    [hit["_source"] for hit in data["hits"]["hits"]]
                ),
                query=body,
                retrieved_at=self._now(),
                cached=False,
            )
            self._cache.put(key, result)
            span.set_attributes({"cache_hit": False, "total": result.total})
            return result

    async def aclose(self) -> None:
        """Close the HTTP connections (at shutdown)."""
        await self._http.aclose()

    def _in_scope(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep only records in scope; log any the Datahub should not have sent.

        Args:
            records: Raw records from a search.

        Returns:
            The records in scope for this client's borough.
        """
        kept = []
        for record in records:
            if in_scope(record, self._borough):
                kept.append(record)
            else:
                logger.warning(
                    "out-of-scope record dropped",
                    extra={"id": record.get("id"), "borough": record.get("borough")},
                )
        return kept

    async def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send one request and turn failures into the errors above (logged once).

        A 4xx other than 401/403/429 means our query is wrong: a bug, so it is not
        caught here and becomes a 500.

        Args:
            endpoint: ``_count`` or ``_search``.
            body: The request body.

        Returns:
            The parsed JSON answer.

        Raises:
            DatahubTimeout: If the Datahub did not answer in time.
            DatahubUnavailable: If it could not be reached, failed or rate-limited.
            DatahubRejected: If it refused the request header.
        """
        fields: dict[str, Any] = {"endpoint": endpoint}
        try:
            response = await self._http.post(f"/applications/{endpoint}", json=body)
        except httpx.TimeoutException as exc:
            logger.warning("datahub timed out", extra=fields | _error(exc))
            raise DatahubTimeout("the Datahub did not answer in time") from None
        except httpx.TransportError as exc:
            logger.warning("datahub unreachable", extra=fields | _error(exc))
            raise DatahubUnavailable("the Datahub could not be reached") from None

        fields["datahub_status"] = response.status_code
        if response.status_code in (401, 403):
            # ERROR: retrying won't help, a person must fix DATAHUB_ALLOW_REQUEST.
            logger.error("datahub rejected the request", extra=fields)
            raise DatahubRejected("the Datahub rejected the request header")
        if response.status_code == 429 or response.status_code >= 500:
            logger.warning("datahub unavailable", extra=fields)
            raise DatahubUnavailable("the Datahub is unavailable right now")
        response.raise_for_status()
        return response.json()


def build_datahub_client(settings: Settings, borough: Borough) -> DatahubClient | None:
    """Create the Datahub client from the app's settings.

    Args:
        settings: The app's settings.
        borough: The borough every query is limited to.

    Returns:
        A client ready to use, or ``None`` when ``DATAHUB_ALLOW_REQUEST`` is not
        set: the app still runs, and the tools answer that they are not configured.
    """
    if settings.datahub_allow_request is None:
        return None
    http = httpx.AsyncClient(
        base_url=settings.datahub_url,
        headers={
            "X-API-AllowRequest": settings.datahub_allow_request.get_secret_value()
        },
        timeout=settings.datahub_timeout_s,
        # One retry when a connection can't be opened; a timeout is not retried.
        transport=httpx.AsyncHTTPTransport(retries=1),
    )
    cache = TTLCache(settings.datahub_cache_ttl_s, settings.datahub_cache_max_entries)
    return DatahubClient(http, borough, cache)


def _cache_key(endpoint: str, body: dict[str, Any]) -> str:
    """Build the cache key: the same query always gives the same key.

    Args:
        endpoint: ``_count`` or ``_search``.
        body: The request body.

    Returns:
        The endpoint and the body as JSON with sorted keys.
    """
    return endpoint + json.dumps(body, sort_keys=True, default=str)


def _error(exc: Exception) -> dict[str, str]:
    """Pick the safe log field from an HTTP error: its class, never its text.

    Args:
        exc: The error ``httpx`` raised.

    Returns:
        ``{"error": <class name>}``.
    """
    return {"error": type(exc).__name__}
