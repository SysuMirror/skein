# Console

> Vue 3 + naive-ui; `services/console/dist` is served by the control plane at `/console/`.

## Pages

Three main surfaces plus an agent for admins, and an assistants page for admins and owners.
The work surfaces are defined in [product.md](product.md).

| Route | Page | Data | What you can do |
| --- | --- | --- | --- |
| `/today` | Today | `/internal/stream` | The deck, the loop ribbon, accept/reject, see what is stuck |
| `/agent` | Agent | `/internal/agent/chat` | Admins: ask and act in natural language |
| `/tickets`, `/tickets/:id` | Tickets | `/internal/tickets`, `/internal/tickets/{id}` | List; file a ticket; dispatch; accept/reject; detail drawer with the delivery card |
| `/fleet` | Operations · overview | `/internal/topology` | Force-directed map (nodes / tickets / sessions) and the recent-dispatch table |
| `/nodes`, `/nodes/:id` | Operations · nodes | `/internal/nodes` | Status, platforms, capacity, last heartbeat |
| `/workers`, `/workers/:id` | Operations · workers | `/internal/sessions` | Session list; transcript timeline |
| `/events` | Operations · events | `/internal/events` | Live stream, admission verdicts, grouped by thread |
| `/calls` | Operations · sessions | `/internal/llm/sessions`, `/internal/llm/calls` | Each model call: input, output, tokens, latency |
| `/signals` | Operations · signals | `/internal/signals` | Dispatch timeouts, failed jobs, offline nodes |
| `/assistants` | Operations · assistants | `/internal/assistants` | Issue, rotate, revoke credentials; set callbacks |
| `/about` | About | — | What this is |

Interaction conventions:

- **The delivery card** (ticket drawer) shows the fix branch, the target branch, commits,
  the diffstat and two links: the draft PR and the test environment (`SKEIN_TEST_ENVS`;
  hidden when not configured).
- **Recent dispatches** (operations overview) lists time, ticket, platform, node, state and
  detail, with failed rows highlighted — a failed dispatch has to be visible without asking.
- **Dispatch asks for a platform** the first time; the choice is remembered per ticket for
  the session. `platform_required`, `no_node`, `already_dispatched` and
  `node_not_connected` are reported as messages instead of silently doing nothing.
- **Filing a ticket** is open to every signed-in account (member and above); dispatching is
  not.
- Deep links work for tickets, workers and nodes.

## What each role sees

The console hides what a role cannot do; the server enforces it regardless.

| | member | admin | owner |
| --- | --- | --- | --- |
| Everything read-only | ✓ | ✓ | ✓ |
| File a ticket | ✓ | ✓ | ✓ |
| Dispatch / accept / cancel | | ✓ | ✓ |
| Agent | | ✓ | ✓ |
| Assistants page | | ✓ | ✓ |
| User management (CLI) | | | ✓ |

## Live updates

`GET /internal/stream` computes a snapshot every 2 seconds (summary, signals, the last 100
tickets, topology, the last 20 events) and compares it with the previous frame: a change is
sent as `event: snapshot`, silence as `event: ping` every 15 seconds.

- The client reads the stream with `fetch` (not `EventSource`) so the session cookie and
  the abort signal work; identity comes only from the HttpOnly cookie, no token is stored
  in the browser.
- Reconnects back off 1s → 2s → … → 15s; a 401 clears the connection and shows the sign-in
  form.
- The header shows the state of this connection: “realtime” when open, “polling” when not
  (pages then poll every 10–15 s).
- Every connection starts with a full snapshot, so a reload never depends on a REST
  first-paint.

## Sign-in

The app asks `GET /api/session` what this deployment uses and renders that form:

| Response | Mode | What the landing page shows |
| --- | --- | --- |
| `{"mode": "token"}` | token | a field for the master token; it is exchanged for a session cookie and forgotten |
| `{"mode": "local"}` | local accounts | email + password |
| `{"mode": "sso", "provider": "…"}` | external provider | email + password |
| 503 | provider unreachable | keeps the signed-in state it already had; shows a degraded banner |
| 403 with `not_internal` / `not_admin` / `account_disabled` | policy | a message naming the reason |

An expired session is treated as signed out. See [authentication.md](authentication.md).

## Tech choices

| Item | Choice | Note |
| --- | --- | --- |
| Framework | Vue 3 + Vite + TypeScript | `vue-tsc` typechecks during `npm run build` |
| Components | naive-ui | |
| State | Pinia | `store.ts` holds the session, role and permissions |
| Graph | hand-drawn force layout (`MeshGraph.vue`) | data assembly separate from drawing |
| Live | SSE with polling fallback | above |

## Visual and motion

Restrained, with one exception: the Today page may visualise the whole state (the radar and
the loop ribbon) because it is the one page that must be understood at a glance. Every dot
and every stage maps to a real object. No 3D, particles or parallax anywhere else; motion
only where state changes.

- All looping animation respects `prefers-reduced-motion: reduce`.
- With no data the radar and ribbon degrade quietly (empty rings, zeros) — never demo data.
- UI copy is Chinese for now; identifiers appear only where they help troubleshooting. Making
  the copy translatable (i18n extraction) is a known follow-up.

## Build and smoke

```bash
cd services/console && npm ci && npm run build     # → dist/, mounted by the control plane
node deploy/scripts/console-smoke.mjs               # smoke test (control plane running)
```
