# Commands cheat sheet

Run everything from the project root (`london-brent-lewisham-chat/`).
Secrets live in `.env` only: it is git-, docker- and deploy-ignored. Never paste a key
or token on the command line.

## Set up (once)

```bash
cp .env.example .env              # then fill in values
uv sync                           # create .venv with runtime + dev dependencies
```

## Run locally

```bash
uv run fastapi dev                # auto-reload, http://localhost:8000/docs
uv run fastapi run                # production server, no reload

curl -i localhost:8000/health     # {"status":"ok"} + X-Request-ID header
curl -si localhost:8000/health -H "X-Request-ID: $(python3 -c 'import uuid; print(uuid.uuid4())')" | grep -i x-request-id
# a caller's ID is kept only if it is a valid UUID4; anything else is replaced with a new one
LOG_LEVEL=DEBUG uv run fastapi dev                       # more logs
LOGFIRE_CONSOLE=true uv run fastapi dev                  # also print Logfire spans in the terminal
```

Logfire: traces are sent only when `LOGFIRE_TOKEN` is set in `.env` (write token from
the Logfire project: Settings → Write tokens). Edit `.env` in an editor; never paste the
token on the command line. Tests never send (`tests/conftest.py` hides `.env` from them).

## Checks (run before every commit)

```bash
uv run ruff format .              # format
uv run ruff check .               # lint (docstrings, type hints)
uv run ruff check . --fix         # auto-fix what it can
uv run pytest -q                  # all tests
uv run pytest -q tests/test_logging.py -k 500   # one file / matching tests
```

## Dependencies

```bash
uv add <package>                  # runtime dependency
uv add --dev <package>            # dev-only (tests, lint)
uv remove <package>
uv lock --upgrade-package <package>   # upgrade one package
```

After `uv add`, rebuild the Docker image: it installs from `uv.lock`.

## Docker

```bash
docker build -t lblchat .
docker run --rm -p 8000:8000 --env-file .env lblchat          # http://localhost:8000/docs
docker run -d --rm --name lblchat -p 8000:8000 --env-file .env lblchat   # in background

docker ps                                                     # STATUS shows (healthy)
docker inspect --format '{{.State.Health.Status}}' lblchat
docker logs -f lblchat                                        # JSON logs
docker exec -it lblchat sh                                    # shell inside (user: app)
docker stop lblchat
docker images lblchat                                         # image size
```

## Deploy: FastAPI Cloud

```bash
uv run fastapi login                                  # once
uv run fastapi deploy                                 # uploads the working directory
uv run fastapi cloud env list                         # which variables are set
uv run fastapi cloud logs --no-follow --since 30m     # recent logs
uv run fastapi cloud logs                             # stream live

# Set a secret from .env via stdin (never on the command line), e.g. the Logfire token:
grep '^LOGFIRE_TOKEN=' .env | cut -d= -f2- \
  | uv run fastapi cloud env set LOGFIRE_TOKEN --value-stdin --secret
```

`fastapi deploy` uploads the current files, so check `git branch --show-current` and
`git status` first. `.fastapicloudignore` keeps `.env*`, `tests/` and `docs/` out.

## Local database (PostGIS) and data pipeline

Full cheat sheet (refresh, boundaries, queries, backup/restore, reset):
[`src/london_brent_lewisham_chat/pipeline/commands.md`](src/london_brent_lewisham_chat/pipeline/commands.md).

```bash
docker compose up -d --wait                                            # start PostGIS (127.0.0.1:5433)
uv run python -m london_brent_lewisham_chat.pipeline extract brent     # today's raw snapshot
uv run python -m london_brent_lewisham_chat.pipeline postcodes brent   # placeholder centroids
uv run python -m london_brent_lewisham_chat.pipeline load brent        # snapshot -> PostGIS
uv run python -m london_brent_lewisham_chat.pipeline push brent        # core.applications -> NEON_DATABASE_URL (.env)
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'   # SQL shell
```
