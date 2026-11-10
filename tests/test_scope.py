"""The golden rule: only Brent and Lewisham, by location, enforced in code."""

import gzip
import json
from pathlib import Path

import httpx
import pytest

from london_brent_lewisham_chat.pipeline import extract
from london_brent_lewisham_chat.scope import in_scope, scope_filter, scoped_query

OUTSIDE = "Elsewhere"  # stands in for any place outside the two boroughs


def test_scope_filter_matches_borough_or_missing_borough_with_lpa_name() -> None:
    should = scope_filter("brent")["bool"]["should"]
    assert {"term": {"borough.raw": "Brent"}} in should
    fallback = should[1]["bool"]
    assert fallback["filter"] == [{"term": {"lpa_name.raw": "Brent"}}]
    assert fallback["must_not"] == [{"exists": {"field": "borough.raw"}}]


def test_scoped_query_always_starts_with_the_scope_filter() -> None:
    extra = {"term": {"status.raw": "Approved"}}
    query = scoped_query("brent", extra)
    assert query["bool"]["filter"] == [scope_filter("brent"), extra]
    assert scoped_query("lewisham")["bool"]["filter"] == [scope_filter("lewisham")]


@pytest.mark.parametrize("borough", [OUTSIDE.lower(), "Brent", "", "brent,elsewhere"])
def test_a_query_cannot_be_built_for_a_borough_outside_the_allow_list(
    borough: str,
) -> None:
    with pytest.raises(ValueError, match="out of scope"):
        scoped_query(borough)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"borough": "Brent", "lpa_name": "Brent"}, True),
        (
            {"borough": "Brent", "lpa_name": "OPDC"},
            True,
        ),  # by location, not who decided
        ({"borough": None, "lpa_name": "Brent"}, True),
        ({"lpa_name": "Brent"}, True),
        ({"borough": OUTSIDE, "lpa_name": "Brent"}, False),  # located elsewhere
        ({"borough": None, "lpa_name": "OPDC"}, False),
        (
            {"borough": "Brent Cross South", "lpa_name": OUTSIDE},
            False,
        ),  # contains, not is
        (
            {"borough": "Lewisham", "lpa_name": "Lewisham"},
            False,
        ),  # other allowed borough
    ],
)
def test_in_scope_follows_the_location_rule(record: dict, expected: bool) -> None:
    assert in_scope(record, "brent") is expected


def _fake_datahub(pages: list[list[dict]]) -> httpx.Client:
    """A Datahub stand-in that serves ``pages`` and records every query it gets."""
    queries: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        queries.append(body["query"])
        if request.url.path.endswith("/_count"):
            return httpx.Response(200, json={"count": sum(len(p) for p in pages)})
        page = pages.pop(0) if pages else []
        hits = [{"_source": r, "sort": [r["id"]]} for r in page]
        return httpx.Response(200, json={"hits": {"hits": hits}})

    client = httpx.Client(
        base_url="https://datahub.test", transport=httpx.MockTransport(handler)
    )
    client.queries = queries  # type: ignore[attr-defined]
    return client


def test_every_upstream_query_carries_the_scope_filter() -> None:
    client = _fake_datahub([[{"id": "Brent-1", "borough": "Brent"}]])
    list(extract.fetch_all(client, "brent"))
    assert client.queries  # type: ignore[attr-defined]
    for query in client.queries:  # type: ignore[attr-defined]
        assert query["bool"]["filter"][0] == scope_filter("brent")


def test_out_of_scope_records_from_upstream_are_dropped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = [{"id": "Brent-1", "borough": "Brent"}, {"id": "X-1", "borough": OUTSIDE}]
    records = list(extract.fetch_all(_fake_datahub([page]), "brent"))
    assert [r["id"] for r in records] == ["Brent-1"]
    assert any(r.levelname == "WARNING" and r.id == "X-1" for r in caplog.records)


def test_a_snapshot_with_an_out_of_scope_record_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "applications_2026-11-10.jsonl.gz"
    with gzip.open(path, "wt") as f:
        f.write(json.dumps({"id": "Brent-1", "borough": "Brent"}) + "\n")
        f.write(json.dumps({"id": "X-1", "borough": OUTSIDE}) + "\n")
    with pytest.raises(ValueError, match="out-of-scope record 'X-1'"):
        list(extract.read_snapshot(path, "brent"))
