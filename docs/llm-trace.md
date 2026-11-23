# Sessions and model-call traces

> No external observability platform; Skein stores it and shows it in the console.

## What is stored

SQLite table `llm_calls`:

| Field | Meaning |
| --- | --- |
| `session_id` | chat thread id / business session id |
| `actor` | who caused the call — a user sub, `assistant:<name>`, or empty for the machine |
| `source` | which component, e.g. `skein-agent` (console Agent); assistants choose their own |
| `model` | model name |
| `prompt` / `response` | clipped to 16k characters |
| `prompt_tokens` / `completion_tokens` | as reported by the gateway; 0 if unknown |
| `latency_ms` | duration |
| `status` / `error` | `ok` / `error` |
| `created_at` | server time |

## Who writes

- **The console Agent**: `source=skein-agent`, one record for planning and one for the final
  answer (`agent.py`). The actor is the signed-in person.
- **Assistants** (optional): with `SKEIN_API_TRACE=1` an assistant can report its own calls
  through `POST /api/v1/llm/calls`; records are attributed to the credential, whatever the
  body claims as `actor`.
- **Internal services**: anything holding the master token can `POST /internal/llm/calls`
  and tag its `source`.
- **Workers** do not report individual model calls; their transcripts are the session
  record (see [architecture.md](architecture.md) §7).

## Endpoints

| Method | Path | Note |
| --- | --- | --- |
| POST | `/internal/llm/calls` | Record one call (`ops.ingest`) |
| POST | `/api/v1/llm/calls` | Record one call as an assistant (only with `SKEIN_API_TRACE=1`) |
| GET | `/internal/llm/sessions` | Aggregated by session: calls, tokens, last model/status |
| GET | `/internal/llm/calls?session_id=&source=&limit=` | Detail for one session |

Recording failures only log; they never break the caller.

## Console

**运行 → 会话**: the left side lists sessions (source, call count, tokens, user, last
activity); the right side shows each call — model, latency, tokens, status, expandable
input/output.

## Storage notes

No configuration; the table is created with the schema. **`prompt` / `response` may contain
user text and are kept forever** — consider disk and privacy requirements before enabling
trace ingestion in a deployment that handles sensitive conversations. A retention policy
and redaction are not implemented.
