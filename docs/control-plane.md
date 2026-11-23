# Control plane (`services/control`)

One process, one writer. It knows nothing about a particular deployment: no site names, no
identity provider's domain, no one code host — those come in through providers, integrations
and configuration.

## Modules

| Module | Responsibility |
| --- | --- |
| `db.py` | SQLite (WAL), schema version and additive migrations, audit rows |
| `access.py` | Roles and the permission matrix; one table, documented from code |
| `auth.py` | `Principal`, the `IdentityProvider` protocol, session verification, rejection codes |
| `auth_providers/` | Login methods: `builtin.py` (token, local), `unisso.py`; registry |
| `accounts.py` | Users, identities, roles, JIT provisioning, last-owner guard |
| `sessions.py`, `passwords.py` | Skein-issued sessions (SHA-256 stored); scrypt passwords |
| `assistants.py` | Assistant credentials; env declaration; callback targets |
| `public_api.py` | `/api/v1` for assistants (idempotent submit, own tickets, optional traces) |
| `assistants_api.py` | Management endpoints for users and assistant credentials (the CLI's API) |
| `notify.py` | Signed status webhooks (`Channel` + `Notifier`) |
| `admission.py` | Deterministic admission (ignore / record / process) |
| `store.py` | Threads, tickets, dedup keys |
| `channel.py`, `scheduler.py` | Node channel; node selection, capacity, dispatch state |
| `agent.py` | Console Agent (planning + tools + model-call traces) |
| `forge.py` | Draft PRs through a `ForgeClient`; base-branch resolution |
| `prompts.py` | Prompt revisions with stable/canary pointers (not yet wired to workers) |
| `watchdog.py`, `watchdog_agent.py` | Facts → signals → optional tickets; daily review |
| `driver.py`, `runs.py` | Scheduled jobs and run records |
| `session_store.py` | Transcript index + content-addressed blobs, per-turn idempotence |
| `llm_trace.py` | Model-call records (console Agent, assistants with `SKEIN_API_TRACE`) |
| `metrics.py`, `logbuf.py`, `middlewares.py` | Prometheus counts, log ring and JSON logs, body limit and same-site guard |
| `config.py` | Every environment key, read in one place |
| `__main__.py`, `cli.py` | `python -m skein [serve | user | assistant | version]` |
| `integrations/` | Optional integrations (chat proxy), mounted only when configured |

## Routes

`require(…)` in the table is the permission the route declares (see
[authentication.md](authentication.md)); “machine” means the master token is enough,
“signed-in” means a person.

| Method | Path | Requires | Purpose |
| --- | --- | --- | --- |
| GET | `/health/live`, `/health/ready` | — | Liveness; database readable |
| GET | `/metrics` | — | Prometheus counts (no content) |
| GET | `/api/session` | — | Login mode; with a session, the user and permissions |
| POST | `/api/session/login` | — | Sign in (token / email+password depending on mode) |
| POST | `/api/session/logout` | — | Revoke the session and clear cookies |
| POST/GET | `/api/v1/tickets`, `/api/v1/tickets/{ref}`, `/api/v1/me` | assistant key | Public API — [assistants.md](assistants.md) |
| POST | `/api/v1/llm/calls` | assistant key | Model-call traces (only with `SKEIN_API_TRACE=1`) |
| GET | `/internal/summary`, `/internal/tickets`, `/internal/tickets/{id}`, `/internal/events`, `/internal/threads/{id}`, `/internal/sessions*`, `/internal/topology`, `/internal/nodes`, `/internal/signals`, `/internal/llm/*`, `/internal/logs` | `read` | Console queries |
| GET | `/internal/stream` | `read` | SSE snapshot |
| POST | `/internal/tickets` | `ticket.file` (dispatch needs `ticket.operate`) | File a ticket; optionally dispatch |
| POST | `/internal/intake` | `ticket.operate` | Threaded intake from an internal system |
| POST | `/internal/tickets/{id}/dispatch` | `ticket.operate` | Dispatch to a node |
| POST | `/internal/tickets/{id}/cancel` | `ticket.operate` | Cancel the live dispatch |
| POST | `/internal/tickets/{id}/review` | `ticket.operate` | approve / reject |
| POST | `/internal/agent/chat`, GET `/internal/agent/thread/{id}` | `agent.chat`, signed-in | Console Agent |
| POST | `/internal/worker/turn`, `/internal/llm/calls` | `ops.ingest` | Transcript turns, model-call records |
| GET/POST/PATCH/DELETE | `/internal/users*` | `user.read` / `user.manage` | Users and roles |
| GET/POST/PATCH/DELETE | `/internal/assistants*` | `assistant.manage` | Assistant credentials |
| WS | `/api/agent/channel` | token in the first message | Node channel |
| GET/POST | `/api/v1/agent/*`, `/task/*` | the upstream's own auth | Chat proxy integration (optional) |

Optional routes do not exist at all when their integration is not configured.

## Data

`events`, `threads`, `tickets`, `assignments`, `runs`, `prompt_revisions`,
`prompt_pointers`, `audit`, `kv`, `sessions`, `session_turns`, `dispatches`, `nodes`,
`llm_calls`, `agent_messages`, and since schema 13: `users`, `identities`,
`login_sessions`, `assistants`.

Schema changes bump `PRAGMA user_version` and are additive; a test upgrades a schema-12
database with data in it.

## High availability and backup

- `Restart=always` on the unit; nodes reconnect on their own; a restart loses nothing.
- One replica. SQLite with WAL is fine at this scale (a team's ticket flow), but it is a
  single write point by design; more control planes would mean Postgres, which is out of
  scope.
- Backups: `deploy/scripts/backup-skein.sh` (SQLite online backup, keeps 7 days by
  default). Back up `SKEIN_BLOB_ROOT` with it.
