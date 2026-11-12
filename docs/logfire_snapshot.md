# Logfire snapshot: one session on the deployed app

Taken 2026-11-12 from the Logfire project (EU region) of the deployed app
(`https://brent-lewisham-chat.fastapicloud.dev`, `deployment_environment = production`).
Session `logfire-demo` asks the README's three questions in order; a second session,
`logfire-demo-cache`, then asks the first question again to show a Datahub cache hit.

Only metadata is shown: timings, token counts, tool calls and their arguments, Datahub
cache hits. The full prompts and model messages are in the trace but left out here.
Exported with a read-only Logfire query key; checked for secrets before writing.

## Summary

| # | Session | Question | Answer | Total | OpenAI rounds | Tokens in / cached / out / reasoning | Datahub cache hit |
|---|---|---|---|---|---|---|---|
| 1 | `logfire-demo` | How many planning applications were validated in Brent in 2024? | 3,140 planning applications were validated in Brent in 2024, using the valid date | 3598 ms | 2 | 15,282 / 15,276 / 68 / 0 | no |
| 2 | `logfire-demo` | How many of those were refused? | 722 of the applications validated in 2024 were recorded as refused by the council | 2884 ms | 2 | 15,699 / 15,431 / 97 / 17 | no |
| 3 | `logfire-demo` | How does that compare with Lewisham? | Lewisham had 389 applications validated in 2024 that were refused, compared with Brent’s 722—333 fewer in Lewisham | 2688 ms | 2 | 16,228 / 15,730 / 115 / 22 | no |
| 4 | `logfire-demo-cache` | How many planning applications were validated in Brent in 2024? | 3,140 planning applications were validated in Brent in 2024, counting by valid date | 2287 ms | 2 | 15,282 / 15,276 / 68 / 0 | yes |

Reading it:

- Each question takes two OpenAI rounds: one that calls a tool, one that writes the
  answer from the tool result. Facts and citations come from the tool, not the model.
- Question 3 makes one Lewisham call only: Brent's 722 comes from the session history,
  with its earlier citation `[c2]` attached again.
- Most input tokens are served from OpenAI's prompt cache (the instructions and tool
  definitions are the same on every request).
- Question 4 is question 1 in a new session: the Datahub answer comes from the app's
  cache (0 ms, `cache_hit=true`), and its citation keeps the original `retrieved_at`.

## The traces

### 1. How many planning applications were validated in Brent in 2024?

Session `logfire-demo`, request ID `cba7b284-da20-4b9e-b685-499f3e72b1f7` (the `X-Request-ID` header; also the
`correlation_id` on every log line of this request).

```
POST /chat  200  3598 ms
└─ chat  3595 ms  rounds=2  reasoning: no reported reasoning
   ├─ OpenAI gpt-6-luna  2119 ms  in=7,588 (cached 7,585)  out=32  reasoning=0  -> tool_calls
   ├─ tool count_applications  124 ms  count=3140
   │    args {"local_authority": "Brent", "year": 2024, "month": null, "decision": null, "application_type": null, "appeal_decision": null, "pending": false, "date_basis": "validated"}
   │  └─ datahub _count  123 ms  cache_hit=false
   ├─ OpenAI gpt-6-luna  1346 ms  in=7,694 (cached 7,691)  out=36  reasoning=0  -> stop
   └─ log: chat finished  citations=1  datahub_cache_hits=0
```

> 3,140 planning applications were validated in Brent in 2024, using the valid date. Legacy records without a valid date are excluded. [c1]

- `[c1]` Planning London Datahub API (live): Brent applications validated in 2024 (valid_date), retrieved 2026-11-12T00:24:04+00:00.

### 2. How many of those were refused?

Session `logfire-demo`, request ID `e7348f83-4f54-4658-bab1-0eb0cdcdbf61` (the `X-Request-ID` header; also the
`correlation_id` on every log line of this request).

```
POST /chat  200  2884 ms
└─ chat  2882 ms  rounds=2  reasoning: reasoned (17 tokens), but OpenAI returned no summary
   ├─ OpenAI gpt-6-luna  1466 ms  in=7,743 (cached 7,691)  out=56  reasoning=17  -> tool_calls
   ├─ tool count_applications  92 ms  count=722
   │    args {"local_authority": "Brent", "year": 2024, "month": null, "decision": "Refused", "application_type": null, "appeal_decision": null, "pending": false, "date_basis": "validated"}
   │  └─ datahub _count  91 ms  cache_hit=false
   ├─ OpenAI gpt-6-luna  1315 ms  in=7,956 (cached 7,740)  out=41  reasoning=0  -> stop
   └─ log: chat finished  citations=1  datahub_cache_hits=0
```

> 722 of the applications validated in 2024 were recorded as refused by the council. This is based on valid date; legacy records without a valid date are excluded. [c2]

- `[c2]` Planning London Datahub API (live): Brent applications validated in 2024 (valid_date) with council decision Refused, excluding decisions recorded but not yet issued, retrieved 2026-11-12T00:24:07+00:00.

### 3. How does that compare with Lewisham?

Session `logfire-demo`, request ID `060c7d21-4ee4-4d73-b12e-77d1c0747896` (the `X-Request-ID` header; also the
`correlation_id` on every log line of this request).

```
POST /chat  200  2688 ms
└─ chat  2686 ms  rounds=2  reasoning: reasoned (22 tokens), but OpenAI returned no summary
   ├─ OpenAI gpt-6-luna  1335 ms  in=7,993 (cached 7,740)  out=62  reasoning=22  -> tool_calls
   ├─ tool count_applications  105 ms  count=389
   │    args {"local_authority": "Lewisham", "year": 2024, "month": null, "decision": "Refused", "application_type": null, "appeal_decision": null, "pending": false, "date_basis": "validated"}
   │  └─ datahub _count  104 ms  cache_hit=false
   ├─ OpenAI gpt-6-luna  1235 ms  in=8,235 (cached 7,990)  out=53  reasoning=0  -> stop
   └─ log: chat finished  citations=1  datahub_cache_hits=0
```

> Lewisham had 389 applications validated in 2024 that were refused, compared with Brent’s 722—333 fewer in Lewisham. Both figures use valid date and count council refusals. [c3] [c2]

- `[c3]` Planning London Datahub API (live): Lewisham applications validated in 2024 (valid_date) with council decision Refused, retrieved 2026-11-12T00:24:10+00:00.
- `[c2]` Planning London Datahub API (live): Brent applications validated in 2024 (valid_date) with council decision Refused, excluding decisions recorded but not yet issued, retrieved 2026-11-12T00:24:07+00:00.

### 4. How many planning applications were validated in Brent in 2024?

Session `logfire-demo-cache`, request ID `c395011f-cd40-423f-939d-fc02d50abefe` (the `X-Request-ID` header; also the
`correlation_id` on every log line of this request).

```
POST /chat  200  2287 ms
└─ chat  2285 ms  rounds=2  reasoning: no reported reasoning
   ├─ OpenAI gpt-6-luna  1039 ms  in=7,588 (cached 7,585)  out=32  reasoning=0  -> tool_calls
   ├─ tool count_applications  1 ms  count=3140
   │    args {"local_authority": "Brent", "year": 2024, "month": null, "decision": null, "application_type": null, "appeal_decision": null, "pending": false, "date_basis": "validated"}
   │  └─ datahub _count  0 ms  cache_hit=true
   ├─ OpenAI gpt-6-luna  1239 ms  in=7,694 (cached 7,691)  out=36  reasoning=0  -> stop
   └─ log: chat finished  citations=1  datahub_cache_hits=1
```

> 3,140 planning applications were validated in Brent in 2024, counting by valid date. Legacy records without a valid date are excluded. [c1]

- `[c1]` Planning London Datahub API (live): Brent applications validated in 2024 (valid_date), retrieved 2026-11-12T00:24:04+00:00.
