# Contributing

Thanks for your interest in Skein (雁阵 — the formation geese fly in; a *skein* is also a
coil of thread, which is what the core unit, a Thread, is named after).

This page covers the development loop, the repository layout, the commit and pull-request
conventions, and how changes flow through the long-lived branches.

## Ground rules

- Keep the core deployment-agnostic. No site names, domains, IP addresses or accounts in
  code, examples or docs — use `example.com` style placeholders.
- A login method goes through a provider (`services/control/src/skein/auth_providers/`) and
  answers only "who is this". What someone may do is decided by the permission matrix in
  `skein/access.py`, never by the provider.
- Capabilities only one deployment needs go through an optional integration
  (`skein/integrations/`) or one of the documented extension points
  (`docs/extending.md`); the core must stay fully usable with zero configuration.
- A component nobody consumes does not get built.

## Development setup

Requirements: Python 3.10+ and Node.js 22+.

```bash
make install          # control plane, editable, with dev extras
make test             # pytest
make lint             # ruff
cd services/node-agent && npm ci && npm test
make build-console    # vue-tsc typecheck + vite build
make run-control      # zero-config token mode on http://127.0.0.1:7800
```

The control plane serves the console at `/console/` itself, so build it once and open
<http://127.0.0.1:7800/console/>; in token mode the sign-in "token" is `dev-token`.
Every deployment switch is listed with an explanation in `.env.example`.

For local work against a fake identity provider, see `deploy/dev/README.md`.

## Repository layout

| Path | What lives there |
| --- | --- |
| `services/control/` | Control plane (Python/FastAPI): tickets, scheduling, the node channel, the console API |
| `services/console/` | Console (Vue 3 + naive-ui) |
| `services/node-agent/` | Node agent (Node.js): reverse channel, worktree isolation, agent sessions, transcript sync |
| `deploy/` | systemd units, node installer, nginx template, local dev helpers |
| `docs/` | Design and operator documentation (English; translations live in `docs/zh-CN/`) |

## Commits and pull requests

- Commit messages follow Conventional Commits: `type(scope): summary` with types `feat`,
  `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`, `style`. One logical
  change per commit; keep refactors separate from behaviour changes.
- Work goes through a pull request. The base branch is `develop`: PRs into `develop` need
  no approval and deploy to the test environment. Merging `develop` into `main` needs one
  approval and is what a release is (see `docs/release-workflow.md`).
- A PR should include the tests for what it changes, a sentence on the failure mode it
  fixes, and before/after screenshots for UI changes.
- PRs opened by a Skein worker (the platform dispatches coding agents, and they open draft
  PRs themselves) carry the ticket code in the title; a human always reviews and merges.

## Tests

Run before pushing:

```bash
make test && make lint
cd services/node-agent && npm test
make build-console
```

- Control plane: pytest under `services/control/tests/`. A new endpoint needs a test for
  the permission-matrix cell it lives in (who may call it, and who gets a 403) plus its
  failure path — upstream unreachable, bad input, duplicate submission.
- Anything that talks to an external service (identity provider, code hosting, model
  gateway, webhooks) is tested against a fake. **Tests must pass without network access**,
  and CI has none beyond the checkout.
- Node agent: `node --test`, no cluster required.
- Schema changes: bump `SCHEMA_VERSION` in `skein/db.py`, keep migrations additive (new
  tables, new columns with defaults), and add a test that upgrades a previous schema with
  data in it.

## Documentation

- `docs/` is English and authoritative. Chinese translations live in `docs/zh-CN/` and may
  lag; they say so at the top.
- New configuration keys are documented in `.env.example` and `docs/configuration.md` in
  the same PR that introduces them, and their default must reproduce the behaviour from
  before the key existed.

## Updating repository metadata

The GitHub description and topics are mirrored in [`docs/repo-metadata.md`](docs/repo-metadata.md).
That file is the source of truth; change it there first, then apply it:

```bash
gh api -X PATCH repos/OWNER/REPO -f description="$(…)"         # from docs/repo-metadata.md
gh api -X PUT   repos/OWNER/REPO/topics  -f names[]="ai-agents" …
```

Keep the description under ~120 characters and the topics lowercase and hyphenated, and
add only topics that describe what is actually in the repository.

## Security

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md). Never commit
credentials; deployment secrets belong in the server's environment file (mode 600).
