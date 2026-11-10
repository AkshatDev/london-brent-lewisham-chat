"""Download every application for one borough into a dated raw snapshot.

The Datahub overwrites records in place and keeps no history, so these snapshots
are our only record of what the data said on a given day. They stay local.
"""

import gzip
import json
import logging
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ..scope import Borough, in_scope, scoped_query

logger = logging.getLogger(__name__)

PAGE_SIZE = 1000
"""Records per request; the gateway's maximum for ``_search``."""


def count(client: httpx.Client, borough: Borough) -> int:
    """Ask the Datahub how many in-scope applications it holds.

    Args:
        client: HTTP client with the Datahub base URL and header set.
        borough: One of the allowed boroughs.

    Returns:
        The number of applications matching the scope filter.
    """
    response = client.post(
        "/applications/_count", json={"query": scoped_query(borough)}
    )
    response.raise_for_status()
    return response.json()["count"]


def fetch_all(client: httpx.Client, borough: Borough) -> Iterator[dict[str, Any]]:
    """Yield every in-scope application, paging with ``search_after``.

    ``search_after`` sorted by the unique ``id`` is needed because plain paging stops
    at 10,000 records. Each record is checked against the scope rule again;
    anything outside the borough is dropped and logged as a warning.

    Args:
        client: HTTP client with the Datahub base URL and header set.
        borough: One of the allowed boroughs.

    Yields:
        One application document at a time.
    """
    body: dict[str, Any] = {
        "size": PAGE_SIZE,
        "query": scoped_query(borough),
        "sort": [{"id": "asc"}],
    }
    while True:
        response = client.post("/applications/_search", json=body)
        response.raise_for_status()
        hits = response.json()["hits"]["hits"]
        if not hits:
            return
        for hit in hits:
            record = hit["_source"]
            if in_scope(record, borough):
                yield record
            else:
                logger.warning(
                    "out-of-scope record dropped",
                    extra={"id": record.get("id"), "borough": record.get("borough")},
                )
        body["search_after"] = hits[-1]["sort"]


def write_snapshot(
    client: httpx.Client, borough: Borough, data_dir: Path, on: date
) -> Path:
    """Download all in-scope applications to ``raw/<borough>/applications_<date>.jsonl.gz``.

    The record count is compared with ``_count`` so a short download is noticed
    instead of being loaded as if it were complete.

    Args:
        client: HTTP client with the Datahub base URL and header set.
        borough: One of the allowed boroughs.
        data_dir: Root of the local data folder.
        on: The snapshot date, used in the file name.

    Returns:
        Path of the snapshot written.

    Raises:
        RuntimeError: If fewer or more records arrived than the API counts.
    """
    expected = count(client, borough)
    path = data_dir / "raw" / borough / f"applications_{on.isoformat()}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    written = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as out:
        for record in fetch_all(client, borough):
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    if written != expected:
        raise RuntimeError(f"downloaded {written} records, API counts {expected}")
    # Rename only when complete, so a half-written file never looks like a snapshot.
    tmp.replace(path)
    logger.info("snapshot written", extra={"path": str(path), "records": written})
    return path


def read_snapshot(path: Path, borough: Borough) -> Iterator[dict[str, Any]]:
    """Read a raw snapshot back, re-checking the scope of every record.

    Args:
        path: A ``.jsonl.gz`` file written by ``write_snapshot``.
        borough: The borough the snapshot is for.

    Yields:
        One in-scope application document at a time.

    Raises:
        ValueError: If the file contains a record outside the borough.
    """
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            record = json.loads(line)
            if not in_scope(record, borough):
                raise ValueError(f"out-of-scope record {record.get('id')!r} in {path}")
            yield record
