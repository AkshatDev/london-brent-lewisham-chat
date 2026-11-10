# Pipeline commands cheat sheet

How to bring the Brent data into the local PostGIS, keep it up to date, and look after
the database. Run everything from the project root (`london-brent-lewisham-chat/`).

- **Golden rule:** Brent and Lewisham only. The pipeline's borough argument accepts
  nothing else, and every download carries the borough filter.
- **Secrets** (`DATAHUB_ALLOW_REQUEST`, `POSTGRES_*`) live in `.env` only. The commands
  below read them from there or from inside the container. Never type them on the
  command line.
- **Local only:** `data/` (snapshots, boundaries, backups) is git-, docker- and
  deploy-ignored.

## What goes where

```
Datahub API ──extract──▶ data/raw/brent/applications_<date>.jsonl.gz   (our only history)
postcodes.io ─postcodes─▶ data/external/brent/postcode_centroids.json  (Brent postcodes only)
ONS (manual) ───────────▶ data/external/brent/*.geojson                (Brent boundary, wards)
                  │
                 load   (rules: semantic/brent/cleaning.yaml)
                  ▼
PostGIS: raw.applications (JSONB, every snapshot) · ref.boroughs, ref.wards · core.applications (cleaned, rebuilt each load)
                  │
                 push   (core.applications only; URL from NEON_DATABASE_URL in .env)
                  ▼
Neon (hosted, for the bot): core.applications
```

## First time on a new machine

```bash
cp .env.example .env              # then set DATAHUB_ALLOW_REQUEST and a POSTGRES_PASSWORD
uv sync                           # Python dependencies
docker compose up -d --wait       # start PostGIS: 127.0.0.1:5433, volume <project>_pgdata
```

Then fetch the ONS boundaries once (see below), and run the full refresh.

## Full refresh (new data from the Datahub)

```bash
docker compose up -d --wait                                            # make sure the DB is up
uv run python -m london_brent_lewisham_chat.pipeline extract brent     # ~20 s: today's raw snapshot
uv run python -m london_brent_lewisham_chat.pipeline postcodes brent   # placeholder centroids -> postcodes.io
uv run python -m london_brent_lewisham_chat.pipeline load brent        # ~10 s: newest snapshot -> PostGIS
```

The last log line, `load finished`, gives the counts loaded, dropped and changed per rule.

`extract` fails if the number downloaded differs from the API's `_count`. `load` runs
in one transaction, so a failure leaves the previous data as it was. It stops on:

- a rule's `expected_count` not matching, e.g. the Datahub fixed a record we correct
  by hand (look, then update `cleaning.yaml`);
- a new `55/` reference (a new test record?);
- a centroid more than 20 m outside Brent.

## Reload after changing `cleaning.yaml` (no download)

```bash
uv run python -m london_brent_lewisham_chat.pipeline load brent        # rebuilds core from the newest snapshot
```

Add a portal-checked outcome as one line under `stale_outcome_from_portal` (or
`outcome_from_portal`) in `semantic/brent/cleaning.yaml`, raise its `expected_count`,
then reload.

## Push the bot's tables to Neon (hosted database)

All cleaning stays local; `push` copies only the finished table, `core.applications`,
for one borough. `raw` and `ref` stay local (the bot doesn't need boundary shapes yet:
`ward_2022` and `location_check` are on every row).

1. In the Neon console, copy the **direct** connection string (not the `-pooler` one),
   with `sslmode=require`, and put it in `.env` (never on the command line):

   ```
   NEON_DATABASE_URL=postgresql://<user>:<password>@<host>/<db>?sslmode=require
   ```

2. Refresh locally if needed (`extract`, `postcodes`, `load`), then push:

   ```bash
   uv run python -m london_brent_lewisham_chat.pipeline push brent
   ```

What it does, in one transaction on Neon (a failure leaves Neon as it was):
- creates the `postgis` extension and the `core` schema if missing;
- creates the table from the same definition as locally; if Neon's table differs
  (the local schema changed), drops and recreates it (logged as a warning);
- replaces the borough's rows (delete, then COPY from local) and checks the row
  counts match;
- then runs `ANALYZE`. The last log line, `push finished`, gives the rows pushed and
  whether the table was recreated.

Check it on Neon with any SQL client, e.g. `select count(*) from core.applications;`
(36,531 for Brent on the 2026-11-10 data). A recreated table loses other boroughs'
rows: push each borough again after a schema change. To push more tables later, add
them to `PUSHED_TABLES` in `pipeline/schema.py`.

## Refresh the ONS boundaries (rarely; ward changes)

Brent only: every query filters on Brent's ONS code (`E09000005`) on the server.

```bash
B='https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services'
q(){ curl -sfG "$B/$1/FeatureServer/0/query" --data-urlencode "where=$2" \
       --data-urlencode 'outFields=*' --data-urlencode 'outSR=4326' --data-urlencode 'f=geojson' \
       -o "data/external/brent/$3"; }
q Local_Authority_Districts_December_2024_Boundaries_UK_BFC "LAD24CD='E09000005'" brent_boundary_2024.geojson
q Wards_December_2024_Boundaries_UK_BFC                     "LAD24CD='E09000005'" brent_wards_2024.geojson
# 2021 wards have no district code: fetch Brent's 21 ward codes, E05000085 to E05000105
q Wards_December_2021_GB_BFC_2022 "WD21CD >= 'E05000085' AND WD21CD <= 'E05000105'" brent_wards_2021.geojson
```

`load` checks that the outline is `E09000005` alone and that every ward lies inside it.

## Look at the data

```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'     # SQL shell (\q to quit)

# one query, no shell
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select count(*) from core.applications"'

# already inside the container (docker compose exec db bash)? Plain `psql` fails with
# 'role "root" does not exist': it logs in as the shell user. Name the user and database:
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

Useful queries inside the shell:

```sql
\dt raw.* | \dt core.* | \dt ref.*                      -- tables per schema
\d core.applications                                     -- columns
select snapshot_date, count(*) from raw.applications group by 1 order by 1;   -- snapshots loaded
select location_check, count(*) from core.applications group by 1;           -- location proof
select count(*) filter (where outcome_source <> 'datahub') portal_checked,
       count(*) filter (where status_stale) stale,
       count(*) filter (where appeal_record_deleted) deleted_appeal
from core.applications;                                                      -- flags
select doc from raw.applications where id = 'Brent-18_0557' order by snapshot_date desc limit 1;  -- original record
```

Desktop clients (QGIS, DBeaver, pgAdmin): host `127.0.0.1`, port `5433`, user, password
and database from `.env`. The port listens on this machine only.

## Run the database

```bash
docker compose ps                 # STATUS shows (healthy)
docker compose logs -f db         # Postgres logs (Ctrl+C to stop following)
docker compose stop               # stop; data stays in the volume
docker compose start              # start again
docker compose down               # remove the container; data stays in the volume
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select postgis_version()"'
docker volume ls | grep pgdata    # the volume holding the data
```

## Back up and restore

A backup is a custom-format `pg_dump` (~32 MB for Brent) in `data/backups/` (git-ignored).

```bash
mkdir -p data/backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "data/backups/lblchat_$(date +%F).dump"

# check a backup restores, into a throwaway database
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" restore_test && pg_restore -U "$POSTGRES_USER" -d restore_test --no-owner' \
  < data/backups/lblchat_YYYY-MM-DD.dump
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d restore_test -Atc "select count(*) from core.applications"'
docker compose exec db sh -c 'dropdb -U "$POSTGRES_USER" restore_test'

# restore over the live database (replaces its tables)
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < data/backups/lblchat_YYYY-MM-DD.dump
```

The raw snapshots in `data/raw/` are the real backup: `load` rebuilds everything from
them in seconds. Keep them; the Datahub has no history.

## Start again from nothing (destructive)

Deletes the database volume and everything in it. The snapshots in `data/` are not
touched, so a `load` brings it all back.

```bash
docker compose down -v            # -v deletes the pgdata volume
docker compose up -d --wait
uv run python -m london_brent_lewisham_chat.pipeline load brent
```

## Checks before committing pipeline changes

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
uv run python -m london_brent_lewisham_chat.pipeline load brent   # and read the counts in the last line
```
