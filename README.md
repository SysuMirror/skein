# Skein

[English](README.md) | [简体中文](README.zh-CN.md)

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Skein** (雁阵, "the formation geese fly in"; Shenzhen is also the Chinese name for a *skein*
of thread, which is what the core unit, a Thread, is named after — the platform keeps the
English name) is a self-hosted control plane that turns tickets into agent-authored pull
requests on your own nodes.

You give it a stream of real feedback — a forum post, a chat message, a monitoring alert —
as **tickets** (工单, the unit of work). It dispatches each ticket to a coding agent running
on a machine you control (a **node**), where an isolated **worker** edits a git worktree,
commits, and pushes a fix branch. The control plane opens a draft pull request and puts the
result in front of a person. A human accepts or rejects it. Nothing merges itself.

## The problem it solves

- **Feedback scatters.** Requests and bugs live in forum threads, chat logs and someone's
  memory. Copies drift, and the same problem is reported three times.
- **Agent work is invisible.** Something edited your code — which branch, which commits,
  what changed, did it even run? Without a record, you cannot accept it with confidence.
- **Agents need to be somewhere.** They need a machine, a repository, credentials, an
  isolated place to work, and a way to stop. Doing that by hand for every request does not
  scale past the first afternoon.

Skein is the middle part: tickets with provenance, isolated execution, delivered results,
and a human gate.

## How it works

```text
feedback / events ──► ticket ──► dispatched to a coding agent on a node ──► fix branch + draft PR ──► human review
        ▲                                                                                                   │
        └────────────────────────────── assistants, the console, the watchdog ◄──────────────────────────────┘
```

- **Ticket** — the unit of work, tied to where it came from (source + external id) and
  deduplicated on retry.
- **Node** — a machine that dials *out* to the control plane (no inbound port) and has clones
  of the repositories it serves.
- **Worker** — one coding-agent session per ticket, in its own `git worktree` and its own
  HOME, killed and cleaned up when the ticket ends.
- **Dispatch** — assigning a ticket to a platform (a repository a node serves); the control
  plane picks a node with capacity, the worker does the rest.
- **Draft PR** — the control plane opens it against the integration branch when the worker
  pushes. Skein never merges.

## Quickstart

**Watch the whole loop in five minutes**, with a mock coding agent and no model key:

```bash
git clone https://github.com/SysuMirror/skein.git && cd skein
docker compose up --build
# console: http://localhost:7800/console/   token: demo-master-token
docker compose logs -f demo                  # a ticket going through the whole pipeline
```

**Or run it from source** (Python 3.10+, Node 22+):

```bash
make install && make build-console && make run-control
# console: http://127.0.0.1:7800/console/   token: dev-token
```

**Then connect a real node** — a machine with a coding agent (Claude Code by default) and a
clone of your repository — and do one for real:

```bash
bash deploy/pod/setup.sh                  # on the node: checks, ~/repos, a starting env file
bash deploy/pod/install-supervisor.sh     # keep the node agent running
```

Full walkthrough: [docs/quickstart.md](docs/quickstart.md). Node details:
[deploy/pod/README.md](deploy/pod/README.md).

**Connect an assistant** so the loop does not start with someone typing:

```bash
skein assistant issue --name forum-bot --purpose "turns forum posts into tickets"
curl -s -X POST $SKEIN_URL/api/v1/tickets \
  -H "Authorization: Bearer $SKEIN_ASSISTANT_KEY" -H "Content-Type: application/json" \
  -d '{"title":"Cannot upload avatars","source":"forum","external_id":"post:42"}'
```

Five-minute tutorial and the API reference: [docs/assistants.md](docs/assistants.md).

## Core concepts

| Concept | What it is |
| --- | --- |
| **Ticket** | A request, with its source, requester, thread and status (`staged → assigned → running → review → done / rejected`). |
| **Thread** | The conversation a ticket came from; follow-up messages merge into the active ticket. |
| **Platform** | A repository a node serves. You choose the platform when you dispatch. |
| **Node** | A machine with the node agent, repository clones and a coding agent. |
| **Worker** | One agent session on a node, one ticket, own worktree and HOME, auto-cleanup. |
| **Dispatch** | Ticket → node assignment; carries the platform, state, worker and (later) the PR. |
| **Assistant** | An external program with its own key that submits tickets through `/api/v1`. |
| **Watchdog** | An optional loop outside the control plane that turns facts (dispatch timeouts, failed jobs) into signals and, if you want, tickets. |

## Architecture

```text
services/control     control plane (Python/FastAPI, one process, the only writer)
  ├─ tickets, threads, admission, audit        SQLite (one file) + content-addressed blobs
  ├─ node channel (WebSocket)                  nodes dial in; dispatch, progress, results
  ├─ /api/v1 (assistants), /internal (console) both authenticated, never interchangeable
  └─ console served at /console/
services/console     console (Vue 3 + naive-ui): Today, Tickets, Operations, Agent, Assistants
services/node-agent  node agent (Node): reversed channel, worktree isolation, agent sessions
deploy/              systemd units, node installer, docker images, nginx template
docs/                operator and design docs (English; translations in docs/zh-CN)
```

Three sources of truth, never merged: the domain store (SQLite), the session store
(transcripts as content-addressed files), and the code (branches on your code host). Read
[docs/architecture.md](docs/architecture.md) before changing how they fit together.

## Configuration overview

Every switch is optional; with none set, the control plane runs in **token mode** (sign in
with the master token) and the core loop works. The ones most deployments touch:

| Key | What for |
| --- | --- |
| `SKEIN_TOKEN` | The master token: machines, nodes, the CLI; the sign-in token in token mode |
| `SKEIN_SSO_*` / `SKEIN_LOCAL_LOGIN` | Sign-in with an external provider, or with local accounts |
| `SKEIN_GITHUB_TOKEN` + `SKEIN_GITHUB_REPOS` | Open draft PRs after a worker pushes |
| `SKEIN_TEST_ENVS` | Link the test environment on the delivery card |
| `SKEIN_ASSISTANTS`, `SKEIN_PUBLIC_URL` | Assistant credentials and their ticket links |
| `SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` | The console Agent's model gateway |
| `SKEIN_WATCHDOG*` | The optional external control loop |

The annotated list is [`.env.example`](.env.example); the reference is
[docs/configuration.md](docs/configuration.md).

## Documentation

Start at [docs/README.md](docs/README.md). Good entry points:

- [docs/quickstart.md](docs/quickstart.md) — demo, source, a real node, an assistant
- [docs/architecture.md](docs/architecture.md) — the parts and their boundaries
- [docs/authentication.md](docs/authentication.md) — login methods, roles, the permission matrix
- [docs/assistants.md](docs/assistants.md) — the public API
- [docs/deployment.md](docs/deployment.md) — install, upgrade, backup, self-checks
- [docs/configuration.md](docs/configuration.md) — every key
- [docs/extending.md](docs/extending.md) — add a login provider, code host, model gateway, notifier

Chinese translations live in [docs/zh-CN/](docs/zh-CN/README.md); English is authoritative.

## Status

Early public release (`0.1.0`). The core loop — intake, tickets, dispatch, isolated workers,
delivery, review — is implemented and runs in production deployments; so are assistant
credentials with signed callbacks, the three login methods, and per-role access. Not yet
implemented, and stated as such in the docs: wiring the Watchdog's prompt revisions into
workers, gVisor-level isolation, cross-node session continuation, webhook retries. See
[CHANGELOG.md](CHANGELOG.md) and the end of [docs/architecture.md](docs/architecture.md).

## Security

Credentials live in one environment file outside the repository. Workers never see the
control plane's token; assistant keys are separate from it; roles and permissions are
enforced on every request. Password storage, rate limits, rotation and how to report a
vulnerability: [SECURITY.md](SECURITY.md).

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) covers the development loop, tests (which must pass
without network access), commits and the `develop → main` release flow. Questions and ideas:
[SUPPORT.md](SUPPORT.md). This project follows the
[Contributor Covenant](CODE_OF_CONDUCT.md).

## License

[Apache-2.0](LICENSE).
