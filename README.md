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

A single application can be looked up by its reference too, e.g. "Tell me about
application 23/0650".

## How it works

- `POST /chat` runs an OpenAI tool loop with two tools: `count_applications` and
  `get_application_details`. Both query the live Datahub, and the results are cached
  for 10 minutes.
- The borough filter is fixed in code (`scope.py`). Any record outside Brent or Lewisham
  is dropped, and a lookup outside them finds nothing.
- What each field means and how it is cleaned was learnt from an EDA of each borough
  (`findings/`). The results are written down in `semantic/<borough>/*.yaml`, and the
  bot reads them at start-up for its prompt, its tool values and its cleaning rules.
- Citations are built in code from the actual queries, not written by the model.

## Limits

- Only counts (by borough, year, decision) and single-application details are
  supported. Questions about wards, floorspace, homes etc. are answered with "not
  covered yet".
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
