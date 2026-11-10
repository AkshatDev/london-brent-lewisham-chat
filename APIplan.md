# APIplan: the chat endpoint (OpenAI) over Brent and Lewisham planning data

My working plan for the **API part only**. Data gathering, ETL (`pipeline/`), EDA
(`findings/`) and the semantic/knowledge files are other people's / other steps' work;
the API reads their output and does not change them. Updated as we go.

`CLAUDE.md` rules apply to everything here, the golden rule first.

## 1. Goal

A FastAPI endpoint where a user asks questions in plain English about planning
applications in Brent (Lewisham later) and gets correct answers from an OpenAI model
that looks the facts up in the cleaned database. The model words the answer; **the
numbers and records always come from the database**, never from the model.

## 2. What exists today (inspected 2026-11-10, branch `feature/api`)

| File | What it gives the API |
|---|---|
| `app.py` | `create_app(settings)`, `lifespan` (configures JSON logging), request-log middleware (one line per request, 500 with `X-Request-ID`), `CorrelationIdMiddleware`, `/health` |
| `config.py` | `Settings` (only `log_level`); `PipelineSettings` is separate so the app needs no DB settings to start |
| `log_config.py` | JSON to stdout, UTC `time`, correlation ID on every record |
| `schemas.py` | `Health` |
| `scope.py` | `Borough = Literal["brent", "lewisham"]`, `borough_name`, `in_scope` (re-check a record), Elasticsearch `scope_filter`/`scoped_query` (pipeline only) |
| `pipeline/` | builds `core.applications` and pushes it to Neon (`push.py`); **not touched by the API** |
| `semantic/database_schema.md`, `semantic/brent/*.yaml` | what each column means and the rules for answers (default filters, flags, citations): the bot's prompt and tools are built from these |
| `tests/` | `conftest.py` (`app`, `client`), health, logging, scope tests |

Data the API will read: **`core.applications` on Neon** (36,531 Brent rows; `CHECK
borough in ('Brent','Lewisham')`). `ref` and `raw` tables are local only and are not
used by the API until a feature needs them (agreed order of work).

Not there yet: `openai` dependency, any `/v1` router, any database access from the app.

## 3. Shape of the solution

```
client ──POST /v1/chat──▶ chat router ──▶ chat loop (llm.py) ──▶ OpenAI (Responses API, tools)
                                              │  ▲
                                   tool calls ▼  │ tool results (JSON)
                                         tools.py ──▶ db.py ──▶ Neon core.applications
                                              (borough filter always added, rows re-checked)
```

- **Tool calling, not free text.** The model gets a few narrow tools (§5). It decides
  which to call; our code runs them with the borough filter built in and returns rows.
- **SQL-first for facts** (as in `PLAN.md` §11): counts, filters and lookups are SQL.
  Semantic/embedding search comes later and only returns ids.
- **Stateless first:** the client sends the conversation so far in each request. No
  server-side sessions to store or expire. (Server-side history is a later option.)
- **Non-streaming first**, then streaming (SSE) as a separate step.

## 4. Golden rule in the API

The prompt is not the guard; the code is.

1. The borough is a request field typed `Borough` → anything else is a 422 before any
   code runs. No config, header or prompt text can add a borough.
2. Every database query is built by one helper that always adds
   `borough = %(borough)s` (the SQL twin of `scope_filter`, added to `scope.py`). No
   other way to build a query exists; the model never writes the `WHERE borough` part.
3. Every row is checked again with `in_scope()` before it goes to the model or the
   client; failures are dropped and logged as a warning.
4. Lookup of an application whose id/reference is outside the boroughs → "not found"
   (404 if exposed as its own endpoint).
5. The system prompt tells the model to refuse questions about other places, and the
   tools have no parameter that could name one.
6. Tests prove 1–4 (§8).

## 5. Tools for the model (first version)

Small and parameterised; values validated by Pydantic before SQL is built.

| Tool | Does | Notes |
|---|---|---|
| `get_application(reference)` | one record by `lpa_app_no` (e.g. `18/1022`) | returns the fields people need + `url_planning_app` |
| `count_applications(filters, group_by?)` | counts with optional grouping (status, decision, ward, year) | applies the default filters from `database_schema.md` (e.g. `not decision_not_issued`, `not status_stale`) |
| `search_applications(filters, text?, limit)` | list matching records | `ILIKE` on `description` / `address`; hard row limit |
| `near(lat, lon, radius_m, filters)` | records near a point | PostGIS `ST_DWithin`; radius capped |

`filters` = a typed object: `status`, `decision`, `appeal_decision`,
`application_type`, `ward_2022`, date ranges on a named date column. Allowed values are
the ones listed in `database_schema.md` (`Literal`s), so the model can't invent one.

A general read-only `query_sql` tool (PLAN.md §11) is **not** in v1: with free SQL we
can't guarantee the borough filter in code. Revisit later with a read-only role,
views and a statement timeout, if the narrow tools prove too limiting.

## 6. Endpoint design (v1)

`POST /v1/chat`

```jsonc
// request
{ "borough": "brent",
  "messages": [ {"role": "user", "content": "How many approvals in Kilburn in 2025?"} ] }
// response
{ "answer": "...",
  "sources": [ {"lpa_app_no": "...", "url_planning_app": "..."} ],
  "model": "...", "snapshot_date": "2026-11-10" }
```

- Limits at the edge: max messages, max characters per message (422 above them).
- Max tool-call rounds per request (e.g. 5) so a confused model can't loop forever.
- Status codes: 422 bad input; 503 DB or OpenAI unavailable / rate-limited; 504 OpenAI
  or DB timeout; 502 malformed OpenAI response; 500 only for our bugs.
- Logging (one request line stays as is) plus one `chat finished` line: model, tool
  calls made, rounds, input/output tokens, duration. **Never** the API key; whether to
  log question text is an open question (§10).

## 7. Code layout (new modules, one job each)

| Module | Job |
|---|---|
| `config.py` | add to `Settings`: `openai_api_key: SecretStr`, `openai_model: str`, `database_url: SecretStr`, timeouts, limits |
| `db.py` | async connection pool (`psycopg_pool`), opened/closed in `lifespan`, stored on `app.state`; read-only transactions, `statement_timeout` |
| `scope.py` | add the SQL scope helper next to the existing ES one |
| `queries.py` | the parameterised SQL for each tool (always through the scope helper) |
| `tools.py` | tool schemas for OpenAI (generated from Pydantic models) + dispatch name → function |
| `llm.py` | OpenAI client (made in `lifespan`, on `app.state`), the tool-call loop |
| `prompt.py` | the system prompt, built from `database_schema.md` rules and `sources.yaml` citation rules |
| `routers/chat.py` | `APIRouter(prefix="/v1")`, `POST /chat` |
| `schemas.py` | `ChatRequest`, `ChatMessage`, `ChatResponse`, `Source` |

`/health` stays dependency-free. A separate readiness check (DB reachable) can be
added if the hosting needs one.

Making OpenAI and the DB injectable (on `app.state`, built from `Settings`) lets tests
swap in fakes without monkeypatching.

## 8. Testing

- **No real OpenAI or Neon calls in `pytest`.** A fake OpenAI client (scripted tool
  calls, then an answer) and either a fake query layer or a local PostGIS fixture.
- Scope tests: query builder always includes the borough filter; `borough` outside the
  allow-list → 422; an out-of-scope row returned by the "DB" is dropped + WARNING
  logged; lookup of an out-of-scope reference → not found.
- Chat loop: tool call → result → answer; max rounds respected; OpenAI timeout → 504,
  rate limit → 503, DB down → 503, each with `X-Request-ID`.
- Edge validation: empty/oversized messages → 422.
- `test_logging.py` style test for the `chat finished` line (no key in any log).
- **Accuracy set** (separate from pytest, needs real model + DB): 30–50 questions with
  answers worked out by hand in SQL (`database_schema.md` example queries are the
  start). Re-run after any change to model, prompt or tools.
- Real-server check each step: `uv run fastapi run` + `curl`, then Docker.

## 9. Steps (small, one at a time; the user commits)

0. **Logfire, local only (done, 2026-11-10):** `observability.py` configures Logfire
   (`send_to_logfire=False`), `instrument_fastapi` gives a span per request, and the
   app's logs go to Logfire through `LogfireLoggingHandler` (API only; the pipeline
   stays plain JSON). `LOGFIRE_CONSOLE=true` prints spans to the terminal.
   **Token (done, 2026-11-10):** `logfire_token: SecretStr | None` (blank = none);
   sent only when set; tests can't see `.env` (`conftest.py`). Deployed: FastAPI Cloud
   secret or its Logfire integration (undecided).
   **Next for Logfire:** `environment`, `X-Request-ID` on the request span, maybe
   exclude `/health` (Docker health checks every 30 s); then `instrument_psycopg`
   (step 1), `instrument_openai` + `chat`/tool spans (step 4). Prompt-content privacy:
   still open (§10.5).
1. **Config + DB connection:** settings, `db.py`, pool in `lifespan`, one trivial
   scoped query; tests with fakes. Decide the DB URL / role (§10).
2. **SQL scope helper + `get_application`** query and its tests (golden-rule tests first).
3. **Remaining queries** (`count`, `search`, `near`) with default filters + tests.
4. **OpenAI client + tool loop** (`llm.py`, `tools.py`, `prompt.py`) with a fake client.
5. **`POST /v1/chat`** router, error mapping, logging; real-server `curl` check.
6. **Accuracy set** run against real model + Neon; tune prompt/tools.
7. Docker image + deploy check (new secrets via `--value-stdin --secret`); `commands.md`
   and `.env.example` updated.
8. Later: streaming (SSE), Lewisham (`borough: "lewisham"` once data is on Neon),
   semantic search, server-side history.

## 10. Open questions (for the user)

1. **OpenAI model** to use (and whether it is a setting with a default).
2. **Database credentials for the app:** reuse `NEON_DATABASE_URL` (the push URL,
   write access) or a separate **read-only** Neon role, e.g. `CHAT_DATABASE_URL`
   (recommended).
3. **Narrow tools vs free SQL** for v1 (I recommend narrow tools, §5).
4. **Borough in the request:** required field, or default to `brent` until Lewisham
   is loaded?
5. **Logging user questions:** log the text, a length only, or nothing?
6. **Conversation state:** client-sent history (recommended for v1) vs server-side.
7. **Streaming** needed for the first version or later?
8. **Auth / rate limiting** on the endpoint (API key, none for now)?
9. Who consumes the API (a web UI, another service)? Affects CORS and response shape.
