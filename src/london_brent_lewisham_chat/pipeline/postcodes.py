"""Replace placeholder centroids with postcode centroids from postcodes.io.

26 Brent records have a placeholder centroid in the sea off Cornwall (grid origin
0,0). Their postcode gives a usable point. Only lookups that postcodes.io places
in the borough itself (by ONS code) are kept, so the cache never holds a place
outside scope.
"""

import json
import logging
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import httpx

from ..scope import ONS_CODES, Borough

logger = logging.getLogger(__name__)

POSTCODES_IO = "https://api.postcodes.io/postcodes"
BULK_LIMIT = 100
"""Most postcodes postcodes.io accepts in one bulk lookup."""
PLACEHOLDER_MAX_LAT = 50.0
"""London is near latitude 51.5; anything south of 50 is not a real site location."""

FULL_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")


def is_placeholder(lat: Any, lon: Any) -> bool:  # noqa: ANN401 (lat/lon come as float or text)
    """Tell whether a centroid is the placeholder rather than a real point.

    Args:
        lat: Latitude from the record.
        lon: Longitude from the record (unused; a placeholder is told by latitude).

    Returns:
        ``True`` for a missing or placeholder centroid.
    """
    return lat is None or float(lat) < PLACEHOLDER_MAX_LAT


def record_postcode(record: Mapping[str, Any]) -> str | None:
    """Find a full postcode for a record: the postcode field, else one in site_name.

    Args:
        record: A raw application document.

    Returns:
        The postcode in upper case with one space, or ``None``.
    """
    for text in (record.get("postcode"), record.get("site_name")):
        if isinstance(text, str) and (m := FULL_POSTCODE.search(text.upper())):
            compact = m.group(0).replace(" ", "")
            return f"{compact[:-3]} {compact[-3:]}"
    return None


def cache_path(data_dir: Path, borough: Borough) -> Path:
    """Return where the postcode lookups for a borough are cached.

    Args:
        data_dir: Root of the local data folder.
        borough: One of the allowed boroughs.

    Returns:
        The JSON cache file path.
    """
    return data_dir / "external" / borough / "postcode_centroids.json"


def refresh_cache(
    records: Iterable[Mapping[str, Any]], data_dir: Path, borough: Borough
) -> Path:
    """Look up the postcodes of placeholder-centroid records and cache the in-borough ones.

    Args:
        records: Raw application documents.
        data_dir: Root of the local data folder.
        borough: One of the allowed boroughs.

    Returns:
        The cache file written: ``{postcode: {"lat": .., "lon": ..}}``.
    """
    wanted = sorted(
        {
            pc
            for r in records
            if is_placeholder((r.get("centroid") or {}).get("lat"), None)
            and (pc := record_postcode(r))
        }
    )
    results: list[dict[str, Any]] = []
    for start in range(0, len(wanted), BULK_LIMIT):
        batch = wanted[start : start + BULK_LIMIT]
        response = httpx.post(POSTCODES_IO, json={"postcodes": batch}, timeout=60)
        response.raise_for_status()
        results += response.json()["result"]
    kept: dict[str, dict[str, float]] = {}
    for item in results:
        found = item["result"]
        if found and found["codes"]["admin_district"] == ONS_CODES[borough]:
            kept[item["query"]] = {"lat": found["latitude"], "lon": found["longitude"]}
    path = cache_path(data_dir, borough)
    path.write_text(json.dumps(kept, indent=1, sort_keys=True))
    logger.info("postcodes cached", extra={"asked": len(wanted), "kept": len(kept)})
    return path


def read_cache(data_dir: Path, borough: Borough) -> dict[str, dict[str, float]]:
    """Read the cached postcode centroids (empty if there is no cache yet).

    Args:
        data_dir: Root of the local data folder.
        borough: One of the allowed boroughs.

    Returns:
        ``{postcode: {"lat": .., "lon": ..}}``.
    """
    path = cache_path(data_dir, borough)
    return json.loads(path.read_text()) if path.exists() else {}
