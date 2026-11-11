# London Brent & Lewisham planning chat

A chatbot API that answers questions about planning applications in the **London
Boroughs of Brent and Lewisham**. It uses OpenAI tool calling over the live
[Planning London Datahub](https://planningdata.london.gov.uk/). Every answer comes with
its citations, and a `session_id` keeps the context for follow-up questions. Other
areas are not covered.

## Run it

You need [uv](https://docs.astral.sh/uv/) (Python 3.14 is installed by uv) and an
OpenAI API key.

```bash
cp .env.example .env      # then set OPENAI_API_KEY=sk-...  (the Datahub header is already filled in)
uv sync
uv run fastapi run        # http://localhost:8000/docs
```

Or with Docker:

```bash
docker build -t lblchat .
docker run --rm -p 8000:8000 --env-file .env lblchat
```

Check that it is ready:

```bash
curl localhost:8000/health              # {"status":"ok"}
curl localhost:8000/v1/chat/status      # {"status":"ok","model":...} once the OpenAI key works
```

## Three example questions

Send them in order with the same `session_id`, so that questions 2 and 3 build on the
earlier answers.

```bash
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"question": "How many planning applications were validated in Brent in 2024?", "session_id": "demo"}'

curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"question": "How many of those were refused?", "session_id": "demo"}'

curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"question": "How does that compare with Lewisham?", "session_id": "demo"}'
```

These were the answers on 2026-11-11. The data is live, so the numbers can change.

| Question | Answer |
|---|---|
| Validated in Brent in 2024 | 3,140 |
| Of those, refused | 722 |
| Compared with Lewisham | 389 refused out of 2,599 validated in Lewisham, vs Brent's 722 out of 3,140 |

Each response looks like this:

```json
{
  "answer": "3,140 planning applications were validated in Brent in 2024 [c1].",
  "citations": [
    {"id": "c1", "text": "Planning London Datahub API (live): Brent ...", "query": {...}, "retrieved_at": "..."}
  ],
  "session_id": "demo"
}
```

## More questions you can ask

Each one in a new `session_id`. The answers are from live runs on 2026-11-11.

| Question | Answer |
|---|---|
| How many householder applications were refused in Lewisham in 2024? | 92 (by decision date) |
| How many appeals were allowed in Brent in 2025? | 37 (by appeal decision date) |
| How many applications are currently pending in Brent? | 493 (out-of-date pending records left out) |
| How many applications were decided in Brent in March 2026? | 213 |
| Show me the 5 most recent refused applications in Brent. | 26/1999, 26/2071, 26/2134, 26/2170, 26/2201 (all refused 4 Nov 2026), each with address and summary |
| Were there any applications for basements in Brent in 2025? | Yes, 109, with an example (25/3257, 166A Chamberlayne Road) |
| List recent applications on Church Road in Brent. | The 5 newest of 144, with date and outcome |
| Are there any applications at SE13 6LG in Lewisham? | 4, all at Lewisham Library, with their references |
| Tell me about application 23/0650 | Prior Approval for a 5G monopole at 103 Church Road, approved 21 April 2023, with its portal link |

Counts can be narrowed by borough, year or month, council decision (Approved,
Refused, ...), application type (Householder, Prior Approval, All Other), appeal
outcome (Allowed, Dismissed, ...) and pending. The year can mean when applications
were validated, decided, or appealed. Lists use the same filters, plus words in the
description and a street, place or postcode in the address, and show up to 10, newest
first.

## Limits

- Counts, lists of up to 10 (with the filters above) and single-application details
  are supported. Questions about wards, floorspace, homes, decision times etc. are
  answered with "not covered yet".
- Address search matches the published address text. About 16% of Brent and 19% of
  Lewisham addresses have no full postcode, so a postcode with no match is reported
  as "doesn't confirm there are none".
- Sessions and the cache live in memory, so they are lost on restart.
- Lewisham's "pending" counts may include old applications that were decided but never
  updated.
- If `LOGFIRE_TOKEN` is set, traces (including questions and answers) are sent to
  Logfire. Leave it empty to send nothing.

## Tests

```bash
uv run pytest -q
```

Settings are listed in `.env.example`; more commands are in `commands.md`.

## Architecture

```
                    offline, once per borough                          at start-up
 Datahub snapshot ──▶ EDA notebooks (findings/) ──▶ semantic layer (semantic/<borough>/*.yaml)
                                                                │ prompt, tool values,
                                                                ▼ cleaning rules
 POST /chat ──▶ OpenAI tool loop ──▶ tools ──▶ cache (10 min) ──▶ live Datahub API
   ▲                                   │          miss only      (borough filter always on)
   └── answer + citations ◀────────────┘  scope check + cleaning on every record
```

- **EDA:** each field of each borough was studied in a notebook in `findings/brent/`
  and `findings/lewisham/`, against a raw snapshot of the Datahub. The notebooks are
  kept with their outputs as the evidence. They cover what each field means, its
  values, known errors (checked by hand against the council portals) and test or
  duplicate records.
- **Semantic layer:** the EDA's conclusions are written down once in
  `semantic/<borough>/*.yaml`, with one file per field plus `cleaning.yaml`,
  `sources.yaml` and `live_api.yaml`. Both boroughs use the same structure. At
  start-up the app builds from them:
  - the model's instructions;
  - the values the tools accept (e.g. decisions);
  - the cleaning applied to every record;
  - the citation texts.

  An invalid file stops the app at start-up.
- **Live API hits:** there is no database. Three tools, `count_applications`,
  `search_applications` and `get_application_details`, send fixed Elasticsearch
  queries to the live Planning London Datahub. The model fills in the arguments
  (borough, year, decision, words, reference, ...), never the query itself. Citations are built in code from the query that
  was actually sent.
- **Caching:** a small in-memory cache per borough (`TTLCache` in `datahub.py`) keeps
  each Datahub answer for 10 minutes, keyed by the exact query. Every session shares
  it. Only successful answers are cached. It is bounded to 256 entries and emptied on
  restart.
  Within one session, a repeated question is answered from the conversation history
  without a tool call. The cache is used when another session builds the same query:
  its `chat finished` log line shows `datahub_cache_hits`, and the Logfire span shows
  `cache_hit: true`.
- **Closed scope:** Brent and Lewisham are a fixed allow-list in code
  (`Literal["brent", "lewisham"]` in `scope.py`), not a setting. Every query is built
  with the borough filter, and every returned record is checked again. Anything outside
  the two boroughs is dropped and logged, and a reference from elsewhere is not found.
  Tests prove that a query can't be built without the filter.
- **Tools for the AI:** the model uses OpenAI function calling with three tools. Each
  tool's argument schema is a Pydantic model built from the semantic layer, so the
  model can only pick allowed values. Bad arguments are sent back to the model as an
  error, and it has at most 5 rounds per question.
  - `count_applications(local_authority, year?, month?, decision?, application_type?,
    appeal_decision?, pending?, date_basis)`: counts with `_count`. `date_basis` is
    `validated` (when applications came in), `decided` (when the decision was made)
    or `appeal_decided` (when the appeal was decided). Bad combinations, such as a
    month without a year, go back to the model as an error before any request is sent.
    Comparing the two boroughs takes one call for each.
  - `search_applications(...count arguments, description_words?, address_text?,
    limit?)`: the same filters as the count (the year is optional here), plus all
    the words in the description and a phrase in the address. It lists up to 10
    applications, newest first by the `date_basis` date, each cleaned and summarised,
    with one citation for the search. An address with no match adds a note that this
    doesn't prove there are none.
  - `get_application_details(reference, local_authority?)`: one application, cleaned,
    with its portal link. It tries both reference forms (e.g. with and without
    Lewisham's `DC/`).
- **Logfire:** if `LOGFIRE_TOKEN` is set, the app sends traces to
  [Logfire](https://logfire.pydantic.dev/):
  - each HTTP request (`instrument_fastapi`);
  - each OpenAI call, including prompts, tool calls and the model's reasoning summary
    (`instrument_openai`);
  - our own spans for each Datahub query, with `cache_hit`.

  Without a token nothing is sent and the app runs the same. `ENVIRONMENT` tags
  traces as `local` or `production`.
- **Custom logging:** stdout gets JSON logs (`log_config.py`), with ISO 8601 UTC
  timestamps. Every request gets an `X-Request-ID` and exactly one `request finished`
  line, and each question gets one `chat finished` line with rounds, tools, tokens and
  cache hits, but never the question or answer text. Unhandled errors are logged with
  their traceback, and the response is a plain 500 that still carries the ID.
- **Testing with fixtures:** `uv run pytest -q` runs offline:
  - `tests/conftest.py` has an autouse fixture that hides `.env` and the keys, so no
    test can reach OpenAI, the Datahub or Logfire;
  - it also has an `app` fixture (built with explicit `Settings`) and a `client`
    fixture (`TestClient`, with the lifespan running);
  - fake clients (`FakeOpenAI`, `FakeDatahub` in `tests/test_chat.py`) script the
    model's tool calls and the Datahub's answers, and record every request;
  - tests cover the scope rule, cleaning against the real semantic YAMLs, the cache,
    sessions and follow-ups, citations, 422/503/504 errors, and the log lines.
