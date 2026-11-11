# APIplan: the chat API (OpenAI + live Datahub) for Brent planning applications

My working plan for the **API part only**. Data gathering, the pipeline (`pipeline/`),
EDA (`findings/`) and the semantic files are the user's work; the API reads them and
does not change them, except where a step below says so. Updated as we go.

`CLAUDE.md` rules apply to everything here, the golden rule first. Work goes one file
at a time, each reviewed by the user before the next; the user commits.

## 1. Goal and the brief

The assignment brief is `python-dev-practical.pdf` (project root): a small chatbot API
that answers natural-language questions about planning applications, with tool calling
and citations.

| # | Brief: core, must have | Plan |
|---|---|---|
| 1 | uv for dependencies | done |
| 2 | FastAPI app exposing **`POST /chat`** with `{"question": str, "session_id": str}`, returns an answer | §6; exactly this path and body |
| 3 | OpenAI with tool/function calling, **at least 2 tools** (examples: `search_applications`, `get_application_details`, `count_applications`) | §5; the brief's three |
| 4 | **Tools must hit the live public API, with basic caching** | §4; live Datahub, in-memory cache |
| 5 | Responses include **citations** | §6; built in code from the tool calls |
| 6 | **Session context**, so follow-ups work ("what about the refused ones?") | §6; own in-memory sessions |
| 7 | `README.md`: setup + **3 example questions that work** | §9 step 7 |

Sample questions: "How many planning applications were validated in Brent in 2024?",
"How many of those were refused?", "How does that compare with Lewisham?".

**Scope now: Brent only.** Lewisham comes after the user's EDA for it produces
`semantic/lewisham/*.yaml`; the design makes that a data change, not a code change
(§3). Until then the bot says Lewisham is not covered yet. The database
(`core.applications` on Neon) is a later, optional **enrichment** (§10), not the
source for the tools.

## 2. Constraints (strict) and best practices

From three sources read on 2026-11-10: AWS, "Text-to-SQL solution powered by Amazon
Bedrock" (AWS); the PySemantic semantic-layer library (PS; the Reddit post itself
could not be read); Google Cloud, "Introducing QueryData for near-100% accurate data
agents" (QD). All three agree, and most of it is already in place here.

**Constraints: every change is checked against these.**

1. **The model never writes queries.** Tools are fixed query *blueprints* with typed
   arguments; code builds the Datahub query. (QD query blueprints; AWS structured output
   via function calling; PS structured filters with whitelisted operators.)
2. **Scope and safety are enforced in code, never by the prompt.** Every query goes
   through `scoped_query()`; every record is re-checked with `in_scope()`; the borough
   argument is a `Literal`; filter values come from the YAML `values` lists. (QD:
   security parameters passed outside the model; AWS: deterministic validators that
   catch missing filters.)
3. **Facts and numbers come only from tool results**, and **citations are built in code
   from the tool calls actually made**, never from the model's text. (AWS: users can
   see the query and data behind an answer; brief #5.)
4. **The semantic YAMLs are the single source of truth.** No field meaning, value list,
   rule or citation format is typed again in code or prompt; code reads the YAMLs.
   (AWS: business definitions in structured config files kept by subject experts;
   PS: define a metric once, use it everywhere.)
5. **Only documented fields reach the model**: those listed under `returned` in
   `semantic/brent/live_api.yaml`, each explained by its YAML. Everything else is not
   requested, or is dropped in the tool. One prompt line says what is not covered.
   (QD: unclear schemas and ambiguous values are the top causes of wrong answers.)
6. **Ambiguity: use a sensible default and state the assumption** ("validated in 2024,
   by valid_date"); ask a clarifying question only when no default makes sense. (QD:
   clarifying questions as a last resort.)
7. **Everything is bounded**: tool rounds per question, HTTP timeouts, result size per
   tool, session length, cache lifetime. A confused model or a slow upstream fails fast
   with a clear status (503/504) and never hangs.
8. **Few round trips, few tokens.** Three coarse tools, not many small ones; parallel
   tool calls when the model asks for several at once; compact JSON results; the prompt
   carries `meaning` + `rules` only, `values` go into tool schemas, and known issues
   travel with the records they concern (the cleaned flags). (AWS: latency is the sum
   of the agent's steps.)
9. **Accuracy is measured, not claimed**: an evaluation set from the user's notebook
   figures and the brief's questions, re-run after any change to model, prompt, tools
   or YAML. (QD: Evalbench and iterate; AWS: validation as a safety-critical layer.)
10. **Keep it proportionate.** No knowledge graph, vector search, majority voting,
    multi-agent supervisor or join resolver: they solve problems of hundreds of tables,
    and we have one index and three tools. (All three sources work at enterprise scale.)

**Project rules that also apply:** no real OpenAI or Datahub calls in `pytest` (fakes
via `MockTransport`); never log keys, or OpenAI's error text, in our own logs; Logfire
traces every request, OpenAI call and Datahub call; a real-server and Docker check
after each step.

## 3. Reusing what exists (do not reinvent)

| Need | Reused from |
|---|---|
| Field meanings, answer rules, allowed values | `semantic/brent/*.yaml` |
| What is available live, what to request, live rules, the uncovered line, live citations | `semantic/brent/live_api.yaml` (added 2026-11-11) |
| Cleaning of live records (values, flags, address, legacy) | `pipeline/clean.py` `clean_record()` + `cleaning.yaml` |
| Records excluded from counts | drop rules in `cleaning.yaml` (ids + the empty-record condition) |
| Default filters ("decided", "pending", ...) | `semantic/database_schema.md` + YAML `rules` |
| Borough filter and re-check | `scope.py` `scoped_query()`, `in_scope()` |
| Datahub request pattern (header, `_count`, `_search`) | `pipeline/extract.py` |
| Citation wording | `sources.yaml` + `live_api.yaml` `citation` |
| Expected answers for the evaluation set | notebook findings in `findings/brent/` |

Adding Lewisham later = its `semantic/lewisham/` folder + widening `Literal["Brent"]`.

## 4. The live Datahub

- Elasticsearch behind a guest gateway, `https://planningdata.london.gov.uk/api-guest`,
  header `X-API-AllowRequest` (a secret in `.env`; today only in `PipelineSettings`).
- Index mapping, checked 2026-11-10: `valid_date` and `decision_date` are **dates**
  (`dd/MM/yyyy`), so year ranges run server-side; `decision`, `status`,
  `application_type`, `borough` have `.raw` for exact matches; **`lpa_app_no` has no
  `.raw`**: look it up as a phrase and check the exact match in code (or build `id`).
- Requests use `_source: request_fields` from `live_api.yaml`: no site polygons, no
  nested lists.
- **Cache:** in memory, keyed by the exact query, with a lifetime (e.g. 10 minutes);
  hits and misses logged and traced. Per process, lost on restart (said in the README).
- To verify in the count step: whether "empty" fields arrive missing or as `""` (the
  empty-record exclusion must cover both); prefix filtering on `lpa_app_no` (text).

## 5. Tools (query blueprints)

| Tool | Datahub call | Notes |
|---|---|---|
| `count_applications(local_authority, year, decision?, date_basis="validated")` | `_count`: date range on `valid_date` (or `decision_date`), decision on `.raw`, dropped records excluded | default filters applied; the answer states the date basis |
| `get_application_details(reference)` | `_search` on the reference; exact match checked in code | `clean_record` -> cleaned values, flags, portal link; not found (including out of scope) -> "not found" |
| `search_applications(local_authority, decision?, year?, text?, limit<=10)` | `_search`, newest first, plus the total | `clean_record` on each hit; only `returned` fields |

`local_authority: Literal["Brent"]` for now. Argument models are Pydantic; their JSON
schema (with allowed values from the YAMLs) is what OpenAI sees.

## 6. Endpoint, sessions, response

`POST /chat`, exactly as in the brief (not under `/v1`, so graders' calls work as
written).

```jsonc
// request
{ "question": "How many planning applications were validated in Brent in 2024?", "session_id": "abc" }
// response
{ "answer": "...",
  "citations": [ {"source": "Planning London Datahub API (live)", "query": "Brent, validated in 2024", "retrieved_at": "..."},
                 {"reference": "24/1234", "url": "https://pa.brent.gov.uk/..."} ],
  "session_id": "abc" }
```

- **Chat loop:** OpenAI **Responses API** (`gpt-6-luna`) with the tools above; a cap on
  tool rounds (e.g. 5); parallel tool calls run concurrently.
- **Sessions (decided):** our own in-memory store keyed by `session_id`, holding each
  turn's question, tool calls and answer; capped turns; an expiry. Per process, lost
  on restart.
- **Prompt:** built from the YAMLs (constraints 4, 5, 8): role and scope, the live
  fields' `meaning` + `rules`, `live_rules`, the `uncovered` line, the ambiguity rule
  (decided: default + stated assumption), "numbers only from tools", the citation rule.
- Limits at the edge: question length, `session_id` format (422 beyond them).
- Status codes: 422 bad input; 503 OpenAI or Datahub unavailable, rate-limited, key
  rejected or not configured; 504 timeout; 500 only for our bugs.
- Logging: the request line as now, plus one `chat finished` line with metadata only
  (session, rounds, tools called, tokens, cache hits, duration), no question text.
  Logfire keeps prompts and answers (accepted trade-off, step 0b).

## 7. Code layout (one job per module)

| Module | Job |
|---|---|
| `semantic.py` | load `semantic/<borough>/*.yaml` into typed models at start-up; prompt text, allowed values, citation formats |
| `datahub.py` | async client (settings: URL, header secret, timeout), `count`, `search`, cache, scope re-check |
| `tools.py` | the three blueprints: argument models, query building, cleaning, compact results + citation entries |
| `chat.py` | the tool loop on the Responses API; collects citations |
| `sessions.py` | in-memory sessions |
| `routers/chat.py` | `GET /v1/chat/status` (exists) and `POST /chat` |
| `schemas.py` | `ChatRequest`, `ChatResponse`, `Citation` |

`semantic/` must ship with the app: remove it from `.dockerignore` and
`.fastapicloudignore` (step 1).

## 8. Testing

- No real OpenAI or Datahub calls: fakes with `httpx2.MockTransport` (OpenAI) and
  `httpx.MockTransport` (Datahub); scripted tool calls, then an answer.
- Scope: every Datahub query carries the scope filter; out-of-scope records from the
  fake Datahub are dropped and logged (WARNING); out-of-scope references -> not found.
- Tools: each builds the expected query (date range, `.raw` filters, exclusions); a
  repeat is a cache hit; only `returned` fields come back.
- Chat loop: tool call -> result -> answer; max rounds; parallel calls; citations match
  the tools called; a follow-up uses the session.
- Errors: Datahub or OpenAI timeout -> 504; down or rate-limited -> 503; each with
  `X-Request-ID`.
- Semantic loader: every `see:` file exists; a malformed YAML fails at start-up.
- **Evaluation set** (outside `pytest`, real model + live API): the brief's questions
  + about 10 from the notebooks; counts checked against a direct `_count` at run time
  (live data changes). Optionally with `pydantic_evals`, visible in Logfire.

## 9. Steps (one file at a time; the user reviews and commits)

0. **Logfire (done, 2026-11-10):** `observability.py`, `instrument_fastapi`, app logs
   via `LogfireLoggingHandler` (API only; the pipeline stays plain JSON),
   `LOGFIRE_CONSOLE`, `LOGFIRE_TOKEN` (sent only when set; tests never send),
   `ENVIRONMENT` (`local` / `production`) on every trace and in the `app started` line.
   Deployed token: FastAPI Cloud secret or its Logfire integration (undecided).
   **Still open:** `X-Request-ID` on the request span; maybe exclude `/health` (Docker
   health checks every 30 s).
0b. **OpenAI connection (done, 2026-11-10):** `GET /v1/chat/status` checks that OpenAI
   can be used (`models.retrieve`, no tokens). `llm.py` builds one `AsyncOpenAI` from
   `Settings`; `lifespan` creates and closes it. Decisions:
   - model `gpt-6-luna` (`OPENAI_MODEL`); timeout 30 s, 2 retries;
   - key **optional**: the app runs without it, status answers 503 `not_configured`
     (deliberate exception to "missing config fails fast"; noted in `Settings`);
   - 200 ok; 503 not configured / unreachable / rate-limited / rejected (ERROR log);
     504 timeout; our logs and responses never carry OpenAI's error text;
   - key permissions: List models = Read, Responses = Write, everything else None;
   - `logfire.instrument_openai(AsyncOpenAI)`. **Accepted trade-off (user,
     2026-11-10):** Logfire keeps prompts, answers and a failed call's error text (never
     scrubbed; for a wrong key, its last 4 characters). A company could send the same
     OpenTelemetry data to a self-hosted backend instead;
   - Logfire's log handler has a `NullHandler` fallback, so logs stay JSON-only.
   - **Not checked yet:** whether the free tier covers generation (step 5 shows it;
     429 `insufficient_quota` = add credit).
1. **Semantic files (in progress):** `live_api.yaml` added 2026-11-11 (no other YAML
   changed). Next: ship `semantic/` with the app (`.dockerignore`, then
   `.fastapicloudignore`).
2. **`semantic.py`:** typed loader + tests.
3. **`datahub.py`:** settings, client, cache, scope re-check + tests.
4. **`count_applications`** + tests; check the cleaned Brent count (36,531) live.
5. **Chat loop + `POST /chat` + sessions + citations.** Sample questions 1 and 2 end to
   end; first real generation (the quota check).
6. **`get_application_details`, `search_applications`.**
7. **README:** setup and 3 example questions, run for real.
8. If time allows: the evaluation set; `DEPLOY.md` (checklist) and a FastAPI Cloud
   deploy.

## 10. Later (after the brief's must-haves)

- **Lewisham:** after its EDA (`semantic/lewisham/`), widen `local_authority`.
- **Database enrichment (hybrid):** async SQLAlchemy following landgraph's pattern
  (`db.py` `Database` class with bounded waits, `models.py`, `queries.py`, the
  site-snapshot route) on `core.applications`; one `DATABASE_URL` (local PostGIS
  locally, Neon in production, direct not pooled); a read-only role; the app starts
  without it. Uses: `ward_2022` and location answers, cleaned counts next to live ones.
- More fields (homes, floorspace, ...) through EDA -> YAML -> `live_api.yaml`.
- Streaming answers.

## 11. Open questions

1. ~~OpenAI model~~ **Answered:** `gpt-6-luna` (step 0b).
2. ~~Database credentials~~ **Answered:** one `DATABASE_URL`, read-only role (§10).
3. ~~Narrow tools vs free SQL~~ **Answered:** blueprints only (constraint 1).
4. ~~Borough in the request~~ **Answered:** a tool argument, Brent only for now.
5. **Logging questions:** Logfire keeps them (accepted); our JSON `chat finished` line
   carries metadata only (to confirm at step 5).
6. ~~Conversation state~~ **Answered:** own in-memory sessions.
7. ~~Streaming~~ **Answered:** later (§10).
8. **Auth / rate limiting on `POST /chat`:** none for the assignment? (Each question
   costs OpenAI tokens.)
9. ~~Who consumes the API~~ **Answered:** graders, via `POST /chat` (the brief).
