"""Load-time cleaning follows semantic/brent/cleaning.yaml."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from london_brent_lewisham_chat.pipeline import clean

RULES = clean.load_rules(
    Path(__file__).parents[1] / "semantic" / "brent" / "cleaning.yaml"
)
NO_EXPECTED_COUNTS = {
    **RULES,
    "all_records": [
        {k: v for k, v in r.items() if k != "expected_count"}
        for r in RULES["all_records"]
    ],
    "fields": {
        f: [{k: v for k, v in r.items() if k != "expected_count"} for r in rs]
        for f, rs in RULES["fields"].items()
    },
    "derived": [
        {k: v for k, v in r.items() if k != "expected_count"} for r in RULES["derived"]
    ],
}


def record(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    """A minimal real-looking Brent record, with ``fields`` overriding the defaults."""
    base = {
        "id": "Brent-24_0001",
        "borough": "Brent",
        "lpa_name": "Brent",
        "lpa_app_no": "24/0001",
        "status": "Approved",
        "decision": "Approved",
        "decision_date": "02/03/2024",
        "valid_date": "05/01/2024",
        "description": "Single storey rear extension",
        "site_name": "1 Example Road, Wembley, HA0 1AA",
        "bo_system": "Uniform",
        "centroid": {"lat": 51.55, "lon": -0.29},
    }
    return base | fields


def run(*records: dict[str, Any], centroids: dict | None = None) -> clean.Result:
    """Clean records without the snapshot-wide expected counts."""
    return clean.clean_all(records, NO_EXPECTED_COUNTS, "brent", centroids or {})


def test_text_is_stripped_and_blank_text_becomes_null() -> None:
    row = run(record(decision="\tApproved", ward="  "))
    assert row.rows[0]["decision"] == "Approved"
    assert row.rows[0]["ward"] is None


def test_all_eight_test_records_are_dropped() -> None:
    ids = ["Brent-55_0003", "Brent-55_0015", "Brent-55_0016", "Brent-55_0100",
           "Brent-55_0200", "Brent-55_5561", "Brent-55_5562", "Brent-55_5563"]  # fmt: skip
    result = run(*(record(id=i, lpa_app_no=i[6:].replace("_", "/")) for i in ids))
    assert result.rows == []
    assert result.dropped["drop_test_records"] == 8


def test_an_unlisted_55_reference_stops_the_load() -> None:
    with pytest.raises(ValueError, match="unlisted 55/"):
        run(record(id="Brent-55_9999", lpa_app_no="55/9999"))


def test_empty_records_and_records_located_outside_brent_are_dropped() -> None:
    empty = record(
        id="Brent-1", status=None, decision=None, decision_date=None, description=None
    )
    outside = record(
        id="Hammersmith_&_Fulham-2025_01931_FUL"
    )  # borough says Brent, site isn't
    result = run(empty, outside)
    assert result.rows == []
    assert result.dropped == {"drop_empty_records": 1, "drop_outside_borough": 1}


def test_out_of_scope_record_is_rejected_not_cleaned() -> None:
    with pytest.raises(ValueError, match="out-of-scope"):
        run(record(borough="Elsewhere"))


def test_status_variants_are_merged_and_empty_status_is_unknown() -> None:
    rows = run(
        record(id="Brent-1", status="lapsed"),
        record(id="Brent-2", status="Appeal in Progress"),
        record(id="Brent-3", status=" "),
    ).rows
    assert [r["status"] for r in rows] == ["Lapsed", "Appeal In Progress", "Unknown"]


def test_broken_portal_url_gets_its_hyphen_back() -> None:
    url = "https://pa.brent.gov.uk/online applications/applicationDetails.do?keyVal=X"
    row = run(record(url_planning_app=url)).rows[0]
    assert row["url_planning_app"].startswith(
        "https://pa.brent.gov.uk/online-applications/"
    )


def test_dates_are_parsed_and_1987_legacy_dates_are_flagged_approximate() -> None:
    rows = run(record(), record(id="Brent-861288", decision_date="01/02/1987")).rows
    assert rows[0]["decision_date"] == date(2024, 3, 2)
    assert [r["approximate_decision_date"] for r in rows] == [False, True]


@pytest.mark.parametrize(
    ("site_name", "kind"),
    [
        (None, "missing"),
        ("24A", "number_only"),
        ("85-91", "number_only"),
        ("Street Record, Acton Lane, London", "street_record"),
        ("1 Example Road, Wembley, HA0 1AA", "with_full_postcode"),
        ("Land next to Haynes House, Haynes Road, Wembley", "several_parts"),
        ("York House", "one_part"),
    ],
)
def test_address_kind(site_name: str | None, kind: str) -> None:
    assert clean.address_kind(site_name) == kind


def test_short_site_name_is_completed_from_the_other_address_fields() -> None:
    row = run(
        record(
            site_name="2",
            site_number=2.0,
            street_name="Example Road",
            locality=", Wembley",
            postcode="HA0 1AA",
        )
    ).rows[0]
    assert row["address"] == "2, Example Road, Wembley, HA0 1AA"
    assert row["address_kind"] == "number_only"


def test_legacy_flag_comes_from_bo_system() -> None:
    rows = run(
        record(id="Brent-1"), record(id="Brent-2", bo_system="FME Legacy Data Import")
    ).rows
    assert [r["legacy_record"] for r in rows] == [False, True]


def test_placeholder_centroid_is_replaced_by_the_postcode_centroid_or_null() -> None:
    sea = {"lat": 49.76681, "lon": -0.0}
    rows = run(
        record(id="Brent-1", centroid=sea, postcode="HA0 1AA"),
        record(id="Brent-2", centroid=sea, site_name="Somewhere, Wembley"),
        centroids={"HA0 1AA": {"lat": 51.55, "lon": -0.30}},
    ).rows
    assert (rows[0]["lat"], rows[0]["centroid_source"]) == (51.55, "postcodes.io")
    assert (rows[1]["lat"], rows[1]["centroid_source"]) == (None, None)
    assert all(r["placeholder_centroid"] for r in rows)


def test_an_expected_count_that_does_not_match_stops_the_load() -> None:
    with pytest.raises(ValueError, match="drop_empty_records: expected 407"):
        clean.clean_all([record()], RULES, "brent", {})


def test_decision_recorded_before_the_application_is_decided_is_flagged() -> None:
    rows = run(
        record(
            id="Brent-1", status="Application Under Consideration", decision_date=None
        ),
        record(
            id="Brent-2",
            status="Application Received",
            decision=None,
            decision_date=None,
        ),
        record(id="Brent-3"),
    ).rows
    assert [r["decision_not_issued"] for r in rows] == [True, False, False]
    assert rows[0]["decision"] == "Approved"  # kept as the Datahub gives it


def test_portal_confirmed_outcome_fills_empty_status_and_decision_only() -> None:
    empty = {"status": None, "decision": None}
    rows = run(
        record(id="Brent-18_0557", **empty),  # portal: refused
        record(
            id="Brent-18_0025", status="Approved", decision="Approved"
        ),  # Datahub wins
        record(id="Brent-18_3324", **empty),  # portal has no decision either
    ).rows
    assert [(r["status"], r["decision"], r["outcome_source"]) for r in rows] == [
        ("Refused", "Refused", "portal_manual_check"),
        ("Approved", "Approved", "datahub"),
        ("Unknown", None, "datahub"),
    ]


def test_zzz_deleted_appeal_becomes_the_councils_refusal_with_a_flag() -> None:
    rows = run(
        record(
            id="Brent-1",
            status="ZZZ",
            decision="Refused",
            appeal_decision="ZZZ",
            appeal_decision_date="08/08/2018",
        ),
        record(id="Brent-2"),
    ).rows
    deleted, normal = rows
    assert (deleted["status"], deleted["appeal_decision"]) == ("Refused", None)
    assert deleted["appeal_decision_date"] is None
    assert (deleted["appeal_record_deleted"], normal["appeal_record_deleted"]) == (
        True,
        False,
    )


def test_portal_outcome_for_a_stale_record_sets_several_fields_while_still_pending() -> (
    None
):
    pending = {
        "status": "Application Received",
        "decision": None,
        "decision_date": None,
    }
    rows = run(
        record(id="Brent-23_0650", **pending),
        record(id="Brent-20_4264", **pending),
        record(
            id="Brent-23_0650", status="Refused", decision="Refused"
        ),  # Datahub wins
    ).rows
    assert (rows[0]["status"], rows[0]["decision_date"]) == (
        "Approved",
        date(2023, 4, 21),
    )
    assert (rows[1]["status"], rows[1]["appeal_decision"], rows[1]["decision"]) == (
        "Allowed",
        "Allowed",
        None,
    )
    assert rows[2]["status"] == "Refused"
    assert rows[2]["outcome_source"] == "datahub"
