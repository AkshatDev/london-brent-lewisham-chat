"""The chat tools: fixed query blueprints the model can call, never free queries.

Each tool takes typed arguments (choices generated from the semantic YAMLs), builds
the Datahub query in code, and returns a compact result plus the citations for it.
Query *parameters* (dropped ids, statuses, date fields) come from the YAMLs; the
small amount of *logic* that turns a rule into a query lives here, and is checked
against the cleaned database's counts.

Three tools: ``count_applications``, ``search_applications`` and
``get_application_details``.
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

DATE_BASIS_KEY = {
    "validated": "submissions",
    "decided": "decisions",
    "appeal_decided": "appeals",
}
"""``date_basis`` argument -> key of ``date_field`` in the ``year`` default filter."""

COUNT_DESCRIPTION = (
    "Count planning applications in one local authority for one year (or one month "
    "of it), optionally narrowed by council decision, application type and appeal "
    "outcome. Choose date_basis by what the year refers to "
    "(the 'year' default filter: decision_date for decisions, valid_date for "
    "submissions, appeal_decision_date for appeals): "
    "'decided' when the year is when a decision was made, e.g. 'approved in 2023', "
    "'refused in 2024', 'decided in 2025'; "
    "'validated' when it is when applications came in, e.g. 'validated in 2024', "
    "'submitted in 2024', 'applications in 2024'; "
    "'appeal_decided' when it is when an appeal was decided, e.g. 'appeals allowed "
    "in 2025'. "
    "pending=true counts applications still awaiting a decision now; no year is "
    "needed for that. "
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

SEARCH_DESCRIPTION = (
    "List up to 10 planning applications in one local authority, newest first, with "
    "the same filters as count_applications (year optional here), plus words in "
    "the description (e.g. 'basement', 'loft conversion') and a street, place or "
    "postcode in the address (e.g. 'Church Road', 'SE13 6LG'). The newest are by "
    "the date_basis date: 'decided' for 'recent refusals/decisions', 'validated' "
    "for 'recent applications'. Returns the total number of matches and a short "
    "summary of each; use get_application_details for one application's full "
    "details."
)

SEARCH_FIELDS = (
    "lpa_app_no",
    "application_type",
    "description",
    "address",
    "status",
    "decision",
    "valid_date",
    "decision_date",
    "appeal_decision",
    "url_planning_app",
)
"""Fields of each listed application: enough to tell them apart and to follow up
(a subset of ``returned`` in ``live_api.yaml``; the details tool gives the rest)."""

SEARCH_DESCRIPTION_CHARS = 200
"""Description length per listed application: the gist, so ten stay small."""

ADDRESS_FIELDS = ("site_name", "site_number", "street_name", "locality", "postcode")
"""The published address parts (``address.yaml``); an address search matches any."""

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

    The authorities are the layers' boroughs; the allowed decisions, application
    types and appeal outcomes the union of their YAML values (in file order), so
    nothing is typed by hand. A value one borough doesn't use, or a combination
    that makes no sense, is rejected per call (``count_in``).

    Args:
        semantic: One borough's semantic layer, or several.

    Returns:
        A Pydantic model whose JSON schema is what the model sees.
    """
    layers = [semantic] if isinstance(semantic, SemanticLayer) else list(semantic)
    authorities = tuple(BOROUGH_NAMES[layer.borough] for layer in layers)

    def values(field: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(v for layer in layers for v in layer.allowed_values(field))
        )

    def check_year(cls: type, value: int | None) -> int | None:
        if value is not None and not FIRST_YEAR <= value <= date.today().year:
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
        year=(
            int | None,
            Field(
                default=None,
                description="Calendar year, e.g. 2024. Required unless pending=true.",
            ),
        ),
        month=(
            int | None,
            Field(
                default=None,
                ge=1,
                le=12,
                description="Month of that year (1-12), e.g. 3 for 'in March 2026'.",
            ),
        ),
        decision=(
            Literal[*values("decision")] | None,
            Field(default=None, description="Council decision to count, if any."),
        ),
        application_type=(
            Literal[*values("application_type")] | None,
            Field(default=None, description="Application type to count, if any."),
        ),
        appeal_decision=(
            Literal[*values("appeal_decision")] | None,
            Field(
                default=None,
                description="Appeal outcome to count, if any (use with "
                "date_basis='appeal_decided' for 'appeals allowed in <year>').",
            ),
        ),
        pending=(
            bool,
            Field(
                default=False,
                description="Only applications still awaiting a decision now.",
            ),
        ),
        date_basis=(
            Literal[*DATE_BASIS_KEY],
            Field(
                default="validated",
                description=(
                    "Which date the year applies to: 'decided' (decision_date) for "
                    "when a decision was made ('approved/refused/decided in'); "
                    "'validated' (valid_date) for when applications came in; "
                    "'appeal_decided' (appeal_decision_date) for when an appeal was "
                    "decided. A follow-up keeps the basis of the count it refers to."
                ),
            ),
        ),
    )


def count_problem(
    args: _Args, semantic: SemanticLayer, *, need_year: bool = True
) -> str | None:
    """Say what is wrong with a count's arguments for this borough, if anything.

    Returned to the model as an error, so it can correct the call instead of
    getting a count that means something else.

    Args:
        args: Validated ``count_applications`` (or ``search_applications``)
            arguments.
        semantic: The borough's semantic layer.
        need_year: Whether a year is required (counts); a search lists the newest
            without one.

    Returns:
        The problem in one sentence, or ``None`` when the call is fine.
    """
    name = BOROUGH_NAMES[semantic.borough]
    for field in ("decision", "application_type", "appeal_decision"):
        value = getattr(args, field)
        if value is not None and value not in semantic.allowed_values(field):
            return f"{value!r} is not a {field} {name} uses"
    if need_year and args.year is None and not args.pending:
        return "year is required (only pending=true counts can leave it out)"
    if args.month is not None and args.year is None:
        return "month needs a year"
    if args.pending and args.decision is not None:
        return "pending applications have no council decision yet: drop one of them"
    if (args.date_basis == "appeal_decided") != (args.appeal_decision is not None):
        return (
            "use date_basis='appeal_decided' together with appeal_decision, and "
            "only then"
        )
    return None


def count_filters(args: _Args, semantic: SemanticLayer) -> list[dict[str, Any]]:
    """Build the Datahub filter clauses for ``count_applications``.

    The scope filter is added by the Datahub client; these come after it.

    Args:
        args: Validated ``count_applications`` arguments (``count_problem`` found
            nothing wrong).
        semantic: The borough's semantic layer.

    Returns:
        The exclusion of dropped records; the year (or month) range on the chosen
        date field; then one clause per filter asked for: council decision (with
        the ``decided`` default filter), pending (the ``pending`` default filter),
        application type, appeal outcome.
    """
    filters = [_excluded_records(semantic)]
    if args.year is not None:
        field = _default_filter(semantic, "year").date_field[
            DATE_BASIS_KEY[args.date_basis]
        ]
        filters.append({"range": {field: _date_range(args.year, args.month)}})
    if args.decision is not None:
        filters.append(_default_clause(semantic, "decided", [args.decision]))
    if args.pending:
        pending = _default_filter(semantic, "pending")
        filters.append(_default_clause(semantic, "pending", pending.status_in))
    if args.application_type is not None:
        filters.append(_is("application_type", args.application_type))
    if args.appeal_decision is not None:
        # A real outcome never matches the deleted appeals' "ZZZ", so the appeals
        # filter's exclude_rules (deleted_appeal) hold without another clause.
        appeals = _default_filter(semantic, "appeals")
        filters.append(
            _is(appeals.filter_on or "appeal_decision", args.appeal_decision)
        )
    return filters


def _date_range(year: int, month: int | None) -> dict[str, str]:
    """Build the ``gte``/``lt`` range of one year or one month, in the Datahub format.

    Args:
        year: The calendar year.
        month: A month of it, or ``None`` for the whole year.

    Returns:
        E.g. ``{"gte": "01/03/2026", "lt": "01/04/2026"}``.
    """
    if month is None:
        start, end = date(year, 1, 1), date(year + 1, 1, 1)
    else:
        start = date(year, month, 1)
        end = date(year + month // 12, month % 12 + 1, 1)
    return {"gte": f"{start:%d/%m/%Y}", "lt": f"{end:%d/%m/%Y}"}


def _default_clause(
    semantic: SemanticLayer, filter_id: str, values: list[str]
) -> dict[str, Any]:
    """Match one of ``values`` on a default filter's field, without its flags.

    Args:
        semantic: The borough's semantic layer.
        filter_id: ``decided`` or ``pending`` (``live_api.yaml``).
        values: The values to match, e.g. ``["Refused"]`` or the pending statuses.

    Returns:
        A ``bool`` clause: one of the values, and none of the excluded flags.
    """
    default = _default_filter(semantic, filter_id)
    field = default.filter_on or "decision"
    return {
        "bool": {
            "filter": [
                _is(field, values[0]) if len(values) == 1 else _any_of(field, values)
            ],
            "must_not": [
                _flag_condition(flag, semantic) for flag in default.exclude_flags
            ],
        }
    }


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


def search_args_model(
    semantic: SemanticLayer | Sequence[SemanticLayer],
) -> type[_Args]:
    """Build the argument model of ``search_applications``: the count's, plus text.

    Args:
        semantic: One borough's semantic layer, or several.

    Returns:
        A Pydantic model whose JSON schema is what the model sees.
    """
    text = Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)
    ]
    return create_model(
        "SearchApplicationsArgs",
        __base__=count_args_model(semantic),
        description_words=(
            text | None,
            Field(
                default=None,
                description="Words that must all appear in the description, "
                "e.g. 'basement'.",
            ),
        ),
        address_text=(
            text | None,
            Field(
                default=None,
                description="A street, place or postcode in the address, matched as "
                "a phrase, e.g. 'Church Road'.",
            ),
        ),
        limit=(
            int,
            Field(default=5, ge=1, le=10, description="How many to list (1-10)."),
        ),
    )


async def search_applications(
    args: _Args,
    *,
    semantic: SemanticLayer,
    datahub: DatahubClient,
    citation_id: str,
    today: date | None = None,
) -> ToolOutput:
    """List the newest matching applications on the live Datahub, cleaned.

    The filters are the count's (``count_filters``), so a list and a count with the
    same arguments agree; the text clauses come after them. Records are cleaned
    one by one, as for the details tool, and summarised to ``SEARCH_FIELDS``.

    Args:
        args: Validated ``search_applications`` arguments.
        semantic: The borough's semantic layer.
        datahub: The live Datahub client (scoped and cached).
        citation_id: The id this call's citation gets.
        today: The date ``status_stale`` is measured from; tests pass a fixed one.

    Returns:
        ``{"total", "shown", "applications", "searched", "notes", "citation"}`` for
        the model, and one citation for the search.

    Raises:
        DatahubError: If the Datahub can't answer (the endpoint answers 503/504).
    """
    field = _default_filter(semantic, "year").date_field[
        DATE_BASIS_KEY[args.date_basis]
    ]
    filters = count_filters(args, semantic)
    if args.description_words:
        filters.append(
            {
                "match": {
                    "description": {"query": args.description_words, "operator": "and"}
                }
            }
        )
    if args.address_text:
        filters.append(
            {
                "multi_match": {
                    "query": args.address_text,
                    "type": "phrase",
                    "fields": list(ADDRESS_FIELDS),
                }
            }
        )
    with logfire.span("tool search_applications", args=args.model_dump()) as span:
        found = await datahub.search(
            *filters,
            size=args.limit,
            sort=[{field: {"order": "desc", "missing": "_last"}}],
            fields=semantic.live.request_fields,
        )
        rows = [
            row
            for record in found.records
            if (row := _cleaned(record, semantic, today or date.today())) is not None
        ]
        span.set_attributes({"total": found.total, "shown": len(rows)})

    returned = {ref.field for ref in semantic.live.returned}
    applications = []
    for row in rows:
        summary = {f: _plain(row.get(f)) for f in SEARCH_FIELDS if f in returned}
        if isinstance(summary.get("description"), str):
            summary["description"] = summary["description"][:SEARCH_DESCRIPTION_CHARS]
        applications.append(summary)

    searched = _describe(args, semantic)
    if args.description_words:
        searched += f', with "{args.description_words}" in the description'
    if args.address_text:
        searched += f', with "{args.address_text}" in the address'
    searched += f", newest first by {field}"
    citation = Citation(
        id=citation_id,
        text=_cite(semantic, searched, found.retrieved_at),
        query=found.query,
        retrieved_at=found.retrieved_at,
    )
    notes = _notes(args, semantic)
    if args.address_text and found.total == 0:
        # Addresses are published unevenly: no match is not proof of no applications.
        notes += [
            " ".join(rule.rule.split())
            for rule in semantic.live.live_rules
            if rule.id == "address_search_no_match"
        ]
    result = {
        "total": found.total,
        "shown": len(applications),
        "applications": applications,
        "searched": searched,
        "notes": notes,
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
        The count, or an error the model can correct (``count_problem``: e.g. a
        decision the borough doesn't use, or a month without a year).

    Raises:
        DatahubError: If the Datahub can't answer (the endpoint answers 503/504).
    """
    area = areas[args.local_authority.lower()]
    if (error := count_problem(args, area.semantic)) is not None:
        return ToolOutput(result={"error": error}, citations=[])
    return await count_applications(
        args, semantic=area.semantic, datahub=area.datahub, citation_id=citation_id
    )


async def search_in(
    args: _Args, *, areas: Mapping[str, Area], citation_id: str
) -> ToolOutput:
    """Search in the borough the model named, with that borough's rules.

    Args:
        args: Validated ``search_applications`` arguments.
        areas: The covered boroughs, by borough key.
        citation_id: The id this call's citation gets.

    Returns:
        The list, or an error the model can correct (``count_problem``, except
        that a year is optional).

    Raises:
        DatahubError: If the Datahub can't answer (the endpoint answers 503/504).
    """
    area = areas[args.local_authority.lower()]
    if (error := count_problem(args, area.semantic, need_year=False)) is not None:
        return ToolOutput(result={"error": error}, citations=[])
    return await search_applications(
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
    text = f"{args.local_authority}"
    if args.application_type is not None:
        text += f" {args.application_type}"
    text += " applications"
    if args.pending:
        text += " still awaiting a decision"
    if args.year is not None:
        field = _default_filter(semantic, "year").date_field[
            DATE_BASIS_KEY[args.date_basis]
        ]
        when = (
            f"{date(args.year, args.month, 1):%B} {args.year}"
            if args.month
            else args.year
        )
        verb = args.date_basis.replace("_", " ")
        text += f" {verb} in {when} ({field})"
    if args.decision is not None:
        text += f" with council decision {args.decision}"
        if "decision_not_issued" in _default_filter(semantic, "decided").exclude_flags:
            text += ", excluding decisions recorded but not yet issued"
    if args.appeal_decision is not None:
        text += f" with appeal decision {args.appeal_decision}"
    if args.pending and _default_filter(semantic, "pending").exclude_flags:
        text += ", excluding records whose pending status is out of date"
    return text


def _notes(args: _Args, semantic: SemanticLayer) -> list[str]:
    """Pick the YAML rules that apply to this count, for the model to pass on.

    Args:
        args: Validated ``count_applications`` arguments.
        semantic: The borough's semantic layer.

    Returns:
        The ``year`` default-filter rule (with a year); with a decision or pending,
        the live rule on published values; with pending, the borough's live rule on
        out-of-date pending records. Each on one line, as written in
        ``live_api.yaml``.
    """
    notes = [_default_filter(semantic, "year").rule] if args.year is not None else []
    wanted = set()
    if args.decision is not None or args.pending:
        wanted.add("counts_use_published_values")
    if args.pending:
        wanted |= {"pending_may_be_stale", "status_stale_today"}
    notes += [rule.rule for rule in semantic.live.live_rules if rule.id in wanted]
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
    if flag == "status_stale":
        # Measured from today, as status_stale_today in live_api.yaml says.
        cutoff = date.today() - timedelta(days=rule["stale_after_days"])
        return {
            "bool": {
                "should": [
                    {"terms": {"id": rule.get("ids", [])}},
                    {
                        "bool": {
                            "filter": [
                                _any_of("status", rule["when_status"]),
                                {
                                    "range": {
                                        "decision_target_date": {
                                            "lt": f"{cutoff:%d/%m/%Y}"
                                        }
                                    }
                                },
                            ],
                            "must_not": [{"exists": {"field": "decision_date"}}],
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        }
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
