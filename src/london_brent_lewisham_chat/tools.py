"""The chat tools: fixed query blueprints the model can call, never free queries.

Each tool takes typed arguments (choices generated from the semantic YAMLs), builds
the Datahub query in code, and returns a compact result plus the citations for it.
Query *parameters* (dropped ids, statuses, date fields) come from the YAMLs; the
small amount of *logic* that turns a rule into a query lives here, and is checked
against the cleaned database's counts.

Two tools so far: ``count_applications`` and ``get_application_details``.
"""

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

import logfire
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    create_model,
    field_validator,
)

from .datahub import DatahubClient
from .pipeline import clean
from .scope import BOROUGH_NAMES
from .semantic import SemanticLayer, Source

logger = logging.getLogger(__name__)

FIRST_YEAR = 1987
"""The earliest year with applications (validated from 1987-04-23 on,
Brent's EDA, ``findings/brent/valid_date.ipynb``)."""

DATE_BASIS_KEY = {"validated": "submissions", "decided": "decisions"}
"""``date_basis`` argument -> key of ``date_field`` in the ``year`` default filter."""

COUNT_DESCRIPTION = (
    "Count planning applications in one local authority for one year, optionally "
    "with one council decision. Choose date_basis by what the year refers to "
    "(the 'year' default filter: decision_date for decisions, valid_date for "
    "submissions): "
    "'decided' when the year is when a decision was made, e.g. 'approved in 2023', "
    "'refused in 2024', 'decided in 2025'; "
    "'validated' when it is when applications came in, e.g. 'validated in 2024', "
    "'submitted in 2024', 'applications in 2024'. "
    "For a follow-up about earlier results ('how many of those were refused?'), "
    "keep the date_basis and year of the count it refers to. To compare boroughs, "
    "count each one with its own call. "
    "Test, empty and duplicate records are excluded, as in the cleaned data."
)

DETAILS_DESCRIPTION = (
    "Look up one planning application by its reference, as people quote it (e.g. "
    "'18/1022', or '21/0044/DELBRE' for one decided by OPDC). Returns its cleaned "
    "details (status, council decision, dates, address, description, appeal, link "
    "to the council's planning portal) and notes on known data issues for this "
    "record. found=false means no such application in the covered area."
)

DESCRIPTION_MAX_CHARS = 1000
"""Longest description passed to the model; a few are very long, and the model
needs the gist, not every word (keeps tokens bounded)."""


@dataclass(frozen=True)
class Citation:
    """One source behind an answer, built in code from a tool call."""

    id: str
    """Short id the answer refers to, e.g. ``c1``."""
    text: str
    """The citation, from the ``cite_as`` template in ``live_api.yaml``."""
    query: dict[str, Any]
    """The exact Datahub query, so the answer can be checked."""
    retrieved_at: datetime


@dataclass(frozen=True)
class ToolOutput:
    """What a tool hands back: a compact result for the model, and its citations."""

    result: dict[str, Any]
    citations: list[Citation]
    datahub_cache_hits: int = 0
    """Datahub answers this call took from the cache (for the ``chat finished`` log
    line); not shown to the model."""


class _Args(BaseModel):
    """Base for tool arguments: unknown arguments are an error the model sees."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def count_args_model(semantic: SemanticLayer | Sequence[SemanticLayer]) -> type[_Args]:
    """Build the argument model of ``count_applications`` from the semantic layers.

    The authorities are the layers' boroughs and the allowed decisions the union of
    their ``decision.yaml`` values (in file order), so nothing is typed by hand. A
    decision one borough doesn't use is rejected per call (``count_in``).

    Args:
        semantic: One borough's semantic layer, or several.

    Returns:
        A Pydantic model whose JSON schema is what the model sees.
    """
    layers = [semantic] if isinstance(semantic, SemanticLayer) else list(semantic)
    authorities = tuple(BOROUGH_NAMES[layer.borough] for layer in layers)
    decisions = tuple(
        dict.fromkeys(v for layer in layers for v in layer.allowed_values("decision"))
    )

    def check_year(cls: type, value: int) -> int:
        if not FIRST_YEAR <= value <= date.today().year:
            raise ValueError(f"year must be between {FIRST_YEAR} and this year")
        return value

    return create_model(
        "CountApplicationsArgs",
        __base__=_Args,
        __validators__={"check_year": field_validator("year")(classmethod(check_year))},
        local_authority=(
            Literal[*authorities],
            Field(description="The local authority. Only these are covered."),
        ),
        year=(int, Field(description="Calendar year, e.g. 2024.")),
        decision=(
            Literal[*decisions] | None,
            Field(default=None, description="Council decision to count, if any."),
        ),
        date_basis=(
            Literal["validated", "decided"],
            Field(
                default="validated",
                description=(
                    "Which date the year applies to: 'decided' (decision_date) for "
                    "when a decision was made ('approved/refused/decided in'); "
                    "'validated' (valid_date) for when applications came in. A "
                    "follow-up keeps the basis of the count it refers to."
                ),
            ),
        ),
    )


def count_filters(args: _Args, semantic: SemanticLayer) -> list[dict[str, Any]]:
    """Build the Datahub filter clauses for ``count_applications``.

    The scope filter is added by the Datahub client; these come after it.

    Args:
        args: Validated ``count_applications`` arguments.
        semantic: The borough's semantic layer.

    Returns:
        The exclusion of dropped records, the year range on the chosen date field,
        and (with a decision) the decision with the ``decided`` default filter.
    """
    year_filter = _default_filter(semantic, "year")
    field = year_filter.date_field[DATE_BASIS_KEY[args.date_basis]]
    filters = [
        _excluded_records(semantic),
        {
            "range": {
                field: {"gte": f"01/01/{args.year}", "lt": f"01/01/{args.year + 1}"}
            }
        },
    ]
    if args.decision is not None:
        decided = _default_filter(semantic, "decided")
        filters.append(
            {
                "bool": {
                    "filter": [_is(decided.filter_on or "decision", args.decision)],
                    "must_not": [
                        _flag_condition(flag, semantic)
                        for flag in decided.exclude_flags
                    ],
                }
            }
        )
    return filters


async def count_applications(
    args: _Args,
    *,
    semantic: SemanticLayer,
    datahub: DatahubClient,
    citation_id: str,
) -> ToolOutput:
    """Count applications on the live Datahub, as the model asked.

    Args:
        args: Validated ``count_applications`` arguments.
        semantic: The borough's semantic layer.
        datahub: The live Datahub client (scoped and cached).
        citation_id: The id this call's citation gets, e.g. ``c1``.

    Returns:
        ``{"count", "counted", "notes", "citation"}`` for the model, and the citation.

    Raises:
        DatahubError: If the Datahub can't answer (the endpoint answers 503/504).
    """
    with logfire.span("tool count_applications", args=args.model_dump()) as span:
        found = await datahub.count(*count_filters(args, semantic))
        counted = _describe(args, semantic)
        span.set_attribute("count", found.count)
    citation = Citation(
        id=citation_id,
        text=_cite(semantic, counted, found.retrieved_at),
        query=found.query,
        retrieved_at=found.retrieved_at,
    )
    result = {
        "count": found.count,
        "counted": counted,
        "notes": _notes(args, semantic),
        "citation": citation_id,
    }
    return ToolOutput(
        result=result, citations=[citation], datahub_cache_hits=int(found.cached)
    )


class DetailsArgs(_Args):
    """Arguments of ``get_application_details``."""

    reference: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=40,
            pattern=r"^[A-Za-z0-9/._ -]+$",
        ),
    ] = Field(description="The application reference, e.g. 18/1022 or DC/24/130001.")
    local_authority: Literal[*BOROUGH_NAMES.values()] | None = Field(
        default=None,
        description="The local authority, if known; otherwise every covered one is searched.",
    )


async def get_application_details(
    args: DetailsArgs,
    *,
    semantic: SemanticLayer,
    datahub: DatahubClient,
    citation_id: str,
    today: date | None = None,
) -> ToolOutput:
    """Look up one application on the live Datahub and return it cleaned.

    The reference is searched as a phrase (``lpa_app_no`` has no exact-match
    field) with the same exclusions as counts, then matched exactly in code, since
    ``21/0044`` would also match ``21/0044/DELBRE``. The record goes through
    ``clean_record`` and the live ``status_stale`` rule, and only the fields in
    ``live_api.yaml``'s ``returned`` list reach the model. Records outside the
    borough never get here: the Datahub client drops them (and logs them).

    Args:
        args: Validated arguments.
        semantic: The borough's semantic layer.
        datahub: The live Datahub client (scoped and cached).
        citation_id: The id this call's citation gets, e.g. ``c4``.
        today: The date ``status_stale`` is measured from; tests pass a fixed one.

    Returns:
        ``{"found": false, "reference"}`` with no citation, or ``{"found": true,
        "application", "notes", "citation"}`` with the record's citation.

    Raises:
        DatahubError: If the Datahub can't answer (the endpoint answers 503/504).
    """
    with logfire.span("tool get_application_details", reference=args.reference) as span:
        forms = _reference_forms(args.reference, semantic)
        found = await datahub.search(
            {
                "bool": {
                    "should": [{"match_phrase": {"lpa_app_no": f}} for f in forms],
                    "minimum_should_match": 1,
                }
            },
            _excluded_records(semantic),
            size=5,
            fields=semantic.live.request_fields,
        )
        wanted = {_normalise_reference(f) for f in forms}
        matches = [
            record
            for record in found.records
            if _normalise_reference(record.get("lpa_app_no") or "") in wanted
        ]
        if len(matches) > 1:
            logger.warning(
                "several records for one reference",
                extra={"ids": [record.get("id") for record in matches]},
            )
        row = _cleaned(matches[0], semantic, today or date.today()) if matches else None
        span.set_attribute("found", row is not None)

    hits = int(found.cached)
    if row is None:
        result: dict[str, Any] = {"found": False, "reference": args.reference}
        if reason := _excluded_reason(wanted, semantic):
            result |= {"excluded": True, "reason": reason}
        return ToolOutput(result=result, citations=[], datahub_cache_hits=hits)

    application = {
        ref.field: _plain(row.get(ref.field)) for ref in semantic.live.returned
    }
    if isinstance(application.get("description"), str):
        application["description"] = application["description"][:DESCRIPTION_MAX_CHARS]
    text = _cite_record(semantic, application, found.retrieved_at)
    if portal := _portal_check_for(application, semantic):
        text += "; " + _cite_portal_check(portal, application, matches[0])
    citation = Citation(
        id=citation_id,
        text=text,
        query=found.query,
        retrieved_at=found.retrieved_at,
    )
    result = {
        "found": True,
        "application": application,
        "notes": _record_notes(application, semantic),
        "citation": citation_id,
    }
    return ToolOutput(result=result, citations=[citation], datahub_cache_hits=hits)


@dataclass(frozen=True)
class Area:
    """One covered borough: its semantic layer and its live Datahub client."""

    semantic: SemanticLayer
    datahub: DatahubClient


async def count_in(
    args: _Args, *, areas: Mapping[str, Area], citation_id: str
) -> ToolOutput:
    """Count in the borough the model named, with that borough's rules.

    Args:
        args: Validated ``count_applications`` arguments.
        areas: The covered boroughs, by borough key ("brent", "lewisham").
        citation_id: The id this call's citation gets.

    Returns:
        The count, or an error the model can correct (a decision value the borough
        doesn't use).

    Raises:
        DatahubError: If the Datahub can't answer (the endpoint answers 503/504).
    """
    area = areas[args.local_authority.lower()]
    if args.decision is not None and args.decision not in area.semantic.allowed_values(
        "decision"
    ):
        error = f"{args.decision!r} is not a decision {args.local_authority} uses"
        return ToolOutput(result={"error": error}, citations=[])
    return await count_applications(
        args, semantic=area.semantic, datahub=area.datahub, citation_id=citation_id
    )


async def details_in(
    args: DetailsArgs, *, areas: Mapping[str, Area], citation_id: str
) -> ToolOutput:
    """Look a reference up in the named borough, or in every covered one at once.

    Args:
        args: Validated ``get_application_details`` arguments.
        areas: The covered boroughs, by borough key.
        citation_id: The id the found record's citation gets.

    Returns:
        The record found; or, if none, an excluded-record explanation when there is
        one, else "not found".

    Raises:
        DatahubError: If the Datahub can't answer (the endpoint answers 503/504).
    """
    keys = [args.local_authority.lower()] if args.local_authority else list(areas)
    outputs = await asyncio.gather(
        *(
            get_application_details(
                args,
                semantic=areas[k].semantic,
                datahub=areas[k].datahub,
                citation_id=citation_id,
            )
            for k in keys
        )
    )
    hits = sum(o.datahub_cache_hits for o in outputs)
    found = [o for o in outputs if o.result.get("found")]
    if found:
        first = found[0]
        if len(found) > 1:
            first.result["notes"].append(
                "Another covered borough has an application with the same reference; "
                "ask which borough is meant."
            )
        return ToolOutput(first.result, first.citations, hits)
    excluded = next((o for o in outputs if o.result.get("excluded")), outputs[0])
    return ToolOutput(excluded.result, [], hits)


def _cleaned(
    record: dict[str, Any], semantic: SemanticLayer, today: date
) -> dict[str, Any] | None:
    """Clean one live record as the pipeline does, plus the live ``status_stale``.

    Args:
        record: One raw, in-scope record.
        semantic: The borough's semantic layer.
        today: The date ``status_stale`` is measured from.

    Returns:
        The cleaned row, or ``None`` if the cleaning rules drop the record (a test
        or empty record): then it is treated as not found.
    """
    result = clean.Result()
    clean.clean_record(record, semantic.cleaning_rules, semantic.borough, result, {})
    if not result.rows:
        return None
    row = result.rows[0]
    row["status_stale"] = _status_stale(row, semantic, today)
    return row


def _status_stale(row: dict[str, Any], semantic: SemanticLayer, today: date) -> bool:
    """Apply ``cleaning.yaml``'s ``status_stale`` rule, measured from today.

    The pipeline computes it at load, from the snapshot date; live, the same rule
    runs from today (``status_stale_today`` in ``live_api.yaml``).

    Args:
        row: A cleaned row (dates already parsed).
        semantic: The borough's semantic layer.
        today: The date to measure from.

    Returns:
        ``True`` when the status is a pending one, there is no decision date and the
        target date is more than ``stale_after_days`` ago; or the id is listed.
        ``False`` for a borough without the rule.
    """
    rule = next(
        (
            r
            for r in semantic.cleaning_rules.get("derived", [])
            if r["id"] == "status_stale"
        ),
        None,
    )
    if rule is None:  # no stale rule for this borough yet (Lewisham)
        return False
    if row.get("id") in rule.get("ids", []):
        return True
    target = row.get("decision_target_date")
    return (
        row.get("status") in rule["when_status"]
        and row.get("decision_date") is None
        and target is not None
        and target < today - timedelta(days=rule["stale_after_days"])
    )


def _record_notes(application: dict[str, Any], semantic: SemanticLayer) -> list[str]:
    """Pick the rules of each derived column that applies to this record.

    Known issues travel with the record they concern, so the model reads a caveat
    exactly when it applies. A column applies when it is ``True`` on the record
    (e.g. status_stale, legacy_record), or for ``outcome_source`` when the outcome did
    not come from the Datahub. Its rules come from its own field file.

    Args:
        application: The record's returned fields.
        semantic: The borough's semantic layer.

    Returns:
        The rules, each on one line, in the order of live_api.yaml's ``returned``.
    """
    notes = []
    for ref in semantic.live.returned:
        value = application.get(ref.field)
        applies = value is True or (
            ref.field == "outcome_source" and value not in (None, "datahub")
        )
        if applies and (doc := semantic.field_doc(ref.field)):
            notes += [" ".join(rule.split()) for rule in doc.rules]
    return notes


def _cite_record(
    semantic: SemanticLayer, application: dict[str, Any], retrieved_at: datetime
) -> str:
    """Fill the live-record citation template, plus the portal link when there is one.

    Args:
        semantic: The borough's semantic layer.
        application: The record's returned fields.
        retrieved_at: When the Datahub answered.

    Returns:
        The citation text.
    """
    text = (
        semantic.live.citation.live_record.cite_as.replace(
            "<lpa_app_no>", str(application.get("lpa_app_no"))
        )
        .replace("<id>", str(application.get("id")))
        .replace("<retrieved_at>", retrieved_at.isoformat(timespec="seconds"))
    )
    if url := application.get("url_planning_app"):
        text += f" Documents and decision notice: {url}"
    return text


def _portal_check_for(
    application: dict[str, Any], semantic: SemanticLayer
) -> Source | None:
    """Return the portal-check source if a value of this record came from it.

    The record is in one of the groups in ``sources.yaml`` (listed by id), or it is
    a deleted appeal, which cleaning finds by value, so a new one is covered too.

    Args:
        application: The record's returned fields.
        semantic: The borough's semantic layer.

    Returns:
        The portal-check source to cite, or ``None``.
    """
    portal = semantic.portal_check()
    if portal is None:
        return None
    in_group = any(
        application.get("id") in group.ids for group in portal.groups.values()
    )
    return (
        portal if in_group or application.get("appeal_record_deleted") is True else None
    )


def _cite_portal_check(
    portal: Source, application: dict[str, Any], raw: dict[str, Any]
) -> str:
    """Fill the portal-check source's ``cite_as`` template for one record.

    ``<datahub_had>`` gets what the live Datahub actually sent for this record
    (before cleaning), so the citation states precisely where the two differ.

    Args:
        portal: The portal-check source from ``sources.yaml``.
        application: The record's returned fields.
        raw: The record as the Datahub sent it.

    Returns:
        The citation text.
    """
    had = ", ".join(
        f"{label} {value}"
        if (value := (raw.get(field) or "").strip())
        else f"no {label}"
        for field, label in (
            ("status", "status"),
            ("decision", "decision"),
            ("appeal_decision", "appeal decision"),
        )
    )
    url = application.get("url_planning_app") or "no portal link"
    text = " ".join(portal.cite_as.split()).strip("'")
    return text.replace("<url_planning_app>", url).replace("<datahub_had>", had)


def _reference_forms(reference: str, semantic: SemanticLayer) -> list[str]:
    """Return the reference as given, and its other form where the borough has one.

    With ``reference_prefix_optional`` (Lewisham's "DC/"), "DC/21/120061" also tries
    "21/120061", and the other way round (handover: lpa_app_no.yaml).

    Args:
        reference: The reference as asked.
        semantic: The borough's semantic layer.

    Returns:
        One or two references to search.
    """
    prefix = semantic.live.reference_prefix_optional
    if not prefix:
        return [reference]
    if _normalise_reference(reference).startswith(_normalise_reference(prefix)):
        return [reference, reference.strip()[len(prefix) :].lstrip()]
    return [reference, prefix + reference.strip()]


def _excluded_reason(wanted: set[str], semantic: SemanticLayer) -> str | None:
    """Explain a lookup of a record the cleaning drops on purpose, from sources.yaml.

    A portal-check group whose records are dropped by a rule ``drop_<group>`` (e.g.
    Lewisham's portal_removed) gets its sentence from ``sources.yaml``'s rules (the
    one starting with "<group>:") instead of a plain "not found".

    Args:
        wanted: The normalised references looked up.
        semantic: The borough's semantic layer.

    Returns:
        The sentence to give, or ``None``.
    """
    portal = semantic.portal_check()
    if portal is None:
        return None
    drop_ids = {
        r["id"]: set(r.get("ids", []))
        for r in semantic.cleaning_rules["all_records"]
        if r["action"] == "drop_records"
    }
    for name, group in portal.groups.items():
        dropped = drop_ids.get(f"drop_{name}", set())
        for record_id in set(group.ids) & dropped:
            reference = record_id.split("-", 1)[1].replace("_", "/")
            if _normalise_reference(reference) in wanted:
                rule = next(
                    (r for r in semantic.sources.rules if r.startswith(f"{name}:")),
                    None,
                )
                return " ".join(rule.split(":", 1)[1].split()) if rule else None
    return None


def _normalise_reference(reference: str) -> str:
    """Compare references however they are spaced or cased ("18 / 1022" = "18/1022").

    Args:
        reference: A reference as typed or as published.

    Returns:
        Upper case, without spaces around ``/`` or at the ends.
    """
    return re.sub(r"\s*/\s*", "/", reference.strip()).upper()


def _plain(value: Any) -> Any:  # noqa: ANN401 (field values of any type)
    """Make a cleaned value JSON-ready: dates as ``YYYY-MM-DD``.

    Args:
        value: A cleaned field value.

    Returns:
        The value, with dates turned into ISO text.
    """
    return value.isoformat() if isinstance(value, date) else value


def _describe(args: _Args, semantic: SemanticLayer) -> str:
    """Say in words exactly what was counted (shown to the model and cited).

    Args:
        args: Validated ``count_applications`` arguments.
        semantic: The borough's semantic layer.

    Returns:
        E.g. "Brent applications validated in 2024 (valid_date) with council
        decision Refused, excluding decisions recorded but not yet issued".
    """
    field = _default_filter(semantic, "year").date_field[
        DATE_BASIS_KEY[args.date_basis]
    ]
    text = f"{args.local_authority} applications {args.date_basis} in {args.year} ({field})"
    if args.decision is not None:
        text += f" with council decision {args.decision}"
        if "decision_not_issued" in _default_filter(semantic, "decided").exclude_flags:
            text += ", excluding decisions recorded but not yet issued"
    return text


def _notes(args: _Args, semantic: SemanticLayer) -> list[str]:
    """Pick the YAML rules that apply to this count, for the model to pass on.

    Args:
        args: Validated ``count_applications`` arguments.
        semantic: The borough's semantic layer.

    Returns:
        The ``year`` default-filter rule, and with a decision the live rule on
        published values; each on one line, as written in ``live_api.yaml``.
    """
    notes = [_default_filter(semantic, "year").rule]
    if args.decision is not None:
        notes += [
            rule.rule
            for rule in semantic.live.live_rules
            if rule.id == "counts_use_published_values"
        ]
    return [" ".join(note.split()) for note in notes if note]


def _cite(semantic: SemanticLayer, what: str, retrieved_at: datetime) -> str:
    """Fill the live-query citation template from ``live_api.yaml``.

    Args:
        semantic: The borough's semantic layer.
        what: What was counted.
        retrieved_at: When the Datahub answered.

    Returns:
        The citation text.
    """
    return semantic.live.citation.live_query.cite_as.replace(
        "<what was counted or searched>", what
    ).replace("<retrieved_at>", retrieved_at.isoformat(timespec="seconds"))


def _excluded_records(semantic: SemanticLayer) -> dict[str, Any]:
    """Exclude the records the pipeline drops, so counts match the cleaned data.

    From ``cleaning.yaml``: the ids listed in the drop rules, the empty-record
    condition (all its fields missing: the Datahub sends empty fields as missing,
    never ``""``, checked 2026-11-11), and test references (the borough's
    ``reference_prefix``), matched on
    ``id`` because ``id`` is a keyword field and ``lpa_app_no`` is not.

    Args:
        semantic: The borough's semantic layer.

    Returns:
        One ``bool``/``must_not`` clause.
    """
    rules = [
        rule
        for rule in semantic.cleaning_rules["all_records"]
        if rule["action"] == "drop_records"
    ]
    ids = sorted({record_id for rule in rules for record_id in rule.get("ids", [])})
    must_not: list[dict[str, Any]] = [{"ids": {"values": ids}}]
    if prefix := clean.test_reference_prefix(semantic.cleaning_rules):
        # The id is "<Borough>-" + the reference with "/" as "_".
        id_prefix = f"{BOROUGH_NAMES[semantic.borough]}-{prefix.replace('/', '_')}"
        must_not.append({"prefix": {"id": id_prefix}})
    for rule in rules:
        if fields := rule.get("when_all_null"):
            must_not.append(
                {"bool": {"must_not": [{"exists": {"field": f}} for f in fields]}}
            )
    return {"bool": {"must_not": must_not}}


def _flag_condition(flag: str, semantic: SemanticLayer) -> dict[str, Any]:
    """Turn a flag from ``flag_conditions`` into a Datahub query condition.

    The parameters come from the flag's rule in ``cleaning.yaml`` (``derived``);
    the logic mirrors ``flag_conditions`` in ``live_api.yaml`` and
    ``pipeline/clean.py``. Only the flags the tools use so far are here; any other
    is an error, so a new default filter can't silently skip its exclusion.

    Args:
        flag: The flag's rule id, e.g. ``decision_not_issued``.
        semantic: The borough's semantic layer.

    Returns:
        A clause matching the records that have the flag.

    Raises:
        NotImplementedError: For a flag no tool handles yet.
    """
    rule = next(r for r in semantic.cleaning_rules["derived"] if r["id"] == flag)
    if flag == "decision_not_issued":
        return {
            "bool": {
                "filter": [
                    _any_of("status", rule["when_status"]),
                    {"exists": {"field": "decision"}},
                ],
                "must_not": [{"exists": {"field": "decision_date"}}],
            }
        }
    raise NotImplementedError(f"no live query for flag {flag!r} yet")


def _is(field: str, value: str) -> dict[str, Any]:
    """Match a published value however it is spaced or cased.

    A phrase match on the analysed text field, not the exact ``.raw``: the Datahub
    has variants such as a tab before ``Approved`` and ``lapsed``/``Lapsed``.
    Checked on 2026-11-11 for every decision (7) and status (19): same counts as the
    raw values after stripping and case-folding, and no match inside longer values.

    Args:
        field: The field, e.g. ``decision``.
        value: The clean value, e.g. ``Approved``.

    Returns:
        A ``match_phrase`` clause.
    """
    return {"match_phrase": {field: value}}


def _any_of(field: str, values: list[str]) -> dict[str, Any]:
    """Match any of several values (see ``_is``).

    Args:
        field: The field, e.g. ``status``.
        values: The clean values.

    Returns:
        A ``bool``/``should`` clause matching at least one value.
    """
    return {
        "bool": {"should": [_is(field, v) for v in values], "minimum_should_match": 1}
    }


def _default_filter(semantic: SemanticLayer, filter_id: str) -> Any:  # noqa: ANN401
    """Return one default filter from ``live_api.yaml`` by id.

    Args:
        semantic: The borough's semantic layer.
        filter_id: E.g. ``year`` or ``decided``.

    Returns:
        The default filter.
    """
    return next(f for f in semantic.live.default_filters if f.id == filter_id)
