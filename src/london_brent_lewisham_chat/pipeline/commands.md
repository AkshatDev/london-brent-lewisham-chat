# Pipeline commands cheat sheet

The chat bot is live-API only: it never reads a local copy of the data. The pipeline
only downloads a raw snapshot for the EDA notebooks (`findings/`). Run everything from
the project root (`london-brent-lewisham-chat/`).

- **Golden rule:** Brent and Lewisham only. The pipeline's borough argument accepts
  nothing else, and every download carries the borough filter.
- **Secrets** (`DATAHUB_ALLOW_REQUEST`) live in `.env` only. Never type them on the
  command line.
- **Local only:** `data/` (snapshots) is git-, docker- and deploy-ignored.

## What goes where

```
Datahub API ──extract──▶ data/raw/<borough>/applications_<date>.jsonl.gz
```

`clean.py` holds `clean_record`: the chat tools clean every live record with it and the
borough's `semantic/<borough>/cleaning.yaml` before the model sees it.

## Download a snapshot

```bash
cp .env.example .env              # first time: then set DATAHUB_ALLOW_REQUEST
uv sync
uv run python -m london_brent_lewisham_chat.pipeline extract brent      # ~20 s
uv run python -m london_brent_lewisham_chat.pipeline extract lewisham
```

`extract` fails if the number downloaded differs from the API's `_count`.

## Checks before committing pipeline changes

```bash
uv run ruff format . && uv run ruff check . && uv run pytest -q
```
