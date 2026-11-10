"""The golden rule in code: the service only ever covers Brent and Lewisham.

The two boroughs are a fixed allow-list here, not a setting, so no configuration,
argument or header can widen the scope. Scope is by location: a record is in
scope when its ``borough`` is the borough, or ``borough`` is missing and its
``lpa_name`` is the borough (e.g. an application decided by OPDC for a site in
Brent has ``borough`` = Brent and is in scope).
"""

from collections.abc import Mapping
from typing import Any, Literal, get_args

Borough = Literal["brent", "lewisham"]
"""The only boroughs the service may ever cover."""

BOROUGH_NAMES: dict[Borough, str] = {"brent": "Brent", "lewisham": "Lewisham"}
"""How each borough is spelt in the Datahub's ``borough`` and ``lpa_name`` fields."""

ONS_CODES: dict[Borough, str] = {"brent": "E09000005", "lewisham": "E09000023"}
"""ONS local authority codes, used to fetch and check official boundaries."""


def borough_name(borough: Borough) -> str:
    """Return the Datahub spelling of an allowed borough.

    Checked at run time as well as by the type checker, because a plain string can
    reach here from the command line.

    Args:
        borough: One of the allowed boroughs.

    Returns:
        The name as the Datahub writes it, e.g. ``"Brent"``.

    Raises:
        ValueError: If ``borough`` is not on the allow-list.
    """
    if borough not in get_args(Borough):
        raise ValueError(f"borough {borough!r} is out of scope")
    return BOROUGH_NAMES[borough]


def scope_filter(borough: Borough) -> dict[str, Any]:
    """Build the Elasticsearch filter that limits a query to one borough.

    Every upstream query must include this, so the server only ever returns
    in-scope records. ``term`` on the ``.raw`` keyword fields means an exact match:
    a ``borough`` that merely contains the name is not matched.

    Args:
        borough: One of the allowed boroughs.

    Returns:
        A ``bool`` query: ``borough`` is the borough, or ``borough`` is missing and
        ``lpa_name`` is the borough.
    """
    name = borough_name(borough)
    return {
        "bool": {
            "should": [
                {"term": {"borough.raw": name}},
                {
                    "bool": {
                        "filter": [{"term": {"lpa_name.raw": name}}],
                        "must_not": [{"exists": {"field": "borough.raw"}}],
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


def scoped_query(borough: Borough, *filters: dict[str, Any]) -> dict[str, Any]:
    """Combine the scope filter with any other filters into one query.

    The only way the pipeline builds a query, so a query without the scope filter
    can't be built by accident.

    Args:
        borough: One of the allowed boroughs.
        *filters: Further Elasticsearch filter clauses.

    Returns:
        A ``bool`` query whose filters always start with the scope filter.
    """
    return {"bool": {"filter": [scope_filter(borough), *filters]}}


def in_scope(record: Mapping[str, Any], borough: Borough) -> bool:
    """Check a downloaded record against the scope rule again, on our side.

    Mirrors ``scope_filter`` exactly (a missing ``borough`` is absent or null, as
    in Elasticsearch), so a record the server should not have returned is caught.

    Args:
        record: One application document from the Datahub.
        borough: One of the allowed boroughs.

    Returns:
        ``True`` if the record is in scope for ``borough``.
    """
    name = borough_name(borough)
    if record.get("borough") is not None:
        return record["borough"] == name
    return record.get("lpa_name") == name
