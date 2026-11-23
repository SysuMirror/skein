# Connecting an assistant

An **assistant** is any external program that turns something users said somewhere — a
forum post, a chat message, a monitoring alert — into a ticket, and wants to know how that
ticket is going. Skein gives each assistant its own credential and a small, versioned
public API. The forum bot of a deployment is just the first user of it; a nightly checker
or a chat assistant looks exactly the same from here.

The credential is deliberately not the master token, and the API is deliberately small:

- An assistant key can **submit a request** (idempotently), **query the tickets it
  submitted**, and — if the deployment allows it — **report model-call traces**.
- It cannot dispatch, review, cancel, read other assistants' tickets, or touch anything
  under `/internal/*`. The master token does not work on the public API, and an assistant
  key does not work on the internal API.
- Callbacks about ticket status are sent with an HMAC signature made from a per-assistant
  secret, so the assistant never needs to hold a Skein credential to verify them.

## Five minutes

### 1. Issue a credential

Console: **运行 → 助手 → 签发凭证**. CLI (on the control plane host):

```bash
skein assistant issue --name forum-bot --purpose "turns forum posts into tickets" \
  --callback-url https://bot.example.com/internal/skein/notify
```

The key (`skein_at_…`) and, if a callback URL was given, the signing secret
(`whsec_…`) are printed **once**. Put them in the assistant's own environment:

```bash
export SKEIN_URL=https://skein.example.com
export SKEIN_ASSISTANT_KEY=skein_at_…
export SKEIN_WEBHOOK_SECRET=whsec_…   # only if you use callbacks
```

### 2. Submit a request with curl

```bash
curl -s -X POST "$SKEIN_URL/api/v1/tickets" \
  -H "Authorization: Bearer $SKEIN_ASSISTANT_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "title": "Login page is misaligned on phones",
        "description": "On iPhone the sign-in button is off screen. Screenshot: https://forum.example.com/p/42",
        "source": "forum",
        "external_id": "post:42",
        "requester": "forum:278",
        "severity": "normal",
        "category": "bug",
        "labels": ["login", "mobile"]
      }'
```

```json
{
  "created": true,
  "ticket": {
    "id": "8f14e45fceea167a5a36dedd4bea2543",
    "code": "SK-8F14E45F",
    "title": "Login page is misaligned on phones",
    "status": "staged",
    "public_status": "received",
    "source": "forum",
    "external_id": "post:42",
    "requester": "forum:278",
    "created_at": 1795444357.8,
    "updated_at": 1795444357.8,
    "url": "https://skein.example.com/console/#/tickets/8f14e45fceea167a5a36dedd4bea2543"
  }
}
```

Send the same request again (network retry, crashed process, at-least-once queue) and you
get `HTTP 200` with `"created": false` and the *same* ticket. That is the whole
idempotency story: `(source, external_id)` is the key, scoped to the credential.

### 3. Query it later

```bash
curl -s "$SKEIN_URL/api/v1/tickets?status=review" -H "Authorization: Bearer $SKEIN_ASSISTANT_KEY"
curl -s "$SKEIN_URL/api/v1/tickets/post:42"          -H "Authorization: Bearer $SKEIN_ASSISTANT_KEY"
```

The second one looks a ticket up by its `id` or its `code`, whichever you kept.

### 4. Read the status back

When the ticket changes state, the assistant gets a webhook — if it registered one:

```http
POST /internal/skein/notify HTTP/1.1
X-Skein-Event: ticket.status
X-Skein-Webhook-Timestamp: 1795444400
X-Skein-Webhook-Signature: v1=5a1f…

{"event":"ticket.status","ticket":{"id":"8f14…","code":"SK-8F14E45F","status":"done",
 "public_status":"fixed","title":"…","source":"forum","external_id":"post:42",
 "requester":"forum:278","url":"https://skein.example.com/console/#/tickets/8f14…"}}
```

Verify it before trusting it (Python, standard library):

```python
import hashlib, hmac, json, time

def verify(body: bytes, timestamp: str, signature: str, secret: str) -> dict | None:
    if abs(time.time() - int(timestamp)) > 300:
        return None
    expected = "v1=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return json.loads(body)
```

### 5. The same in TypeScript

```ts
const res = await fetch(`${process.env.SKEIN_URL}/api/v1/tickets`, {
  method: "POST",
  headers: {
    Authorization: `Bearer ${process.env.SKEIN_ASSISTANT_KEY}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    title: message.subject,
    description: message.body,
    source: "forum",
    external_id: `post:${message.id}`,
    requester: `forum:${message.authorId}`,
  }),
});
if (res.status === 429) throw new Error(`slow down; retry after ${res.headers.get("Retry-After")}s`);
const { ticket } = await res.json(); // created or replayed — same ticket
```

## API reference (`/api/v1`)

All endpoints take `Authorization: Bearer <key>`. Bodies are JSON, at most 64 KiB. Every
response carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`.

### `POST /api/v1/tickets`

| Field | Required | Notes |
| --- | --- | --- |
| `title` | yes | 1–200 characters |
| `description` | no | up to 20 000 characters |
| `source` | yes | the system this came from, e.g. `forum`, `chat`, `monitoring`; lowercase slug |
| `external_id` | yes | the request's id *in that system*; `(source, external_id)` is the idempotency key |
| `requester` | no | who asked, in the source system's own identifiers (e.g. `forum:278`) |
| `severity` | no | `low` \| `normal` (default) \| `high` \| `critical` |
| `category` | no | a short slug, e.g. `bug`, `feature` |
| `labels` | no | up to 20 short strings |
| `links` | no | up to 10 `http(s)` URLs (evidence, the original thread, logs) |

`201` when created, `200` when it was a retry. `409 idempotency_conflict` when the same
`(source, external_id)` arrives with a different title — that is almost always a bug in the
assistant (reusing an id for a new request); use a new `external_id`.

### `GET /api/v1/tickets?status=&limit=`

The assistant's own tickets, newest first; `limit` defaults to 50, at most 200. `status`
filters on Skein's internal status (`staged`, `assigned`, `running`, `review`, `done`,
`rejected`).

### `GET /api/v1/tickets/{ref}`

By `id` or by `code` (`SK-…`, a leading `#` is fine). Someone else's ticket is
indistinguishable from a missing one: `404 not_found`.

### `GET /api/v1/me`

The credential's name, purpose, last use, callback URL (never the key), and the scopes it
has: `api.ticket.create`, `api.ticket.read`, and `api.trace.write` when the deployment
enables trace ingestion.

### `POST /api/v1/llm/calls` (optional)

Off unless the deployment sets `SKEIN_API_TRACE=1`. Lets an assistant report the model
calls it made (session, model, prompt, response, tokens, latency, status) so they show up
in **运行 → 会话** next to the control plane's own. Records are attributed to the
credential, not to whatever `actor` the body claims.

### Errors

```json
{"detail": {"code": "invalid_key", "message": "missing, unknown or revoked assistant key"}}
```

| Status | `code` | When |
| --- | --- | --- |
| 401 | `invalid_key` | missing / unknown / revoked key (the master token lands here too) |
| 404 | `not_found` | not your ticket, or no such ticket |
| 409 | `idempotency_conflict` | same key, different title |
| 413 | `payload_too_large` | body over 64 KiB |
| 422 | `invalid_request` | a field is missing or malformed; `fields[]` says which |
| 429 | `rate_limited` | over the per-credential limit; honour `Retry-After` |

## Limits

- **Rate**: `SKEIN_API_RATE_LIMIT` requests per minute per credential (default 60; trace
  records have their own bucket). Counted in memory by the control plane process.
- **Size**: request bodies up to 64 KiB; field limits above.
- **Idempotency**: `(source, external_id)` per credential, enforced by a unique index, so
  two concurrent retries still produce one ticket.

## Who is the requester, who can see a ticket, and how does one withdraw it

Stated plainly, because these are the questions every integration asks:

- **The requester is whoever the assistant says.** Skein never authenticates end users; the
  assistant vouches for them and the `requester` string is recorded as-is (it shows up in
  the console and the audit trail). If that matters, make the assistant authenticate its
  users first.
- **Visibility is by team, not by requester.** Every person who can read the console sees
  every ticket; there are no per-ticket ACLs. Assistants see only their own tickets. Do not
  put secrets or personal data in a ticket that the whole team should not see.
- **Withdrawal is a human decision.** Assistants cannot delete or cancel tickets — that is
  on purpose; a request that turned out to be noise is rejected by a person (the ticket
  stays, with its history). If you need to supersede a request, submit a new one and say so
  in the description.
- **Everything an assistant does is audited** as `assistant:<name>`, including the ticket
  id and the source id it used.

## Callbacks

- Set `callback_url` on the credential (console: **助手 → 回调**, or
  `skein assistant callback <name> --url …`). Status changes post `ticket.status` events
  with `public_status` in `received` / `working` / `fixed` / `need_info`.
- Signature: `X-Skein-Webhook-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>`,
  timestamp in `X-Skein-Webhook-Timestamp`. Reject anything older than a few minutes and
  compare in constant time.
- Delivery is best-effort: one attempt with a 3-second timeout, failures logged
  (`webhook ... failed`) and visible in the control plane's log, **no retries** in this
  version. Treat the callback as a hint and use `GET /api/v1/tickets/{ref}` as the source of
  truth.
- Rotate the secret with `skein assistant callback <name> --url <same> --new-secret`.

## Where the key comes from

Three equivalent ways; pick per taste and per deployment:

1. **Console** — 运行 → 助手 → 签发凭证 (admins and owners).
2. **CLI / API** — `skein assistant issue|rotate|revoke|callback`; the same endpoints under
   `/internal/assistants` for scripts (master token). Keys are stored as SHA-256 only, so
   there is nothing to recover; if one is lost, rotate it.
3. **Declared in the environment file** — `SKEIN_ASSISTANTS=[{"name": …, "purpose": …,
   "key": "skein_at_…"}]`. Declared credentials follow the file: change the key to rotate,
   drop the entry to revoke. Credentials issued through the console are left alone. Useful
   when the same file provisions both the control plane and the assistant.

A key is shown exactly once; `skein assistant list` never prints it. Revoking keeps the
row (and its name) for the audit trail but the key and callback stop working immediately.

## What this API is not

- Not a general-purpose ticket API: no updating, commenting, assigning, or deleting.
- Not a channel to operators: to ask a human to look at something, submit a ticket with the
  reason in the description.
- Not authenticated per end user; see "who is the requester" above.
