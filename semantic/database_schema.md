# Database schema · planning applications (Brent)

Reference for building the chat bot: what is in the database and how to query it
correctly. It covers the data as it is, not how it was produced. Per-field detail
(values, known issues, rules for answers) is in `semantic/brent/<field>.yaml`; how to
cite sources is in `semantic/brent/sources.yaml`.

**Scope:** the London Borough of Brent only (Lewisham will be added to the same
tables later). Every row is a site inside Brent, checked by location against the
official boundary. `borough` can only be `Brent` or `Lewisham` (database constraint).
The bot must never answer about anywhere else.

## Connection

- **Local:** PostgreSQL 18 with PostGIS 3.6, host `127.0.0.1`, port `5433`; user,
  password and database name are in `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`,
  `POSTGRES_DB`).
- **Hosted (for the bot):** Neon, URL in `.env` as `NEON_DATABASE_URL`; holds
  `core.applications` with the same columns, constraints and spatial indexes.
All coordinates are WGS84 (`SRID 4326`, longitude/latitude).

## Tables

**The bot's hosted database (Neon) has only `core.applications`.** The other tables
exist in the local PostGIS only; don't build bot features on them yet.

| Table | On Neon | Rows | One row is | Use it for |
|---|---|---|---|---|
| `core.applications` | **yes** | 36,531 | one planning application | **every question**: the cleaned, typed table |
| `ref.wards` | no (local) | 43 | one ward boundary | ward shapes (22 current + 21 pre-2022); not needed for "in ward X", use `ward_2022` |
| `ref.boroughs` | no (local) | 1 | one borough boundary | the Brent outline |
| `raw.applications` | no (local) | 36,952 | one application document as published, per download | the nested lists not yet in `core` (homes, floorspace, ...) |

Snapshot: data as published by the Planning London Datahub, downloaded 2026-11-10
(`snapshot_date`). Applications validated 1987-04-23 to 2026-11-04.

## `core.applications`

Primary key `id`. Spatial (GiST) indexes on `centroid` and `site`.

### Identity

| Column | Type | Null | Meaning |
|---|---|---|---|
| `id` | text | no | Record id: authority + reference, e.g. `Brent-18_1022`. Unique. |
| `lpa_app_no` | text | no | The planning reference people quote, e.g. `18/1022`. Unique. Show this, not `id`. |
| `lpa_name` | text | yes | Authority that decided it: `Brent` (36,377) or `OPDC` (154, Old Oak and Park Royal Development Corporation). Not where the site is. |
| `borough` | text | no | Always `Brent` here. Not a way to split Brent up. |

### The application

| Column | Type | Null | Meaning |
|---|---|---|---|
| `application_type` | text | yes | `All Other` (23,043), `Householder` (9,409), `Prior Approval` (4,079). Not an outcome. |
| `description` | text | yes | Free text of what was proposed; mostly unique. Search by meaning or `ILIKE`, never exact match. `No description provided by LDMS` (227) means no description. |
| `address` | text | yes | One address text for the site. Search case-insensitively (`ILIKE`); never exact match. |
| `address_kind` | text | no | How complete `address` is: `with_full_postcode` (30,729), `several_parts`, `number_only`, `one_part`, `street_record` (a whole street is the site), `missing`. |
| `url_planning_app` | text | yes | Link to the application on Brent's planning portal (documents, decision notice). None for legacy records. |
| `legacy_record` | boolean | no | `true` (3,528) for records imported from Brent's old system: they never have `valid_date`, `decision_target_date` or `url_planning_app`. |

### Dates (all `date`)

| Column | Null | Meaning |
|---|---|---|
| `valid_date` | yes | When the application became valid. Missing for all legacy records. |
| `decision_target_date` | yes | Recorded target date for the decision; exists from 2018-01-24 on. Never compute it yourself. |
| `decision_date` | yes | Date of the authority's decision (not the appeal). |
| `appeal_decision_date` | yes | Date of the appeal decision. Reliable for year trends from 2019 on only. |

### Outcome (all `text`)

| Column | Null | Meaning |
|---|---|---|
| `status` | no | Current stage or outcome, overwritten as the case moves on: `Approved`, `Refused`, `Completed`, `Lapsed`, `Withdrawn`, `Dismissed`, `Superseded`, `Application Received`, `Allowed`, `Commenced`, `Application Under Consideration`, `Appeal Received`, `Closed`, `Split Decision`, `Application Invalid`, `No Objection to Proposal (OBS only)`, `Appeal In Progress`, `Objection Raised to Proposal (OBS only)`, `Unknown`. |
| `decision` | yes | The authority's own decision: `Approved`, `Refused`, `Withdrawn`, `Closed`, `No Objection to Proposal (OBS only)`, `Objection Raised to Proposal (OBS only)`, `Comment Issued`. NULL = not decided yet or not recorded. Not changed by an appeal. |
| `appeal_decision` | yes | Planning Inspectorate's appeal outcome: `Dismissed`, `Allowed`, `Withdrawn`, `Split Decision`. NULL = no decided appeal. |

**Which column answers what:** "was it approved / refused (by the council)?" →
`decision`. "Appeal outcome?" → `appeal_decision`. "Where is it now?" → `status`
(e.g. `decision = Refused` + `status = Allowed`: refused, then won on appeal).

### Flags: read these before answering

| Column | Type | True for | What the bot does |
|---|---|---|---|
| `decision_not_issued` | boolean | 2 | A decision is recorded but not issued yet (awaiting a legal agreement). Treat as **not decided**; leave out of decision counts. |
| `status_stale` | boolean | 34 | Status probably out of date (still "pending" long after its target date). **Don't report or count as pending**; say it hasn't been updated, give the link. |
| `appeal_record_deleted` | boolean | 16 | An appeal was entered and then deleted. Not an appeal; `appeal_decision` is NULL for these. |
| `approximate_decision_date` | boolean | 25 | Month-only decision date (early 1987). Give the month only. |
| `outcome_source` | text | `portal_manual_check`: 11 | Outcome confirmed on Brent's planning portal (2026-11-10) rather than taken from the Datahub; otherwise `datahub`. Count normally; cite as in `sources.yaml`. |

### Location

| Column | Type | Null | Meaning |
|---|---|---|---|
| `centroid` | geometry(Point, 4326) | yes | A point for the site. NULL for 7 (no usable location). Use for maps, distance, "near". |
| `centroid_source` | text | yes | `datahub`, or `postcodes.io` (19: the postcode's centre, not the exact site). |
| `placeholder_centroid` | boolean | no | `true` (26) when the published point was a placeholder (replaced by the postcode centre, or NULL). |
| `site` | geometry(Geometry, 4326) | yes | Site boundary: Polygon (21,927), GeometryCollection (18), NULL (14,586). |
| `location_check` | text | no | `inside` Brent (36,488), `boundary_edge` (36, within 20 m on a boundary road), `no_centroid` (7). |
| `ward_2022` | text | yes | Current ward (2022 boundaries, 22 wards) containing `centroid`. NULL for 43 (`boundary_edge`, `no_centroid`). **Use this for "in ward X".** |
| `ward` | text | yes | Ward as published: mixed old/new names, upper-case copies, "- WARD NO LONGER EXISTS" suffixes. Don't filter on it. |

### Provenance

| Column | Type | Null | Meaning |
|---|---|---|---|
| `last_updated` | timestamptz | yes | When the Datahub last changed the record (2025-03-19 to 2026-11-06). |
| `snapshot_date` | date | no | The day the data was downloaded (2026-11-10). |

## `ref.wards` (local only)

Primary key `(vintage, ward_code)`.

| Column | Type | Meaning |
|---|---|---|
| `borough` | text | `Brent` |
| `vintage` | int | `2024` = current wards (22, boundaries in force since 2022); `2021` = the 21 wards before 2022 |
| `ward_code` | text | ONS ward code |
| `ward_name` | text | Ward name; `vintage = 2024` names are the values of `core.applications.ward_2022` |
| `geom` | geometry(MultiPolygon, 4326) | Ward boundary |

Current wards: Alperton, Barnhill, Brondesbury Park, Cricklewood & Mapesbury, Dollis
Hill, Harlesden & Kensal Green, Kenton, Kilburn, Kingsbury, Northwick Park, Preston,
Queens Park, Queensbury, Roundwood, Stonebridge, Sudbury, Tokyngton, Welsh Harp,
Wembley Central, Wembley Hill, Wembley Park, Willesden Green.

## `ref.boroughs` (local only)

Primary key `borough`. Columns: `borough` (`Brent`), `ons_code` (`E09000005`), `geom`
(geometry(MultiPolygon, 4326), the official boundary).

## `raw.applications` (local only)

Primary key `(id, snapshot_date)`. Columns: `id`, `borough`, `snapshot_date`, `doc`
(jsonb: the full published record). Holds every download; **always filter to the
newest `snapshot_date` and to ids in `core.applications`** (`raw` also has records
left out of `core`: empty, test and duplicate records).

Use it only for data not yet in `core`, mainly the nested lists under
`doc->'application_details'`:

| JSON path | Items | What |
|---|---|---|
| `residential_details.residential_units[]` | 115,594 | homes: `tenure`, `no_bedrooms`, `unit_type`, `change_type`, `gia`, `actual_completion_date`, `superseded_by_lpa_app_no`, ... |
| `existing_proposed_floorspace_details[]` | 10,675 | floorspace by use |
| `constraints_details[]` | 10,693 | site constraints |
| `building_details[]`, `phasing_details[]` | 6,897, 5,174 | buildings, phases |
| `superseding_details[]` | 756 | which application supersedes which |

These lists are **not cleaned or checked yet**: treat numbers from them as unverified,
and say so.

## Relationships (local database)

```
core.applications.id ──── raw.applications.id         (+ newest snapshot_date)
core.applications.ward_2022 ──── ref.wards.ward_name  (vintage = 2024)
core.applications.centroid ──ST_Within── ref.wards.geom / ref.boroughs.geom
core.applications.borough ──── ref.boroughs.borough
```

## Default filters for answers

| Question kind | Filter |
|---|---|
| Decided / approved / refused counts | `decision = '...' and not decision_not_issued` |
| "Pending", "under consideration" | `status in ('Application Received','Application Under Consideration') and not status_stale and not decision_not_issued` |
| Appeal counts | on `appeal_decision` (deleted appeals are NULL already) |
| "In ward X" | `ward_2022 = 'X'` |
| "Near X", maps | `centroid is not null` |
| "In <year>" | say which date you use (`decision_date` for decisions, `valid_date` for submissions); legacy records have no `valid_date` |
| Decision times / on time | only where both dates exist (`valid_date`, `decision_target_date` from 2018 on) |

## Example queries (tested on the 2026-11-10 data)

```sql
-- One application by its reference
select lpa_app_no, status, decision, decision_date, address, url_planning_app
from core.applications where lpa_app_no = '18/1022';

-- Approvals decided in 2025                                         -> 2,380
select count(*) from core.applications
where decision = 'Approved' and not decision_not_issued
  and decision_date >= date '2025-01-01' and decision_date < date '2026-01-01';

-- Applications pending now                                          -> 432 + 61
select status, count(*) from core.applications
where status in ('Application Received', 'Application Under Consideration')
  and not status_stale and not decision_not_issued
group by status;

-- Decisions in a ward since 2024
select decision, count(*) from core.applications
where ward_2022 = 'Kilburn' and decision_date >= date '2024-01-01'
group by decision order by 2 desc;

-- Within 500 m of a point (Wembley Stadium)                          -> 871
select count(*) from core.applications
where ST_DWithin(centroid::geography,
                 ST_SetSRID(ST_MakePoint(-0.2795, 51.5560), 4326)::geography, 500);

-- Appeal outcomes per year
select extract(year from appeal_decision_date)::int as year, appeal_decision, count(*)
from core.applications where appeal_decision_date >= date '2024-01-01'
group by 1, 2 order by 1, 2;

-- Text search on the proposal
select lpa_app_no, description from core.applications
where description ilike '%loft conversion%';

-- Homes (residential units) from the nested list: unverified data, LOCAL ONLY
select r.id, u->>'tenure' as tenure, u->>'no_bedrooms' as bedrooms
from raw.applications r
cross join lateral jsonb_array_elements(
  r.doc->'application_details'->'residential_details'->'residential_units') as u
where r.snapshot_date = (select max(snapshot_date) from raw.applications)
  and r.id in (select id from core.applications)
limit 20;
```
