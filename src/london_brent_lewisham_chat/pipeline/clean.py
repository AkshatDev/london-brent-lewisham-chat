"""Apply the load-time rules in ``semantic/<borough>/cleaning.yaml`` to raw records.

The rules live in that one file so the EDA never has to be redone and the code
never disagrees with the documentation: record ids, value maps and expected counts
are read from it, and only the derivations (address, legacy flag) are written here.
"""

import json
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from ..scope import Borough, borough_name, in_scope
from . import postcodes

logger = logging.getLogger(__name__)

DATE_FIELDS = [
    "valid_date",
    "decision_date",
    "decision_target_date",
    "appeal_decision_date",
]
"""Fields the Datahub sends as ``dd/mm/yyyy`` text."""

TEXT_FIELDS = [
    "lpa_app_no",
    "lpa_name",
    "status",
    "decision",
    "application_type",
    "description",
    "appeal_decision",
    "url_planning_app",
    "ward",
    "bo_system",
]
"""Text fields loaded as they are, after the field rules."""

ADDRESS_PARTS = ["site_name", "site_number", "street_name", "locality", "postcode"]
"""The five raw address fields, combined into ``address`` and not loaded themselves."""

FULL_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")
NUMBER_ONLY = re.compile(r"\d+[A-Za-z]?(\s*-\s*\d+[A-Za-z]?)?")


@dataclass
class Result:
    """What cleaning did to a batch of records."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    """Cleaned rows ready to load."""

    dropped: Counter[str] = field(default_factory=Counter)
    """How many records each drop rule removed."""

    applied: Counter[str] = field(default_factory=Counter)
    """How many values each field rule changed."""


def load_rules(path: Path) -> dict[str, Any]:
    """Read a borough's cleaning rules.

    Args:
        path: The ``cleaning.yaml`` file.

    Returns:
        The parsed rules.
    """
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def tidy_text(value: Any) -> Any:  # noqa: ANN401 (raw JSON values have any type)
    """Strip a text value and turn an empty result into ``None``.

    Rules ``strip_whitespace`` and ``blank_to_null``; other types pass through.

    Args:
        value: A raw field value.

    Returns:
        The stripped text, ``None`` for blank text, or the value unchanged.
    """
    if not isinstance(value, str):
        return value
    value = value.strip()
    return value or None


def parse_date(value: str | None) -> date | None:
    """Parse a Datahub ``dd/mm/yyyy`` date.

    Fails loudly on any other format: a silently dropped date would look like a
    real gap.

    Args:
        value: The tidied text, or ``None``.

    Returns:
        The date, or ``None`` if there is none.
    """
    return None if value is None else datetime.strptime(value, "%d/%m/%Y").date()


def number_as_text(value: Any) -> str | None:  # noqa: ANN401
    """Store ``site_number`` as text, whole numbers without a decimal part (2 → "2").

    Args:
        value: A tidied ``site_number`` (text, int, float or ``None``).

    Returns:
        The value as text, or ``None``.
    """
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return None if value is None else str(value)


def address_kind(site_name: str | None) -> str:
    """Sort a tidied ``site_name`` into one kind (derived rule ``address``).

    Args:
        site_name: The tidied site name.

    Returns:
        One of ``missing``, ``number_only``, ``street_record``,
        ``with_full_postcode``, ``several_parts``, ``one_part``.
    """
    if site_name is None:
        return "missing"
    if NUMBER_ONLY.fullmatch(site_name):
        return "number_only"
    if site_name.lower().startswith("street record"):
        return "street_record"
    if FULL_POSTCODE.search(site_name.upper()):
        return "with_full_postcode"
    if "," in site_name:
        return "several_parts"
    return "one_part"


def build_address(parts: Mapping[str, str | None]) -> tuple[str | None, str]:
    """Combine the five address fields into one ``address`` and its kind.

    When ``site_name`` is already a full address it is used as it is; otherwise the
    other parts complete it, so the bot has one column to read.

    Args:
        parts: The tidied five address fields (``site_number`` already as text).

    Returns:
        The address (or ``None`` if every part is empty) and its kind.
    """
    kind = address_kind(parts["site_name"])
    if kind in ("with_full_postcode", "several_parts", "street_record"):
        return parts["site_name"], kind
    pieces = [parts["site_name"]]
    if parts["site_number"] != parts["site_name"]:
        pieces.append(parts["site_number"])
    pieces += [parts["street_name"], parts["locality"], parts["postcode"]]
    # locality has values like "London," and ", Wembley"
    cleaned = [p.strip(" ,") for p in pieces if p]
    return ", ".join(p for p in cleaned if p) or None, kind


def _ids(rules: Mapping[str, Any], rule_id: str) -> set[str]:
    """Return the record ids listed under one ``all_records`` or field rule.

    Args:
        rules: The parsed cleaning rules.
        rule_id: The rule's ``id``.

    Returns:
        The ids the rule lists; empty if the borough has no such rule (rules such as
        ``early_1987_approximate`` exist only where the data needs them).
    """
    every = [*rules["all_records"], *(r for rs in rules["fields"].values() for r in rs)]
    for rule in every:
        if rule["id"] == rule_id:
            return set(rule.get("ids", []))
    return set()


def _drop_reason(record: Mapping[str, Any], rules: Mapping[str, Any]) -> str | None:
    """Return why a tidied record is dropped, or ``None`` to keep it.

    Args:
        record: One record after ``tidy_text``.
        rules: The parsed cleaning rules.

    A reference starting with the borough's test marker (``reference_prefix`` on
    ``drop_test_records``: "55/" in Brent, "TEST" in Lewisham) but not on the list is
    a new test record: dropped and logged as a WARNING, so someone looks at it. (The
    database load used to stop here; live, a lookup must not fail.)

    Returns:
        The id of the drop rule that matched, or ``None``.
    """
    for rule in rules["all_records"]:
        if rule["action"] != "drop_records":
            continue
        if record["id"] in rule.get("ids", []):
            return rule["id"]
        if "when_all_null" in rule and all(
            record.get(k) is None for k in rule["when_all_null"]
        ):
            return rule["id"]
    prefix = test_reference_prefix(rules)
    if prefix and (record.get("lpa_app_no") or "").startswith(prefix):
        logger.warning(
            "unlisted test reference dropped",
            extra={"id": record["id"], "prefix": prefix},
        )
        return "drop_test_records"
    return None


def test_reference_prefix(rules: Mapping[str, Any]) -> str | None:
    """Return the borough's test-reference marker, from ``drop_test_records``.

    Args:
        rules: The parsed cleaning rules.

    Returns:
        The ``reference_prefix`` (e.g. "55/"), or ``None`` if the borough has none.
    """
    rule = next(
        (r for r in rules["all_records"] if r["id"] == "drop_test_records"), None
    )
    return rule.get("reference_prefix") if rule else None


def _apply_field_rules(
    row: dict[str, Any], rules: Mapping[str, Any], applied: Counter[str]
) -> None:
    """Apply the ``fields`` rules (map, replace, fill_null, copy_when_null) in place.

    ``copy_when_null`` copies the ``from`` field into the field when the field is
    empty and ``from`` is not (e.g. Lewisham's status_from_decision). ``to_text``
    rules are documentation: ``number_as_text`` already does it.

    Args:
        row: The row being built; text values already tidied.
        rules: The parsed cleaning rules.
        applied: Counter of changes per rule id, updated here.
    """
    for name, field_rules in rules["fields"].items():
        for rule in field_rules:
            value = row.get(name)
            match rule["action"]:
                case "map" if value == rule["from"]:
                    row[name] = rule["to"]
                case "replace" if isinstance(value, str) and rule["from"] in value:
                    row[name] = value.replace(rule["from"], rule["to"])
                case "fill_null" if value is None:
                    row[name] = rule["to"]
                case "copy_when_null" if value is None and row.get(rule["from"]):
                    row[name] = row[rule["from"]]
                case _:
                    continue
            applied[rule["id"]] += 1


def _apply_outcomes(
    row: dict[str, Any], rules: Mapping[str, Any], applied: Counter[str]
) -> None:
    """Fill the outcome from what a person confirmed on the portal.

    Rule ``set_outcome`` applies only while the Datahub still shows the old state
    (every ``only_when_null`` field empty, or status one of ``only_when_status``),
    so the Datahub's own update always wins once it has one. An outcome is either
    one value for every field in ``sets``, or a mapping of field → value (dates as
    ``dd/mm/yyyy``). ``outcome_source`` records where the outcome came from.

    Args:
        row: The row being built; text values tidied, dates parsed.
        rules: The parsed cleaning rules.
        applied: Counter of changes per rule id, updated here.
    """
    row["outcome_source"] = "datahub"
    for rule in rules["all_records"]:
        if rule["action"] != "set_outcome" or row["id"] not in rule["outcomes"]:
            continue
        still_old = all(
            row.get(n) is None for n in rule.get("only_when_null", [])
        ) and (
            "only_when_status" not in rule or row["status"] in rule["only_when_status"]
        )
        if not still_old:
            continue
        outcome = rule["outcomes"][row["id"]]
        values = (
            outcome
            if isinstance(outcome, dict)
            else dict.fromkeys(rule["sets"], outcome)
        )
        for name, value in values.items():
            row[name] = parse_date(value) if name in DATE_FIELDS else value
        row["outcome_source"] = rule["source"]
        applied[rule["id"]] += 1


def _apply_set_when(
    row: dict[str, Any], rules: Mapping[str, Any], applied: Counter[str]
) -> None:
    """Apply ``set_when`` rules: when every ``when`` value matches, set ``set`` values.

    Each rule also writes its ``flag`` column (``True`` when it applied, else
    ``False``), so the change stays visible after the values are replaced.

    Args:
        row: The row being built (dates already parsed).
        rules: The parsed cleaning rules.
        applied: Counter of changes per rule id, updated here.
    """
    for rule in rules["all_records"]:
        if rule["action"] != "set_when":
            continue
        matches = all(row.get(k) == v for k, v in rule["when"].items())
        row[rule["flag"]] = matches
        if matches:
            row.update(rule["set"])
            applied[rule["id"]] += 1


def _decision_not_issued(row: Mapping[str, Any], rules: Mapping[str, Any]) -> bool:
    """Tell whether a decision is recorded for an application that isn't decided yet.

    Derived rule ``decision_not_issued``: the portal showed such records as
    "Awaiting decision" (a decision recorded early, before a legal agreement).

    Args:
        row: The row being built, with status, decision and parsed decision_date.
        rules: The parsed cleaning rules.

    Returns:
        ``True`` when the status is still pending, a decision is set and there is
        no decision date.
    """
    rule = next(
        (r for r in rules.get("derived", []) if r["id"] == "decision_not_issued"), None
    )
    if rule is None:  # only where the data needs it (Brent)
        return False
    return (
        row["status"] in rule["when_status"]
        and row["decision"] is not None
        and row["decision_date"] is None
    )


def _legacy_record(bo_system: str | None, rules: Mapping[str, Any]) -> bool:
    """Apply the derived rule ``legacy_record`` (its ``when_equals``) to one record.

    Args:
        bo_system: The record's tidied ``bo_system``.
        rules: The parsed cleaning rules.

    Returns:
        ``True`` when ``bo_system`` equals the rule's value; ``False`` if it doesn't,
        or the borough has no such rule.
    """
    rule = next(
        (r for r in rules.get("derived", []) if r["id"] == "legacy_record"), None
    )
    if rule is None:
        return False
    return bo_system == rule["when_equals"]["bo_system"]


def _geojson(value: Any) -> str | None:  # noqa: ANN401
    """Serialise a GeoJSON geometry as text, skipping empty ones.

    Args:
        value: The ``wgs84_polygon`` object from the record.

    Returns:
        GeoJSON text, or ``None`` when there is no geometry.
    """
    if not value or not (value.get("coordinates") or value.get("geometries")):
        return None
    return json.dumps(value)


def clean_record(
    raw: Mapping[str, Any],
    rules: Mapping[str, Any],
    borough: Borough,
    result: Result,
    postcode_centroids: Mapping[str, Mapping[str, float]],
) -> None:
    """Clean one raw record and add the row to ``result`` (or count it as dropped).

    Args:
        raw: One application document from a raw snapshot.
        rules: The parsed cleaning rules.
        borough: The borough being loaded.
        result: Collects rows and counts.
        postcode_centroids: In-borough postcode centroids for placeholder records.

    Raises:
        ValueError: If the record is out of scope (it must never get this far).
    """
    if not in_scope(raw, borough):
        raise ValueError(f"out-of-scope record {raw.get('id')!r}")
    record = {k: tidy_text(v) for k, v in raw.items()}
    if reason := _drop_reason(record, rules):
        result.dropped[reason] += 1
        return

    row: dict[str, Any] = {"id": record["id"]}
    # borough is missing only when lpa_name gave the scope; store the scope borough.
    row["borough"] = record.get("borough") or borough_name(borough)
    row |= {name: record.get(name) for name in TEXT_FIELDS}
    # The address parts go through the field rules too (e.g. locality "NULL" -> null)
    # before the address is built from them.
    row |= {name: record.get(name) for name in ADDRESS_PARTS}
    row["site_number"] = number_as_text(record.get("site_number"))
    for name in DATE_FIELDS:
        row[name] = parse_date(record.get(name))
    # Before the field rules, so a confirmed outcome wins over "empty -> Unknown".
    _apply_outcomes(row, rules, result.applied)
    _apply_field_rules(row, rules, result.applied)
    _apply_set_when(row, rules, result.applied)  # after dates: it can clear one
    row["decision_not_issued"] = _decision_not_issued(row, rules)
    if row["decision_not_issued"]:
        result.applied["decision_not_issued"] += 1
    row["approximate_decision_date"] = row["id"] in _ids(
        rules, "early_1987_approximate"
    )
    if row["approximate_decision_date"]:
        result.applied["early_1987_approximate"] += 1

    parts = {name: row.pop(name) for name in ADDRESS_PARTS}
    row["address"], row["address_kind"] = build_address(parts)
    row["legacy_record"] = _legacy_record(row.pop("bo_system"), rules)

    centroid = raw.get("centroid") or {}
    lat, lon = centroid.get("lat"), centroid.get("lon")
    row["placeholder_centroid"] = postcodes.is_placeholder(lat, lon)
    row["centroid_source"] = "datahub"
    if row["placeholder_centroid"]:
        found = postcode_centroids.get(postcodes.record_postcode(raw) or "")
        lat, lon = (found["lat"], found["lon"]) if found else (None, None)
        row["centroid_source"] = "postcodes.io" if found else None
        result.applied["placeholder_centroid"] += 1
    row["lat"], row["lon"] = lat, lon
    row["site_geojson"] = _geojson(raw.get("wgs84_polygon"))
    row["last_updated"] = record.get("last_updated")
    result.rows.append(row)


def clean_all(
    records: Iterable[Mapping[str, Any]],
    rules: Mapping[str, Any],
    borough: Borough,
    postcode_centroids: Mapping[str, Mapping[str, float]],
) -> Result:
    """Clean every record and check the counts the rules expect.

    Args:
        records: Raw application documents.
        rules: The parsed cleaning rules.
        borough: The borough being loaded.
        postcode_centroids: In-borough postcode centroids for placeholder records.

    Returns:
        The rows and what was dropped or changed.

    Raises:
        ValueError: If a rule's ``expected_count`` doesn't match, so a changed
            download is looked at before it is loaded.
    """
    result = Result()
    for raw in records:
        clean_record(raw, rules, borough, result, postcode_centroids)
    every = [
        *rules["all_records"],
        *(r for rs in rules["fields"].values() for r in rs),
        *rules["derived"],
    ]
    for rule in every:
        expected = rule.get("expected_count")
        got = result.dropped[rule["id"]] or result.applied[rule["id"]]
        if expected is not None and got != expected:
            raise ValueError(f"rule {rule['id']}: expected {expected}, got {got}")
    return result
