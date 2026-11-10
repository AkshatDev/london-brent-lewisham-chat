# PLAN: read-only GET endpoints over the Planning London Datahub

Working notes, not documentation. This file is ignored by git, Docker and FastAPI Cloud.
No code has been written for any of this yet.
See §11 for the local database + AI chat bot plan (Brent first).

> **GOLDEN RULE (see CLAUDE.md): scope is the London Boroughs of Brent and Lewisham
> only.** This applies to everything in this plan:
> - every upstream query, including manual `curl`s, counts and aggregations;
> - every response, log field, example, test and fixture.
>
> The GLA documents use other boroughs in their examples. We don't copy them.

## 1. Sources

| Source | What it gives us |
|---|---|
| `planninglondondatahub_public_technical_schemav2.1.xlsx` (local, git-ignored) | Indexes, field names, types, definitions and allowed values ("Field Values" tab) |
| [GLA API connection doc v1 (PDF)](https://www.london.gov.uk/sites/default/files/planninglondondatahub_api_connection_technical_documentation_v1.pdf) | Base URL, access header, how to get one record and how to search |
| [Elasticsearch 7.9 reference](https://www.elastic.co/guide/en/elasticsearch/reference/7.9/search.html) | Query DSL, `_source`, paging, aggregations |

## 2. The upstream API

The **Planning London Datahub (PLD)** is run by the GLA and stored in **Elasticsearch
7.9**. It is exposed read-only to guests.

- **Base URL:** `https://planningdata.london.gov.uk/api-guest/`, with no auth.
- **Required header:** `X-API-AllowRequest: be2rmRnt&`. The value is published in the
  PDF, so it is not a secret, but it is config. It is read through `Settings` from
  `.env` and never hard-coded, logged or typed on the command line.
- **Responses** are raw Elasticsearch JSON.

### Calls we will use

| Purpose | Call | Notes |
|---|---|---|
| One application | `POST applications/_search` with `ids` + scope filter | **What we use**: 0 hits for an out-of-scope id, so its data is never fetched |
| ~~One application~~ | ~~`GET applications/_source/{id}`~~ | Works, but can't carry a filter, so it would fetch out-of-scope records. Not used |
| Search | `POST <index>/_search` + JSON body | A read despite the POST; our endpoints stay GET |
| Count | `POST <index>/_count` + JSON body | Works for guests (~0.15 s) |
| Aggregations | `POST <index>/_search` with `size: 0` + `aggs` | Work for guests (~0.16 s) |
| ~~Field names and types~~ | ~~`GET <index>/_mapping`~~ | **403 for guests**, so field names come from real documents (§10) |
| Index list + field names and types | `GET /_all` or `GET /<index>` (Get index API, linked in the PDF) | **Works for guests** (verified 2026-11-07): returns `aliases`, `mappings` (types, date formats) and `settings` for all 7 indexes, ~26 KB. `_cat/indices` and `_aliases` are 403 |

**Ids** have the form `<LPA name>-<LPA reference>` with **`/` replaced by `_`**
(verified):

| `lpa_app_no` | id |
|---|---|
| `26/0225` | `Brent-26_0225` |
| `DC/25/138731` | `Lewisham-DC_25_138731` |

An unknown id returns **404** with `resource_not_found_exception`.

**Search body** (the doc's example shape, rewritten for our scope):

```json
{
  "query": {
    "bool": {
      "filter": [
        {"terms": {"lpa_name.raw": ["Brent", "Lewisham"]}},
        {"term": {"application_type.raw": "All Other"}},
        {"range": {"valid_date": {"gte": "01/01/2021", "format": "dd/MM/yyyy"}}}
      ]
    }
  },
  "_source": ["id", "lpa_name", "lpa_app_no", "valid_date", "decision_date"],
  "from": 0,
  "size": 20,
  "track_total_hits": true
}
```

- **Exact matches use the `.raw` keyword fields.** The plain fields are analysed text.
- **Range values are `DD/MM/YYYY` strings.** We pass `format` explicitly, so we don't
  depend on the mapping. The doc's own example has a stray leading space in
  `" 01/01/2021"`.
- **Filters go in `bool.filter`** (exact, no scoring, cacheable). Only free text goes in
  `must`.
- **`_source` limits the fields returned.** Documents have ~270 fields.

## 3. The data (from the schema)

Every record has `_id`, `_index` and `_type` (always `_doc`). String fields have a
`.raw` twin.

| Index | One record is | Fields |
|---|---|---|
| `applications` | One planning application, with nested detail under `application_details.*` | ~270 |
| `residential_units` | One residential unit gained or lost (`residential_unit.*`) | 22 + summary |
| `existing_proposed_floorspace_details` | Floorspace by use class: existing, gained and lost GIA | 8 + summary |
| `other_resi_accommodation_unit_details` | Non-self-contained housing (C2, Sui Generis rooms) | 12 + summary |
| `non_permanent_dwellings_details` | Traveller pitches, houseboat moorings and similar | 7 + summary |
| `open_spaces_details` | Open space lost, gained or changed | 11 + summary |
| `protected_spaces_details` | Nature-conservation spaces | 9 + summary |

The six child indexes are flattened copies of arrays inside `applications`. Each one
repeats an application summary (`application_details.id`, `.lpa_name`, `.borough`,
`.status`, `.decision`, `.decision_date`...). That means they can be filtered by borough
directly and joined back with `application_details.id`.

### Fields we care about

- **Identity:** `id` (= `_id`); `lpa_app_no`, the council's own reference.
- **Where:**
  - `lpa_name` and `borough` (both are scope checks; see §4);
  - `ward`, `postcode`, `street_name`, `site_name`, `uprn`;
  - `centroid` (lat/lon), `centroid_easting`/`_northing` (OSGB), `wgs84_polygon`.
- **What:**
  - `description`;
  - `application_type` (Householder / Prior Approval / All Other);
  - `application_type_full`, `development_type`, `application_details.scheme_name`.
- **Outcome and time:**
  - `status` (30 values), `decision` (14 values), `decision_process` (Committee / Delegated);
  - `valid_date`, `decision_date`, `actual_commencement_date`, `actual_completion_date`,
    `lapsed_date`;
  - the appeal fields.
- **Housing:**
  - `application_details.residential_details.*` (affordable %, densities, GIA gained or lost);
  - the `residential_units` index (tenure, bedrooms, unit type, provider).
- **Link out:** `url_planning_app`.

### Quirks to handle

- **Dates** are mostly `DD/MM/YYYY` strings and become `date` in our models. Exceptions
  (verified):
  - `last_updated` and `last_synced` are ISO 8601 timestamps;
  - `phasing_details.*_date` are `MM/YYYY`.
- **Booleans are real JSON `true`/`false`** (verified). The schema's "Y/N" is wrong, so no
  conversion is needed.
- **Index names (verified):** `non_permanent_dwellings_details` and
  `protected_spaces_details`, both with underscores. The PDF's hyphen spelling and the
  Field Values spelling give 403. The `residential_units` index keeps its fields under
  `residential_unit.*` plus an `application_details.*` summary.
- **`development_type` has no `.raw`**: it is already a keyword field. Use `term` on
  `development_type` directly.
- **Undocumented fields:** ~40 `application_details.residential_details.total_no_{existing,proposed}_residential_units_<tenure>`
  counts (market for sale, social rent, London affordable rent, shared ownership…). These
  are ready-made housing totals per application, so we don't need to sum units ourselves.
- **Dirty values in Brent/Lewisham** (verified):
  - `status`: `ZZZ`, `lapsed`, `Historical`, `Appeal in Progress` vs `Appeal In Progress`;
  - `decision`: `"\tApproved"` (leading tab), `Not required` vs `Not Required`;
  - `development_type`: `Q29` (7,859 Brent records!), `X`, `Q12`, `Q28`, plus spelling
    variants (`Minor Dwellings`, `Certificates of lawful development`…).

  Filters can't be strict `Literal`s of the Field Values list alone. See §5.
- **Units disagree:** open-space `area` is hectares in `applications` but m² in
  `open_spaces_details`.
- **Superseded components** (`superseded_by_lpa_app_no`, `superseded_date`) are excluded
  when counting units, or totals double-count.

## 4. Enforcing the golden rule

One small module, `scope.py`, does one job. Every other module goes through it.

**Decision (2026-11-06): scope is by *location*.** Everything inside the bounds of
Brent and Lewisham is in scope, whoever decided it.

That includes the 158 applications decided by the Old Oak and Park Royal Development
Corporation (OPDC) and the 2 decided by Hammersmith & Fulham whose sites are in Brent.
OPDC or Hammersmith & Fulham applications located in Ealing or Hammersmith & Fulham are
**out of scope**.

### The scope rule

A record is in scope when:

> `borough ∈ {Brent, Lewisham}`, **or** (`borough` is missing **and**
> `lpa_name ∈ {Brent, Lewisham}`)

In other words, location wins when it's known, and the deciding council is only the
fallback when the location is missing. As Elasticsearch:

```json
{"bool": {"should": [
  {"terms": {"borough.raw": ["Brent", "Lewisham"]}},
  {"bool": {"filter": [{"terms": {"lpa_name.raw": ["Brent", "Lewisham"]}}],
            "must_not": [{"exists": {"field": "borough.raw"}}]}}
], "minimum_should_match": 1}}
```

With `borough=brent` or `borough=lewisham`, the same clause is built with just that one
value.

**Verified 2026-11-06:**

| Decided by | Located in | Records |
|---|---|---|
| Brent | Brent / missing | 36,386 / 407 |
| Lewisham | Lewisham / missing | 28,538 / 452 |
| OPDC | Brent | 158 |
| Hammersmith & Fulham | Brent | 2 |
| **Total** | | **65,943** |

- **0** records are decided by Brent or Lewisham but located elsewhere, so the fallback
  never lets in a site outside the boroughs.
- `borough` alone would lose 859 of the boroughs' own records, which is why the fallback
  is needed.

### `scope.py`

One small module, `scope.py`, does one job. Every other module goes through it.

- **`Borough = Literal["brent", "lewisham"]`** is fixed in code, not a setting. It maps to
  the upstream values `"Brent"` and `"Lewisham"` (spelling verified).
- **`scope_filter(boroughs)`** returns the clause above. The query builder always adds it
  to `bool.filter`, and no code path builds a query without it. User input never becomes
  raw query DSL:
  - free text goes through a `match` query, never `query_string`;
  - other params become `term`/`range` clauses on fixed field names.
- **`in_scope(doc)`** applies the same rule in Python to every record before it is
  returned. Out-of-scope records are dropped and logged as a warning (id only).
- **By-id lookups** use `_search` with an `ids` query **plus `scope_filter`**, not
  `_source/{id}`. An id outside the boroughs therefore never returns data, upstream or
  ours: zero hits gives our 404. There's no id-prefix check, because in-scope ids can
  start with `Brent-`, `Lewisham-`, `OPDC-` (e.g. `OPDC-21_0044_DELBRE`) or
  `Hammersmith & Fulham-`.
- **Child indexes** use the same rule on `application_details.borough.raw` /
  `application_details.lpa_name.raw`. Verified: a `residential_units` search by
  `application_details.id` returns that application's units.

### Showing both in responses

Every item carries both values, so a caller can tell a council decision from an OPDC one:
- **`borough`:** where the site is (`"brent"` / `"lewisham"`). When the upstream
  `borough` is missing, it is derived from the deciding council.
- **`decided_by`:** who decided it (`"Brent"`, `"Lewisham"`, `"OPDC"`,
  `"Hammersmith & Fulham"`).

A `decided_by` query filter can come later if it's needed. The OPDC-in-Brent records
have an empty `ward`.

## 5. Proposed endpoints (all GET, under `/v1`)

| Endpoint | Purpose | Upstream |
|---|---|---|
| `GET /v1/applications` | Search and list applications | `applications/_search` |
| `GET /v1/applications/{id}` | One application in detail | `applications/_search` (`ids` + scope filter) |
| `GET /v1/applications/{id}/residential-units` | Unit breakdown for one scheme | `residential_units/_search` |
| `GET /v1/boroughs/{borough}/summary` *(later)* | Counts by status, decision or development type over a date range | `applications/_search` with aggregations |

Query parameters for `/v1/applications`, validated with `Annotated[..., Query(...)]`:

- **`borough`:** `Borough | None`. When it's omitted we search both boroughs, never more.
- **`status`, `decision`, `development_type`, `application_type`:** `Literal`s of the
  **clean** values, so a bad value gets a 422. Each clean value maps to the dirty variants
  that really occur (e.g. `Approved` → `["Approved", "\tApproved"]`), sent as a `terms`
  query. Unmapped junk (`ZZZ`, `Q29`) is returned as-is in responses but isn't offered
  as a filter.
- **`decided_from`, `decided_to`, `valid_from`, `valid_to`:** `date`.
  `from > to` gets a 422.
- **`ward`:** exact filter (92–98% filled). **No `postcode` filter**: the field is only
  10–19% filled, and the address usually sits in `site_name`.
- **`q`:** free text over `description`, as `match` with `operator: and`. The default OR
  makes "loft conversion" match 9,289 records, i.e. anything with "loft" *or* "conversion".
- **`limit`** (1–100, default 20) and **`offset`** (≥ 0), with `offset + limit <= 10000`.
  **Verified:** `from 9990 + size 10` works; `9995 + 10` and `size 10001` get a 400. A
  bad page is therefore a 422 from us, never an upstream 400.

Responses are Pydantic models with attribute docstrings:
- **The list** returns `total`, `limit`, `offset` and `items`. Each item is a **curated
  summary**: id, reference, borough, address, ward, description, types, status,
  decision, key dates, centroid and link.
- **The detail** adds housing, floorspace and parking numbers.
- **Never the raw ~270-field document.**

## 6. How it fits the existing code

- **`config.py`:** adds three settings.
  - `datahub_base_url`, with default `https://planningdata.london.gov.uk/api-guest/`.
  - `datahub_allow_request: SecretStr`, with no default, so missing config fails fast.
    `SecretStr` keeps the value out of logs and reprs.
  - `datahub_timeout_seconds`.

  Also add the header line to `.env.example` and `.env`, and set it in FastAPI Cloud via
  `--value-stdin`. `tests/conftest.py` passes it explicitly in `Settings(...)`.
- **`scope.py`:** the golden rule (§4).
- **`datahub.py`:** the upstream client. It builds queries (always through `scope.py`),
  calls Elasticsearch and maps hits to our models. It never logs response bodies.
- **`httpx.AsyncClient`** is created in `lifespan` with the base URL, header and timeout.
  It is stored on `app.state` and closed on shutdown, so connections are reused.
- **`schemas.py`:** response models. Validators turn `DD/MM/YYYY` into dates and strip
  stray whitespace from values (e.g. `"\tApproved"`).
- **`v1.py`:** an `APIRouter` included with `prefix="/v1"`. `/health` is unchanged and
  never calls the upstream.
- **Errors:**

  | Situation | Our status |
  |---|---|
  | Upstream timeout | 504 |
  | Connection error or upstream 5xx | 502 (503 if the upstream says it's overloaded) |
  | Upstream 404 on `_source`, or an out-of-scope id | 404 |
  | Any other upstream 4xx (we built a bad query, so it's a bug) | 500 |

  Each error is logged once, with the request ID, the upstream status and the duration.

## 7. Testing

- **Unit tests use `httpx.MockTransport`** and never hit the real API. They cover:
  - query building;
  - date parsing and value clean-up;
  - 422 on bad params;
  - the 404/500/502/504 mapping;
  - paging limits.
- **Golden-rule tests:**
  - every built query contains the scope filter, whatever the parameters;
  - a by-id lookup always sends the scope filter with the `ids` query, and zero hits gives
    404;
  - an OPDC record located in Brent **is** returned, with `decided_by = "OPDC"`;
  - an OPDC record located in Ealing (fixture built by hand, not fetched) is dropped by
    `in_scope`;
  - a record with no `borough` is in scope only when `lpa_name` is Brent or Lewisham;
  - an out-of-scope record in an upstream response is dropped;
  - `borough=westminster` (or any other value) returns 422.
- **Fixtures** are trimmed real responses for Brent and Lewisham only.
- **Real server check:** `fastapi run`/Docker + `curl` against the live API.
- **README:** record the measured upstream latency and payload size (with and without
  `_source`).

## 8. Steps (one branch each; the user commits)

1. ✅ **Done 2026-11-05** (findings in §10). **Explore the upstream with manual `curl`s.** No code. Every query carries the
   Brent/Lewisham filter, and the header is read from `.env`:
   - `_mapping` for `applications` and `residential_units` (real field names, date
     formats, `.raw` fields);
   - `_source/{id}` for one Brent and one Lewisham application, which shows the id
     character rules;
   - a `_search` with the scope filter + a `valid_date` range + `_source`;
   - `_count`, and a `terms` aggregation on `status.raw` (both scoped). Do guests have
     access?
   - the `borough` values on Brent/Lewisham records, and whether OPDC records in Brent
     carry `borough = Brent`.

   Save trimmed samples as fixtures, answer §9 and update this plan.
2. **Settings + `httpx` client in `lifespan` + `scope.py`**, with tests. No endpoint yet.
3. **`GET /v1/applications/{id}`**, with the response model and error mapping.
4. **`GET /v1/applications`**, with filters and paging.
5. **`GET /v1/applications/{id}/residential-units`.**
6. *(Later)* **Borough summary** via aggregations; caching if latency needs it.

Each step also updates `commands.md` (new curls) and the README (decisions + measurements).

## 9. Open questions

1. **Which questions should the API answer first?** This sets the curated fields.
   *Decision for the user.*
2. Latency: by-id via `_search` measured 0.77–0.92 s on 2026-11-06, vs 0.15 s for
   `_source` on 2026-11-05 (different days, maybe a cold cache). *Re-measure both
   side by side in step 3.*
3. Rate limits: none hit in ~25 calls; no documented limit. *Watch in logs.*
4. Licence and attribution wording to show in `/docs` and the README. *To find on the
   GLA site.*

Decided: **OPDC and others inside Brent are in scope** (location-based, §4, 2026-11-06).

Answered in step 1:
- the id rule is `/` → `_`;
- the window is 10,000;
- `_count` and aggregations work, `_mapping` doesn't;
- spellings are `Brent` / `Lewisham`.

## 10. Step 1 findings (live API, 2026-11-05, Brent and Lewisham only)

**Coverage**

| | Brent | Lewisham |
|---|---|---|
| Applications | 36,781 | 28,980 |
| Records from | 2018 (a handful earlier) | 2019 (a handful earlier) |
| Per year | ~3,000–4,650 | ~2,400–4,200 |
| Householder / Prior Approval / All Other | 9,520 / 4,088 / 23,173 | 6,615 / 1,597 / 20,768 |
| Approved / Refused / Withdrawn (decision) | 26,102 / 8,223 / 1,413 | 20,704 / 4,425 / 939 |
| Completed / Commenced (status) | 3,593 / 194 | 3,698 / 139 |
| Major dwellings | 155 | 163 |
| Minor dwellings | 2,364 | 1,269 (+13 `Minor Dwellings`) |

**Child indexes** (scoped on `application_details.lpa_name.raw`, both boroughs):

| Index | Records |
|---|---|
| `residential_units` | 200,908 |
| `existing_proposed_floorspace_details` | 21,308 |
| `other_resi_accommodation_unit_details` | 1,505 |
| `open_spaces_details` | 226 |
| `protected_spaces_details` | 88 |
| `non_permanent_dwellings_details` | 19 |

**How full the fields are** (% of records with a value, Brent / Lewisham):

| Field | Brent | Lewisham |
|---|---|---|
| `centroid` | 100% | 100% |
| `site_name` | 99% | 99% |
| `description` | 98% | 95% |
| `ward` | 98% | 92% |
| `decision_date` | 97% | 94% |
| `uprn` | 95% | 76% |
| `url_planning_app` | 89% | 80% |
| housing totals | 63% | 66% |
| `wgs84_polygon` | 59% | 76% |
| `actual_commencement_date` | 10% | 13% |
| `actual_completion_date` | 9% | 12% |
| `postcode` | 10% | 19% |
| `street_name` | 9% | 15% |
| `lead_developer_company_name` | 1% | 1% |

**Measurements** (for the README):

| Call | Size | Time |
|---|---|---|
| `_source/{id}`, major scheme `Brent-26_0225` | 34.5 KB | 0.15 s |
| `_source/{id}`, `Lewisham-DC_25_138731` | 8.8 KB | 0.15 s |
| 20 newest applications, full documents | 60 KB | 0.23 s |
| 20 newest applications, 15-field `_source` | 17 KB | 0.16 s |

`_source` filtering cuts the payload by **71%**.

**Example records:**
- **`Brent-26_0225`:** NHS site, Wembley Central. Approved by committee 18/09/2026.
  108 market-sale flats (0% affordable), 3 blocks up to 6 storeys. Car spaces 35 → 12.
  Developer Fairview New Homes.
- **`Lewisham-DC_25_138731`:** 164–196 Trundley's Road, SE8. A Section 73 amendment
  (a change to an existing permission). Recorded as Major dwellings, but with 0 units on
  this record: **a major-dwellings application doesn't always carry units itself.**

**Samples** are saved locally in the session scratchpad. When the test step starts,
trimmed copies go to `tests/fixtures/`.

## 11. Local data store + AI chat bot (Brent first) — planned 2026-11-07

Goal: all Brent data from this API in a local database, with an AI chat bot that
answers questions over it accurately. Lewisham later, through the same pipeline.

### What the data is (verified on the full download, 2026-11-06/07)

- **Everything is in the `applications` index.** Each application is one JSON
  document; related data is **nested lists inside it**, not separate tables joined by
  keys. The six child indexes (`residential_units`, ...) are flattened copies of those
  lists, so they add nothing new.
- Full Brent download: 36,953 applications (= API count), 312 MB JSONL, **26 MB
  gzipped**; most of the size is site polygons, stored twice (OSGB + WGS84).
  ~37 requests of 1,000 with `search_after`; no limit hit.
- **Use JSON, not CSV.** The SQL endpoint (`_sql?format=csv`, `SELECT *`) gives 211
  flat columns and silently drops every list. No Parquet/bulk export in the API; we
  make our own Parquet from the JSON if needed. A London-wide bulk file elsewhere
  would break the golden rule (other boroughs stored); the API filters server-side.
- Field list (union over all records, with value types): `findings/application_fields.txt`.
  Three CSV columns never appear in Brent's JSON: `is_pre_app`, `pp_retry_expiry`,
  `pp_update_required`.
- **Current state only.** Status/decision are overwritten in place; there is no
  history in the API. Dated snapshots on our side are the only way to answer
  "what was the state on date X".

Nested lists → child tables (items in the Brent download):

| List | Items | Child table |
|---|---|---|
| `residential_details.residential_units[]` | 115,595 | `residential_units` |
| `constraints_details[]` | 10,707 | `constraints` |
| `existing_proposed_floorspace_details[]` | 10,691 | `floorspace` |
| `building_details[]` | 6,905 | `buildings` |
| `phasing_details[]` | 5,178 | `phases` |
| `other_residential_accommodation_details.other_resi_accommodation_unit_details[]` | 1,189 | `other_resi_units` |
| `superseding_details[]` | 756 | `supersedes` (application → application) |
| `infrastructure_details.waste_details[]` | 362 | `waste` |
| `open_and_protected_space_details.open_spaces_details[]` | 148 | `open_spaces` |
| `open_and_protected_space_details.protected_spaces_details[]` | 55 | `protected_spaces` |
| `non_permanent_dwellings_details[]` | 12 | `non_permanent_dwellings` |
| `title_number[]`, `existing_uprns[]`, `householder_dev_category[]`, `decision_conditions[]` | — | small lists of plain values (array columns) |

### Approach: hybrid, SQL first

Most questions are counts, sums and filters ("how many homes approved in Wembley in
2025?"). Embeddings find similar text but can't add things up, so:

- **Structured questions** → the bot writes SQL against a documented semantic layer.
  This is the majority and where accuracy is won.
- **Fuzzy questions** ("loft conversions near a school", a half-remembered scheme
  name) → hybrid text search (embeddings + keywords) that returns application ids;
  the facts then come from SQL. The bot never states numbers from embedded text.

### Layers (one Postgres container: PostGIS + pgvector, all local)

```
API (_search, scope filter, search_after)
  │
  ▼
1. raw/             dated JSONL.gz snapshots (immutable, replayable; our history)
  ▼
2. raw schema       raw.applications(id, doc jsonb, snapshot_date)
  │                 scope re-checked on load; anything outside Brent is rejected
  ▼
3. core schema      typed, cleaned tables: applications + the child tables above
  │                 dates as date, numbers numeric, geometry, '' → NULL,
  │                 clean status/decision/ward next to the raw values, quality flags
  ▼
4. semantic schema  the only views the bot sees: plain names, decoded codes,
  │                 agreed definitions
  ▼
5. search           one text per application → embedding (pgvector)
                    + full-text (tsvector) + trigram on addresses
```

- **Decided 2026-11-10:** only the cleaned database is hosted; the raw snapshots stay
  local. They are the only history (the API overwrites records) and the only way to
  rebuild after a rule change or to get back a field that is not loaded. Load-time rules,
  derived columns and fields not loaded are in `semantic/brent/cleaning.yaml`.
- Cleaning rules in layer 3 can change and be re-run from layer 2 without refetching.
- Incremental refresh after the first full load: pull records with `last_updated` ≥
  last run (*check first that `last_updated` is reliable*).
- Later: raw snapshots to a cloud bucket and a managed Postgres with PostGIS +
  pgvector (*provider: decision for the user*).

### Semantic layer (layer 4): definitions to agree before building

Written once as views plus a short YAML/Markdown description the bot reads.

- **"Approved"**: which `decision` values count (Approved, Granted, Prior approval
  not required, ...).
- **"Homes delivered"** = completed units (`actual_completion_date` set), not
  approved units.
- **"Net new homes"** = proposed − existing.
- **"Affordable"**: which tenures count.
- **Default date for "in 2025"**: `valid_date` or `decision_date`; pick one and say it.
- **Superseded applications** are left out, so homes are not counted twice.
- **Wards**: 2022 boundaries vs pre-2022 names (both appear in the data).
- **Quality flags** (placeholder dates/centroids, `ZZZ`, dates out of order) are
  excluded by default.
- Per column: description, allowed values (e.g. 22 raw statuses → clean set), units
  (m², ha, £), synonyms ("extension" ≈ householder, "flats" ≈ residential units).

### Embeddings (layer 5)

- One short document per application: description, type, address, ward,
  decision/status, year, unit counts, scheme name. Descriptions are short, so no
  chunking; ~37k vectors.
- Every vector keeps the application `id`.
- Hybrid with keyword search: application numbers, "HMO", street names match better
  on keywords than on meaning.
- *Model (local vs API): decision for the user.*

### Bot tools (few, narrow)

1. `query_sql`: read-only role that sees only the `semantic` views; row limit and
   timeout.
2. `search_applications(text, filters)`: hybrid search, returns ids.
3. `get_application(id)`: full record plus its child rows.
4. `find_place(name)`: ward / street / postcode, or a point and radius (PostGIS).

### Measuring accuracy

Before tuning anything: 30–50 real questions with correct answers worked out by hand
in SQL. Re-run after every change to model, views or prompt. This set *is* the
accuracy measure.

### Golden rule in this pipeline

- Extract: every query carries the scope filter (§4).
- Load: loader rejects out-of-scope documents; a CHECK constraint on the scope
  columns in the database.
- The bot can't widen scope: it only sees Brent data (two allowed values once
  Lewisham is added).
- External sources (ward/borough boundaries, postcodes, census) are **not** added
  without asking, even if about Brent only.

### Order of work

1. Docker: Postgres + PostGIS + pgvector.
2. Extract script → raw snapshots → `raw.applications`. *Can start now.*
3. `core` tables + cleaning rules. *Waits for the user's EDA* (data-quality findings
   so far: placeholder dates/centroids, 22 status variants, `'\tApproved'`, two ward
   boundary sets, mixed number/string types, `Q29` is a catch-all).
4. Semantic views, definitions document, question test set.
5. Embeddings and search.
6. The bot.

### Open questions for this section

1. The 3 records with `borough = Brent` but non-Brent wards
   (`Hammersmith_&_Fulham-2025_01931_FUL`, `Hammersmith_&_Fulham-2025_01998_PMA56`
   ward `COLLP`; `OPDC-18_0210_FULOPDC` ward `East Acton`): exclude, keep, or check
   against an official boundary (external data)? *Decision for the user.*
2. Source of `All Valid Applications 06_04_2026-05_04_2027.csv` in the project root
   (19 columns, 1,653 Brent rows; looks like a Datahub website export).
3. Cloud provider; embedding model; LLM for the bot.
4. How this relates to the GET endpoints in §5–§8: the bot could query the local
   database instead of the live API. *Decision for the user.*
