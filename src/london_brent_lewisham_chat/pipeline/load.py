"""Load a borough's newest raw snapshot and its reference boundaries into PostGIS.

Three schemas (PLAN.md §11): ``raw`` keeps the documents as JSONB, ``core`` the
cleaned, typed table built from ``cleaning.yaml``, ``ref`` the official ONS
boundaries. ``core`` is rebuilt on every load, so a rule change only needs a reload.
The golden rule is enforced again by the database itself: ``borough`` has a CHECK
constraint, and every reference shape must lie in the borough.
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from ..config import PipelineSettings
from ..scope import ONS_CODES, Borough, borough_name
from . import clean, extract, postcodes, schema

logger = logging.getLogger(__name__)

EDGE_TOLERANCE_M = 20
"""How far outside the boundary a centroid may be and still count as on its edge.

The boundary follows road centre lines; sites on those roads (most are "Street
Record" rows) land up to 16 m outside in the Brent data.
"""

SCHEMA_SQL = f"""
{schema.EXTENSIONS_SQL}
create schema if not exists raw;
create schema if not exists core;
create schema if not exists ref;
{schema.RAW_APPLICATIONS}
{schema.REF_BOROUGHS}
{schema.REF_WARDS}
drop table if exists core.applications;  -- rebuilt from raw on every load
{schema.CORE_APPLICATIONS}
"""
"""Creates the local schemas and tables; ``core.applications`` is rebuilt each load."""

COLUMNS = [
    "id", "borough", "lpa_app_no", "lpa_name", "valid_date", "status", "decision",
    "decision_date", "decision_not_issued", "outcome_source", "approximate_decision_date",
    "application_type", "description",
    "address", "address_kind", "decision_target_date", "appeal_decision",
    "appeal_decision_date", "appeal_record_deleted", "url_planning_app", "legacy_record", "ward",
    "last_updated", "lat", "lon", "site_geojson", "placeholder_centroid",
    "centroid_source",
]  # fmt: skip
"""Order of the values ``clean`` produces, as copied into the staging table."""


def newest_snapshot(data_dir: Path, borough: Borough) -> tuple[Path, date]:
    """Find the newest raw snapshot for a borough.

    Args:
        data_dir: Root of the local data folder.
        borough: One of the allowed boroughs.

    Returns:
        The file and its snapshot date.

    Raises:
        FileNotFoundError: If there is no snapshot yet.
    """
    files = sorted((data_dir / "raw" / borough).glob("applications_*.jsonl.gz"))
    if not files:
        raise FileNotFoundError(
            f"no snapshot in {data_dir / 'raw' / borough}; run extract"
        )
    newest = files[-1]
    return newest, date.fromisoformat(newest.name.removeprefix("applications_")[:10])


def load_reference(conn: psycopg.Connection, data_dir: Path, borough: Borough) -> None:
    """Load the borough outline and its wards (ONS) into ``ref``.

    The outline must carry the borough's own ONS code, and every ward must lie in
    the outline, so a file covering anywhere else is refused.

    Args:
        conn: Open database connection (inside a transaction).
        data_dir: Root of the local data folder.
        borough: One of the allowed boroughs.

    Raises:
        ValueError: If a file has a shape from outside the borough.
    """
    name, code, folder = (
        borough_name(borough),
        ONS_CODES[borough],
        data_dir / "external" / borough,
    )
    outline = json.loads((folder / f"{borough}_boundary_2024.geojson").read_text())[
        "features"
    ]
    if [f["properties"]["LAD24CD"] for f in outline] != [code]:
        raise ValueError(f"boundary file is not {code} alone")
    conn.execute("delete from ref.wards where borough = %s", [name])
    conn.execute("delete from ref.boroughs where borough = %s", [name])
    conn.execute(
        "insert into ref.boroughs values (%s, %s, ST_Multi(ST_GeomFromGeoJSON(%s)))",
        [name, code, json.dumps(outline[0]["geometry"])],
    )
    for vintage, prefix in [(2024, "WD24"), (2021, "WD21")]:
        wards = json.loads((folder / f"{borough}_wards_{vintage}.geojson").read_text())
        for feature in wards["features"]:
            p = feature["properties"]
            conn.execute(
                "insert into ref.wards values (%s, %s, %s, %s, ST_Multi(ST_GeomFromGeoJSON(%s)))",
                [
                    name,
                    vintage,
                    p[f"{prefix}CD"],
                    p[f"{prefix}NM"],
                    json.dumps(feature["geometry"]),
                ],
            )
    outside = conn.execute(
        """select w.ward_code from ref.wards w join ref.boroughs b using (borough)
           where w.borough = %s and not ST_Within(ST_PointOnSurface(w.geom), b.geom)""",
        [name],
    ).fetchall()
    if outside:
        raise ValueError(f"wards outside {name}: {outside}")


def _copy_raw(conn: psycopg.Connection, path: Path, borough: Borough, on: date) -> int:
    """Copy every document of a snapshot into ``raw.applications``.

    Args:
        conn: Open database connection (inside a transaction).
        path: The snapshot file.
        borough: The borough it belongs to.
        on: The snapshot date.

    Returns:
        The number of documents copied.
    """
    name = borough_name(borough)
    conn.execute(
        "delete from raw.applications where borough = %s and snapshot_date = %s",
        [name, on],
    )
    n = 0
    with conn.cursor().copy(
        "copy raw.applications (id, borough, snapshot_date, doc) from stdin"
    ) as cp:
        for record in extract.read_snapshot(path, borough):
            cp.write_row(
                [record["id"], name, on, json.dumps(record, ensure_ascii=False)]
            )
            n += 1
    return n


def _load_core(
    conn: psycopg.Connection,
    rows: list[dict[str, Any]],
    rules: dict[str, Any],
    borough: Borough,
    on: date,
) -> None:
    """Replace the borough's rows in ``core.applications`` and derive the spatial columns.

    Rows go through a staging table so geometry is built in SQL. Site polygons are
    repaired with ``ST_MakeValid`` (invalid rings stop spatial queries working).
    ``location_check`` and ``ward_2022`` come from the official ONS boundaries.
    ``status_stale`` is set here because it depends on the snapshot date.

    Args:
        conn: Open database connection (inside a transaction).
        rows: Cleaned rows from ``clean.clean_all``.
        rules: The parsed cleaning rules (for ``status_stale``).
        borough: The borough being loaded.
        on: The snapshot date.
    """
    name = borough_name(borough)
    conn.execute(
        "create temp table stage ("
        + ", ".join(f"{c} text" for c in COLUMNS)
        + ") on commit drop"
    )
    with conn.cursor().copy(f"copy stage ({', '.join(COLUMNS)}) from stdin") as cp:
        for row in rows:
            cp.write_row([None if row[c] is None else str(row[c]) for c in COLUMNS])
    conn.execute("delete from core.applications where borough = %s", [name])
    conn.execute(
        """
        insert into core.applications
        select id, borough, lpa_app_no, lpa_name, valid_date::date, status, decision,
               decision_date::date, decision_not_issued::boolean, false, outcome_source,
               approximate_decision_date::boolean, application_type,
               description, address, address_kind, decision_target_date::date,
               appeal_decision, appeal_decision_date::date, appeal_record_deleted::boolean, url_planning_app,
               legacy_record::boolean, ward, null, 'no_centroid',
               placeholder_centroid::boolean, centroid_source,
               ST_SetSRID(ST_MakePoint(lon::float8, lat::float8), 4326),
               ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(site_geojson), 4326)),
               last_updated::timestamptz, %s
        from stage
        """,
        [on],
    )
    # null centroid stays 'no_centroid'; anything farther out than the edge
    # tolerance is left as NULL, which the NOT NULL check turns into a failed load.
    conn.execute(
        """update core.applications a set location_check = case
               when ST_Within(a.centroid, b.geom) then 'inside'
               when ST_DWithin(a.centroid::geography, b.geom::geography, %s) then 'boundary_edge'
           end
           from ref.boroughs b
           where a.borough = b.borough and a.borough = %s and a.centroid is not null""",
        [EDGE_TOLERANCE_M, name],
    )
    stale = next(r for r in rules["derived"] if r["id"] == "status_stale")
    conn.execute(
        """update core.applications set status_stale = true
           where borough = %s and (id = any(%s) or (
               status = any(%s) and decision_date is null
               and decision_target_date < snapshot_date - %s::int))""",
        [name, stale.get("ids", []), stale["when_status"], stale["stale_after_days"]],
    )
    conn.execute(
        """update core.applications a set ward_2022 = w.ward_name
           from ref.wards w
           where w.vintage = 2024 and w.borough = a.borough and a.borough = %s
             and ST_Within(a.centroid, w.geom)""",
        [name],
    )


def run(settings: PipelineSettings, borough: Borough) -> None:
    """Load the newest snapshot for a borough, all in one transaction.

    One transaction means a failed load leaves the previous data as it was.

    Args:
        settings: Pipeline settings (database, data folder).
        borough: One of the allowed boroughs.
    """
    path, on = newest_snapshot(settings.data_dir, borough)
    rules = clean.load_rules(Path("semantic") / borough / "cleaning.yaml")
    centroids = postcodes.read_cache(settings.data_dir, borough)
    result = clean.clean_all(
        extract.read_snapshot(path, borough), rules, borough, centroids
    )
    with psycopg.connect(settings.conninfo()) as conn:
        conn.execute(SCHEMA_SQL)
        load_reference(conn, settings.data_dir, borough)
        raw_count = _copy_raw(conn, path, borough, on)
        _load_core(conn, result.rows, rules, borough, on)
    logger.info(
        "load finished",
        extra={
            "borough": borough,
            "snapshot": str(path),
            "raw": raw_count,
            "loaded": len(result.rows),
            "dropped": dict(result.dropped),
            "applied": dict(result.applied),
        },
    )
