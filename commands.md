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

curl -i localhost:8000/v1/chat/status    # 200 {"status":"ok","model":...} if OpenAI can be used
OPENAI_API_KEY= uv run fastapi dev       # run without a key: chat/status answers 503 "not_configured"
```

Chat status: `200 ok`; `503 not_configured` (no `OPENAI_API_KEY`); `503 unavailable`
(OpenAI down, rate-limited, or key/model rejected: the log line says which); `504`
(no answer within `OPENAI_TIMEOUT_S`). It costs no tokens. The `app started` line shows
`openai_configured` and `openai_model`.

Logfire: traces are sent only when `LOGFIRE_TOKEN` is set in `.env` (write token from
the Logfire project: Settings → Write tokens). Edit `.env` in an editor; never paste the
token on the command line. Tests never send (`tests/conftest.py` hides `.env` from them).
Traces are tagged with `ENVIRONMENT` (`local` unless set); the first app log line,
`app started`, shows it and whether traces are being sent (`sending_to_logfire`).

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

# Tag the deployed app as production (not a secret; the default is local):
uv run fastapi cloud env set ENVIRONMENT production
uv run fastapi cloud logs --no-follow --since 30m | grep '"app started"'   # check: "environment": "production"

# Set secrets from .env via stdin (never on the command line): the Logfire token, the OpenAI key
grep '^LOGFIRE_TOKEN=' .env | cut -d= -f2- \
  | uv run fastapi cloud env set LOGFIRE_TOKEN --value-stdin --secret --no-redeploy
grep '^OPENAI_API_KEY=' .env | cut -d= -f2- \
  | uv run fastapi cloud env set OPENAI_API_KEY --value-stdin --secret
curl -s https://<app-url>/v1/chat/status                  # check: {"status":"ok",...}
```

`env set` redeploys the app by default; add `--no-redeploy` to set several variables
first and deploy once.

`fastapi deploy` uploads the current files, so check `git branch --show-current` and
`git status` first. `.fastapicloudignore` keeps `.env*`, `tests/` and `docs/` out.

## Data pipeline (raw snapshots for the EDA notebooks)

The bot is live-API only; snapshots are for analysis. Details:
[`src/london_brent_lewisham_chat/pipeline/commands.md`](src/london_brent_lewisham_chat/pipeline/commands.md).

```bash
uv run python -m london_brent_lewisham_chat.pipeline extract brent      # today's raw snapshot
uv run python -m london_brent_lewisham_chat.pipeline extract lewisham
```
