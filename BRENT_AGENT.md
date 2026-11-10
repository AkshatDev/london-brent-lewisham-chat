# Brent · agent handover

Where the Brent work stands, how it was done, and where every file is. Brent only;
Lewisham will be done the same way later, with its own files. State as of 2026-11-10.

Read `CLAUDE.md` first: the golden rule and the working rules there apply to everything here.

## 1. Goal

Build a local, cleaned database of Brent planning applications and an AI chat bot that
answers questions about them correctly. The work so far is the **EDA of the 16 columns
the Datahub website shows**, turned into:

- **semantic YAMLs**: what each field means after cleaning, for the bot;
- **one cleaning file**: every load-time rule, so the EDA never has to be redone;
- **knowledge notes and an open-questions log**, for people.

No loader, database or bot code exists yet.

## 2. Rules that must not be broken

- **Golden rule:** scope is Brent (and later Lewisham) only, by location: `borough` is
  Brent, or `borough` is missing and `lpa_name` is Brent. Every query carries this filter.
  Any task that needs data from elsewhere, or external data (boundaries, Brent's portal,
  postcode lookups): stop and ask the user.
- **Secrets** only in `.env` (`DATAHUB_ALLOW_REQUEST`). Read with `dotenv_values`; never
  print it or put it on a command line.
- **The user commits.** Never run `git commit`, push, merge or rebase; suggest a message.
- **Small steps; do only what is asked.** Workflow per column: add a step to the notebook →
  run it → write findings → discuss → only after agreement update YAML / knowledge.
- **Don't guess.** Schema definitions are copied verbatim. Anything not measured is
  marked "not checked". Facts from outside the data (e.g. planning law) are not written as
  facts about the data.

## 3. The data source

- GLA Planning London Datahub: Elasticsearch 7.9 behind a guest gateway,
  `https://planningdata.london.gov.uk/api-guest`, header `X-API-AllowRequest`.
- `POST /applications/_search` and `/_count` work; `GET /applications` returns the mapping.
  More than 10,000 records need `search_after`, sorted by `id`.
- Exact matches use the `.raw` keyword fields; `description` has no `.raw`.
- Brent: 36,952 applications (live API, 2026-11-10); 36,538 after cleaning.
- Records are overwritten in place; the API has no history.
- Official field definitions and value lists:
  `planninglondondatahub_public_technical_schemav2.1.xlsx` (sheets `applications`,
  `Field Values`).

## 4. Decisions taken

| Decision | Where it is recorded |
|---|---|
| Only the cleaned database is hosted; raw JSONL snapshots stay local (our only history) | `PLAN.md` §11 |
| Cleaning rules live in one file, not in the field YAMLs | `semantic/brent/cleaning.yaml` |
| Field YAMLs describe the data **after** cleaning; no coverage, no value counts, no notebook links | `semantic/brent/*.yaml` |
| Drop the 407 empty records, the 3 test records and the 4 OPDC duplicate copies | `cleaning.yaml` |
| The 5 address fields are combined into one `address` column and not loaded themselves | `cleaning.yaml` (`derived`, `not_loaded`), `address.yaml` |
| `legacy_record` flag derived from `bo_system` | `cleaning.yaml`, `legacy_record.yaml` |
| `application_type_full` is not used (keep it simple) | `findings/brent/application_type.ipynb` Step 3 |
| `borough` = Brent is taken as Brent; location checks deferred | `knowledge/brent_open_questions.md` (Deferred 10, 11) |

## 5. Files

### Notebooks: `findings/brent/` (one per web column, live API)

Each starts with the same setup cell (`brent_filter()`, `search()`), then numbered steps,
each followed by a "Findings" markdown cell. Open them in Jupyter with the project kernel,
or run one from the project root with:

```bash
uv run --with nbclient --with nbformat python -c "
import nbformat
from nbclient import NotebookClient
f = 'findings/brent/status.ipynb'
nb = nbformat.read(f, as_version=4)
NotebookClient(nb, timeout=900, kernel_name='python3', resources={'metadata': {'path': 'findings/brent'}}).execute()
nbformat.write(nb, f)
"
```

| Notebook | Done | YAML |
|---|---|---|
| `lpa_app_no.ipynb` | nulls, type, duplicates, Brent/OPDC pairs | `lpa_app_no.yaml` |
| `borough.ipynb` | nulls, type, values | `borough.yaml` |
| `valid_date.ipynb` | nulls/dates, overlaps, legacy records | `valid_date.yaml` |
| `status.ipynb` | values, ZZZ, empty status, duplicates, type | `status.yaml` |
| `decision.ipynb` | values, gaps, decision without date, type | `decision.yaml` |
| `decision_date.ipynb` | nulls/dates, 1987 legacy dates, order vs valid date | `decision_date.yaml` |
| `application_type.ipynb` | values, Prior Approval decisions, `application_type_full` (skipped), type | `application_type.yaml` |
| `description.ipynb` | nulls, type, common values, overlap with empty records | `description.yaml` |
| `site_name.ipynb` | nulls, gaps, common values, type, how complete as an address | `address.yaml` |
| `site_number.ipynb` | nulls, common values, mixed JSON types | `address.yaml` |
| `street_name.ipynb`, `locality.ipynb`, `postcode.ipynb` | nulls, type, common values | `address.yaml` |
| `decision_target_date.ipynb` | nulls/dates, overlaps, overdue, type, values, gap to valid date | `decision_target_date.yaml` |
| `appeal_decision.ipynb` | nulls, mismatch, type, values, vs `status` | `appeal_decision.yaml` |
| `appeal_decision_date.ipynb` | nulls/dates, type, values | `appeal_decision_date.yaml` |
| `url_planning_app.ipynb` | nulls, type, overlap, after-cleaning counts | **not written yet** |

Other files in `findings/`: `01_one_row.ipynb` (guided first look at one record),
`application_fields.txt` (all field paths and types in Brent records), `data/` (local
downloads, git-ignored).

### Semantic YAMLs: `semantic/brent/` (git-ignored)

- `cleaning.yaml`: load-time rules, in order: `all_records` (strip whitespace; drop test,
  empty and OPDC-duplicate records) → `fields` (status case fixes, empty status → Unknown,
  approximate 1987 decision dates, `site_number` as text) → `derived` (`address`,
  `address_kind`, `legacy_record`) → `not_loaded` (the 5 raw address fields).
- One file per bot-facing field: `lpa_app_no`, `borough`, `valid_date`, `status`,
  `decision`, `decision_date`, `application_type`, `description`, `address`,
  `decision_target_date`, `appeal_decision`, `appeal_decision_date`, `legacy_record`.
- Common shape: `field`, `web_label`, `meaning`, `schema_definition` (verbatim), `format`,
  `values` / `values_not_in_schema` / `schema_values_never_seen` (fixed-value fields),
  `checks_passed`, `known_issues` (id, count, description, optional `hint` = not
  confirmed), `rules` (for the bot), `checked` (date, source).

### Knowledge: `knowledge/` (git-ignored)

- `brent.md`: the 52 fields, verbatim schema definitions, how applications relate to each
  other, the field → YAML → notebook table (§4), groups of records (§5).
- `brent_open_questions.md`: open questions, "Deferred" (10, 11) and "Answered" (1, 7, 12).

### Plan: `PLAN.md` §11 (git-ignored)

The pipeline (raw snapshots → typed tables → bot views → search), embeddings, bot tools,
accuracy test set, and the hosting decision.

## 6. What is left

- **`url_planning_app`:** most common values (do all URLs point to Brent's planning
  portal, one format?), then `url_planning_app.yaml`.
- **Decisions waiting for the user:**
  - `No description provided by LDMS` (227) → null at load, or keep?
  - A general "blank text → null" rule (e.g. 2 blank `site_name` become `""` after strip)?
- **Open questions** still open: 2 (ZZZ), 3 (infer missing decision), 4 (2 contradictions),
  5 (stale statuses, 39 + 1, and the Brent/OPDC status conflict), 6 (are all `55/` test
  records), 8 (same-day 1987 appeal dates), 9 (11 Unknown-status records with content),
  13 (ZZZ `lapsed_date`).
- **Not started:** the loader, the database, the bot, the test question set (PLAN.md §11),
  and the nested lists (residential units, floorspace, etc.).
- **Lewisham:** the same process, with `findings/lewisham/`, `semantic/lewisham/`,
  `knowledge/lewisham.md`.
