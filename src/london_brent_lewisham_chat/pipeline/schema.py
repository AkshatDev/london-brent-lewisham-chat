"""Table definitions, shared by the local load and the push to a hosted database.

One definition per table, so the hosted copy can never drift from the local one.
Every table has a CHECK constraint on ``borough``: the database itself refuses a
row from outside the two boroughs (golden rule).
"""

from ..scope import BOROUGH_NAMES

_ALLOWED = ", ".join(f"'{name}'" for name in BOROUGH_NAMES.values())

EXTENSIONS_SQL = "create extension if not exists postgis;"

RAW_APPLICATIONS = f"""
create table if not exists raw.applications (
    id text not null,
    borough text not null check (borough in ({_ALLOWED})),
    snapshot_date date not null,
    doc jsonb not null,
    primary key (id, snapshot_date)
);
"""

REF_BOROUGHS = f"""
create table if not exists ref.boroughs (
    borough text primary key check (borough in ({_ALLOWED})),
    ons_code text not null,
    geom geometry(MultiPolygon, 4326) not null
);
"""

REF_WARDS = f"""
create table if not exists ref.wards (
    borough text not null check (borough in ({_ALLOWED})),
    vintage int not null,
    ward_code text not null,
    ward_name text not null,
    geom geometry(MultiPolygon, 4326) not null,
    primary key (vintage, ward_code)
);
"""

CORE_APPLICATIONS = f"""
create table if not exists core.applications (
    id text primary key,
    borough text not null check (borough in ({_ALLOWED})),
    lpa_app_no text not null,
    lpa_name text,
    valid_date date,
    status text not null,
    decision text,
    decision_date date,
    decision_not_issued boolean not null,
    status_stale boolean not null default false,
    outcome_source text not null check (outcome_source in ('datahub', 'portal_manual_check')),
    approximate_decision_date boolean not null,
    application_type text,
    description text,
    address text,
    address_kind text not null,
    decision_target_date date,
    appeal_decision text,
    appeal_decision_date date,
    appeal_record_deleted boolean not null,
    url_planning_app text,
    legacy_record boolean not null,
    ward text,
    ward_2022 text,
    location_check text not null
        check (location_check in ('inside', 'boundary_edge', 'no_centroid')),
    placeholder_centroid boolean not null,
    centroid_source text,
    centroid geometry(Point, 4326),
    site geometry(Geometry, 4326),
    last_updated timestamptz,
    snapshot_date date not null
);
create index if not exists applications_centroid_idx on core.applications using gist (centroid);
create index if not exists applications_site_idx on core.applications using gist (site);
"""

PUSHED_TABLES: dict[str, str] = {
    "core.applications": CORE_APPLICATIONS,
}
"""The tables pushed to the hosted database: only what the bot needs today.

``ward_2022`` and ``location_check`` are already on every row, so the bot needs no
boundary shapes yet. ``ref.wards`` / ``ref.boroughs`` (maps, pre-2022 ward names,
areas) can be added here when a bot feature uses them. ``raw`` stays local
(PLAN.md §11).
"""
