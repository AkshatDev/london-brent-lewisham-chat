"""The chat tools: arguments from the YAMLs, queries built in code, a fake Datahub."""

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from london_brent_lewisham_chat.datahub import (
    DatahubClient,
    DatahubUnavailable,
    TTLCache,
)
from london_brent_lewisham_chat.scope import scope_filter
from london_brent_lewisham_chat.semantic import SemanticLayer, load_semantic
from london_brent_lewisham_chat.tools import (
    DESCRIPTION_MAX_CHARS,
    Area,
    DetailsArgs,
    _excluded_records,
    count_applications,
    count_args_model,
    count_filters,
    count_in,
    details_in,
    get_application_details,
)

SEMANTIC = Path(__file__).parents[1] / "semantic"
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def brent() -> SemanticLayer:
    return load_semantic("brent", SEMANTIC)


@pytest.fixture(scope="module")
def args_model(brent: SemanticLayer) -> type[BaseModel]:
    return count_args_model(brent)


def fake_datahub(response: httpx.Response) -> tuple[DatahubClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response

    http = httpx.AsyncClient(
        base_url="https://datahub.test", transport=httpx.MockTransport(handler)
    )
    return DatahubClient(http, "brent", TTLCache(600, 16), now=lambda: T0), requests


def drop_rules(brent: SemanticLayer) -> list[dict[str, Any]]:
    return [
        r for r in brent.cleaning_rules["all_records"] if r["action"] == "drop_records"
    ]


# --- Arguments ---------------------------------------------------------------------


def test_decision_choices_come_from_decision_yaml(
    brent: SemanticLayer, args_model: type[BaseModel]
) -> None:
    decision = args_model.model_json_schema()["properties"]["decision"]
    [choices] = [a["enum"] for a in decision["anyOf"] if "enum" in a]
    assert tuple(choices) == brent.allowed_values("decision")


@pytest.mark.parametrize(
    "bad",
    [
        {"local_authority": "Lewisham", "year": 2024},  # not covered yet
        {"local_authority": "Brent", "year": 1986},
        {"local_authority": "Brent", "year": date.today().year + 1},
        {"local_authority": "Brent", "year": 2024, "decision": "Granted"},
        {"local_authority": "Brent", "year": 2024, "date_basis": "submitted"},
        {
            "local_authority": "Brent",
            "year": 2024,
            "ward": "Kilburn",
        },  # no such argument
    ],
    ids=[
        "other-authority",
        "too-early",
        "future",
        "unknown-decision",
        "basis",
        "extra",
    ],
)
def test_invalid_arguments_are_rejected(
    args_model: type[BaseModel], bad: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        args_model(**bad)


# --- Filters ---------------------------------------------------------------------


def test_dropped_records_are_excluded_as_cleaning_yaml_says(
    brent: SemanticLayer, args_model: type[BaseModel]
) -> None:
    excluded = count_filters(args_model(local_authority="Brent", year=2024), brent)[0]
    must_not = excluded["bool"]["must_not"]

    listed = sorted({i for r in drop_rules(brent) for i in r.get("ids", [])})
    assert {"ids": {"values": listed}} in must_not
    assert {"prefix": {"id": "Brent-55_"}} in must_not  # test references (55/...)
    [empty_rule] = [r for r in drop_rules(brent) if r.get("when_all_null")]
    assert {
        "bool": {
            "must_not": [{"exists": {"field": f}} for f in empty_rule["when_all_null"]]
        }
    } in must_not


@pytest.mark.parametrize(
    ("basis", "field"), [("validated", "valid_date"), ("decided", "decision_date")]
)
def test_the_year_applies_to_the_date_the_basis_names(
    brent: SemanticLayer, args_model: type[BaseModel], basis: str, field: str
) -> None:
    args = args_model(local_authority="Brent", year=2024, date_basis=basis)
    filters = count_filters(args, brent)

    assert filters[1] == {"range": {field: {"gte": "01/01/2024", "lt": "01/01/2025"}}}
    assert len(filters) == 2  # no decision clause without a decision


def test_a_decision_is_a_phrase_match_without_decisions_not_yet_issued(
    brent: SemanticLayer, args_model: type[BaseModel]
) -> None:
    args = args_model(local_authority="Brent", year=2024, decision="Refused")
    clause = count_filters(args, brent)[2]["bool"]

    assert clause["filter"] == [{"match_phrase": {"decision": "Refused"}}]
    [not_issued] = clause["must_not"]
    rule = next(
        r for r in brent.cleaning_rules["derived"] if r["id"] == "decision_not_issued"
    )
    statuses = not_issued["bool"]["filter"][0]["bool"]["should"]
    assert statuses == [{"match_phrase": {"status": s}} for s in rule["when_status"]]
    assert {"exists": {"field": "decision"}} in not_issued["bool"]["filter"]
    assert not_issued["bool"]["must_not"] == [{"exists": {"field": "decision_date"}}]


# --- The tool --------------------------------------------------------------------


def test_the_tool_counts_with_the_scope_filter_first_and_returns_a_cited_result(
    brent: SemanticLayer, args_model: type[BaseModel]
) -> None:
    datahub, requests = fake_datahub(httpx.Response(200, json={"count": 722}))
    args = args_model(local_authority="Brent", year=2024, decision="Refused")

    out = asyncio.run(
        count_applications(args, semantic=brent, datahub=datahub, citation_id="c1")
    )

    [request] = requests
    sent = json.loads(request.content)["query"]["bool"]["filter"]
    assert sent == [scope_filter("brent"), *count_filters(args, brent)]

    assert out.result["count"] == 722
    assert out.result["citation"] == "c1"
    assert out.result["counted"] == (
        "Brent applications validated in 2024 (valid_date) with council decision "
        "Refused, excluding decisions recorded but not yet issued"
    )
    [citation] = out.citations
    assert citation.id == "c1"
    assert citation.text == (
        f"Planning London Datahub API (live): {out.result['counted']}, "
        "retrieved 2026-11-11T12:00:00+00:00."
    )
    assert citation.query == json.loads(request.content)  # the exact body sent
    assert citation.retrieved_at == T0


def test_notes_are_the_rules_from_live_api_yaml_that_apply(
    brent: SemanticLayer, args_model: type[BaseModel]
) -> None:
    def notes(**kwargs: str) -> list[str]:
        datahub, _ = fake_datahub(httpx.Response(200, json={"count": 1}))
        args = args_model(local_authority="Brent", year=2024, **kwargs)
        out = asyncio.run(
            count_applications(args, semantic=brent, datahub=datahub, citation_id="c1")
        )
        return out.result["notes"]

    year_rule = next(f.rule for f in brent.live.default_filters if f.id == "year")
    published = next(
        r.rule for r in brent.live.live_rules if r.id == "counts_use_published_values"
    )
    assert notes() == [" ".join(year_rule.split())]
    assert notes(decision="Refused") == [
        " ".join(year_rule.split()),
        " ".join(published.split()),
    ]


def test_datahub_errors_reach_the_caller_unchanged(
    brent: SemanticLayer, args_model: type[BaseModel]
) -> None:
    datahub, _ = fake_datahub(httpx.Response(502))
    args = args_model(local_authority="Brent", year=2024)

    with pytest.raises(DatahubUnavailable):
        asyncio.run(
            count_applications(args, semantic=brent, datahub=datahub, citation_id="c1")
        )


# --- get_application_details -------------------------------------------------------

TODAY = date(2026, 11, 11)
OUTSIDE = "Elsewhere"  # stands in for any place outside the two boroughs


def raw(**changes: str | None) -> dict[str, Any]:
    """A raw record as the Datahub sends it (dates as DD/MM/YYYY text)."""
    record: dict[str, Any] = {
        "id": "Brent-24_3634",
        "borough": "Brent",
        "lpa_name": "Brent",
        "lpa_app_no": "24/3634",
        "status": "Approved",
        "decision": "Approved",
        "application_type": "Householder",
        "description": "Single storey side extension",
        "appeal_decision": None,
        "url_planning_app": "https://pa.brent.gov.uk/online-applications/x",
        "bo_system": "Uniform",
        "valid_date": "31/12/2024",
        "decision_date": "25/02/2025",
        "decision_target_date": "25/02/2025",
        "appeal_decision_date": None,
        "site_name": None,
        "site_number": "53",
        "street_name": "Burns Road",
        "locality": "London",
        "postcode": "NW10 4DT",
        "last_updated": "2025-03-01T00:00:00",
    }
    return record | changes


def searched(*records: dict[str, Any]) -> httpx.Response:
    hits = [{"_source": record} for record in records]
    return httpx.Response(
        200, json={"hits": {"total": {"value": len(records)}, "hits": hits}}
    )


def details(
    brent: SemanticLayer, reference: str, *records: dict[str, Any]
) -> tuple[Any, list[httpx.Request]]:
    datahub, requests = fake_datahub(searched(*records))
    out = asyncio.run(
        get_application_details(
            DetailsArgs(reference=reference),
            semantic=brent,
            datahub=datahub,
            citation_id="c4",
            today=TODAY,
        )
    )
    return out, requests


def test_details_returns_only_the_returned_fields_cleaned_and_cited(
    brent: SemanticLayer,
) -> None:
    out, _ = details(brent, "24/3634", raw())

    assert out.result["found"] is True
    application = out.result["application"]
    assert set(application) == {ref.field for ref in brent.live.returned}
    assert "bo_system" not in application and "site_number" not in application
    assert application["valid_date"] == "2024-12-31"  # cleaned: a real date
    assert application["address"] == "53, Burns Road, London, NW10 4DT"  # from 5 parts
    assert out.result["notes"] == []
    [citation] = out.citations
    assert citation.id == "c4"
    assert citation.text == (
        "Planning London Datahub, application 24/3634 (record Brent-24_3634), "
        "retrieved 2026-11-11T12:00:00+00:00. Documents and decision notice: "
        "https://pa.brent.gov.uk/online-applications/x"
    )


def test_details_searches_the_reference_with_scope_exclusions_and_fields(
    brent: SemanticLayer,
) -> None:
    _, [request] = details(brent, "24/3634", raw())

    body = json.loads(request.content)
    assert body["query"]["bool"]["filter"] == [
        scope_filter("brent"),
        {
            "bool": {
                "should": [{"match_phrase": {"lpa_app_no": "24/3634"}}],
                "minimum_should_match": 1,
            }
        },
        _excluded_records(brent),
    ]
    assert body["_source"] == brent.live.request_fields
    assert body["size"] == 5


def test_an_unknown_reference_is_not_found_and_not_cited(brent: SemanticLayer) -> None:
    out, _ = details(brent, "99/9999")

    assert out.result == {"found": False, "reference": "99/9999"}
    assert out.citations == []


def test_the_exact_reference_wins_over_a_longer_one(brent: SemanticLayer) -> None:
    longer = raw(id="OPDC-21_0044_DELBRE", lpa_name="OPDC", lpa_app_no="21/0044/DELBRE")
    exact = raw(id="Brent-21_0044", lpa_app_no="21/0044")

    only_longer, _ = details(brent, "21/0044", longer)
    both, _ = details(brent, "21/0044", longer, exact)

    assert only_longer.result["found"] is False  # "21/0044" is not "21/0044/DELBRE"
    assert both.result["application"]["id"] == "Brent-21_0044"


def test_references_match_however_they_are_spaced_or_cased(
    brent: SemanticLayer,
) -> None:
    out, _ = details(brent, " 21 / 0044 / delbre ", raw(lpa_app_no="21/0044/DELBRE"))

    assert out.result["found"] is True


@pytest.mark.parametrize(
    "reference", ["", "   ", "x" * 41, "18/1022; drop", "<script>"]
)
def test_invalid_references_are_rejected(reference: str) -> None:
    with pytest.raises(ValidationError):
        DetailsArgs(reference=reference)


def test_a_deleted_appeal_is_cleaned_noted_and_cites_the_portal_check(
    brent: SemanticLayer,
) -> None:
    out, _ = details(
        brent,
        "24/0041",
        raw(
            lpa_app_no="24/0041",
            status="ZZZ",
            appeal_decision="ZZZ",
            decision="Refused",
            appeal_decision_date="01/06/2025",
        ),
    )

    application = out.result["application"]
    assert application["status"] == "Refused"  # ZZZ cleaned, as in cleaning.yaml
    assert application["appeal_decision"] is None
    assert application["appeal_record_deleted"] is True
    rules = brent.field_doc("appeal_record_deleted").rules
    assert out.result["notes"] == [" ".join(rule.split()) for rule in rules]
    assert out.citations[0].text.endswith(
        "the Planning London Datahub shows status ZZZ, decision Refused, "
        "appeal decision ZZZ."
    )


@pytest.mark.parametrize(
    ("target", "stale"), [("01/01/2024", True), ("01/06/2026", False)]
)
def test_status_stale_is_measured_from_today(
    brent: SemanticLayer, target: str, stale: bool
) -> None:
    pending = raw(
        status="Application Received",
        decision=None,
        decision_date=None,
        decision_target_date=target,
    )

    out, _ = details(brent, "24/3634", pending)

    assert out.result["application"]["status_stale"] is stale
    assert bool(out.result["notes"]) is stale  # the stale note only when stale


def test_a_long_description_is_cut_to_the_cap(brent: SemanticLayer) -> None:
    out, _ = details(
        brent, "24/3634", raw(description="x" * (DESCRIPTION_MAX_CHARS + 500))
    )

    assert len(out.result["application"]["description"]) == DESCRIPTION_MAX_CHARS


def test_a_record_the_cleaning_rules_drop_is_not_found(brent: SemanticLayer) -> None:
    [test_rule] = [r for r in drop_rules(brent) if r["id"] == "drop_test_records"]
    test_id = test_rule["ids"][0]
    reference = test_id.split("-", 1)[1].replace("_", "/")

    out, _ = details(brent, reference, raw(id=test_id, lpa_app_no=reference))

    assert out.result["found"] is False


def test_an_out_of_scope_record_never_reaches_the_model(
    brent: SemanticLayer, caplog: pytest.LogCaptureFixture
) -> None:
    outsider = raw(
        id="Elsewhere-24_3634",
        borough=OUTSIDE,
        lpa_name=OUTSIDE,
        description="A record from elsewhere",
    )

    out, _ = details(brent, "24/3634", outsider)

    assert out.result == {"found": False, "reference": "24/3634"}
    assert out.citations == []
    assert "elsewhere" not in json.dumps(out.result).lower()
    assert any(
        r.levelname == "WARNING" and r.getMessage() == "out-of-scope record dropped"
        for r in caplog.records
    )


# --- Two boroughs ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def lewisham_layer() -> SemanticLayer:
    return load_semantic("lewisham", SEMANTIC)


def test_count_choices_cover_both_boroughs(
    brent: SemanticLayer, lewisham_layer: SemanticLayer
) -> None:
    model = count_args_model([brent, lewisham_layer])
    schema = model.model_json_schema()["properties"]
    assert set(schema["local_authority"]["enum"]) == {"Brent", "Lewisham"}
    [choices] = [a["enum"] for a in schema["decision"]["anyOf"] if "enum" in a]
    assert "Not Required" in choices  # Lewisham's own value, from its decision.yaml


def test_a_decision_a_borough_does_not_use_goes_back_as_an_error(
    brent: SemanticLayer, lewisham_layer: SemanticLayer
) -> None:
    model = count_args_model([brent, lewisham_layer])
    datahub, requests = fake_datahub(httpx.Response(200, json={"count": 1}))
    areas = {"brent": Area(brent, datahub), "lewisham": Area(lewisham_layer, datahub)}

    out = asyncio.run(
        count_in(
            model(local_authority="Brent", year=2024, decision="Not Required"),
            areas=areas,
            citation_id="c1",
        )
    )

    assert out.result == {"error": "'Not Required' is not a decision Brent uses"}
    assert requests == []  # never sent to the Datahub


def test_an_excluded_lewisham_record_is_explained_not_just_missing(
    lewisham_layer: SemanticLayer,
) -> None:
    datahub, _ = fake_datahub(searched())  # dropped records never come back
    areas = {"lewisham": Area(lewisham_layer, datahub)}

    out = asyncio.run(
        details_in(DetailsArgs(reference="DC/26/142577"), areas=areas, citation_id="c1")
    )

    assert out.result["found"] is False
    assert out.result["excluded"] is True
    assert "removed or restricted from public viewing" in out.result["reason"]
    assert out.citations == []


@pytest.mark.parametrize(
    ("asked", "forms"),
    [
        ("DC/21/120061", ["DC/21/120061", "21/120061"]),
        ("21/120061", ["21/120061", "DC/21/120061"]),
    ],
)
def test_lewisham_lookups_try_the_reference_with_and_without_dc(
    lewisham_layer: SemanticLayer, asked: str, forms: list[str]
) -> None:
    client, requests = fake_datahub(searched())
    asyncio.run(
        get_application_details(
            DetailsArgs(reference=asked),
            semantic=lewisham_layer,
            datahub=client,
            citation_id="c1",
        )
    )
    should = json.loads(requests[0].content)["query"]["bool"]["filter"][1]["bool"][
        "should"
    ]
    assert [clause["match_phrase"]["lpa_app_no"] for clause in should] == forms


def test_details_searches_every_borough_when_none_is_named(
    brent: SemanticLayer, lewisham_layer: SemanticLayer
) -> None:
    brent_hub, brent_requests = fake_datahub(searched(raw()))
    lewisham_hub, lewisham_requests = fake_datahub(searched())
    areas = {
        "brent": Area(brent, brent_hub),
        "lewisham": Area(lewisham_layer, lewisham_hub),
    }

    out = asyncio.run(
        details_in(DetailsArgs(reference="24/3634"), areas=areas, citation_id="c1")
    )

    assert out.result["found"] is True
    assert (len(brent_requests), len(lewisham_requests)) == (1, 1)
