# Configuration reference

Everything is read from the environment by one module (`services/control/src/skein/config.py`).
`.env.example` at the repository root is the copy-paste version with short comments; this
page is the reference. Two rules apply to every key:

- **A new key's default reproduces the behaviour from before it existed.** Upgrade by
  pulling the code; the environment file does not have to change.
- **Nothing secret belongs in the repository.** Deployment credentials live in one file the
  operator can read (mode 600), e.g. `/etc/skein/env`, loaded by systemd's
  `EnvironmentFile=` or docker compose's `env_file`.

Booleans accept `1/0`, `true/false`, `yes/no`.

## Core

| Key | Default | Meaning |
| --- | --- | --- |
| `SKEIN_TOKEN` | empty | The master token: machines use it as `X-Skein-Token`, nodes to register, the CLI to manage. In token mode a person signs in with it too. Empty means nobody can authenticate. |
| `SKEIN_DB` | `data/skein.db` | SQLite database |
| `SKEIN_BLOB_ROOT` | `data/blobs` | Content-addressed transcript files |
| `SKEIN_HOST` / `SKEIN_PORT` | `127.0.0.1` / `7800` | Listen address |
| `SKEIN_CONTROL_URL` | empty | The control plane's own address (the Watchdog CLI uses it) |
| `SKEIN_CONSOLE_DIR` | empty | Directory whose `dist/` holds the console build. Empty = `services/console` next to the source (set it for pip installs and images) |
| `SKEIN_LOG_FORMAT` | `text` | `json` writes one object per line (control plane and uvicorn) |

## Login

See [authentication.md](authentication.md) for what these mean.

| Key | Default | Meaning |
| --- | --- | --- |
| `SKEIN_SSO_PROVIDER` | `unisso` | Provider name, must be registered in `auth_providers/__init__.py` |
| `SKEIN_SSO_BASE` | empty | Provider base URL |
| `SKEIN_SSO_CLIENT_ID` / `SKEIN_SSO_CLIENT_SECRET` | empty | Provider credentials; **both** set enables the provider (and with it `sso` mode) |
| `SKEIN_SSO_COOKIE_NAME` | empty | Session cookie name; empty = the provider's default |
| `SKEIN_COOKIE_DOMAIN` | empty | Cookie domain: unset = what the provider answers; `host` = no Domain (plain-HTTP debugging); anything else = explicit |
| `SKEIN_SSO_REQUIRE_INTERNAL` | `1` | Require the provider's "internal member" flag (kept for compatibility; set `0` when the deployment serves the public) |
| `SKEIN_LOCAL_LOGIN` | `0` | Enable local accounts — only when no provider credentials are set (setting both refuses to start) |
| `SKEIN_AUTH_AUTO_ROLE` | empty | Provision unknown non-admin identities as this role; empty = don't (`member` is the only accepted value) |
| `SKEIN_SESSION_TTL_HOURS` | `168` | Lifetime of a Skein-issued session |
| `SKEIN_SESSION_COOKIE_NAME` | `skein_session` | Name of the session cookie |
| `SKEIN_SESSION_COOKIE_SECURE` | `1` | `Secure` flag on that cookie; set `0` only for plain-HTTP demos |

## Assistants and the public API

See [assistants.md](assistants.md).

| Key | Default | Meaning |
| --- | --- | --- |
| `SKEIN_PUBLIC_URL` | empty | Public base URL used in ticket links and callbacks; empty = no links in API responses |
| `SKEIN_API_RATE_LIMIT` | `60` | Requests per minute per assistant credential |
| `SKEIN_API_TRACE` | `0` | Mount `POST /api/v1/llm/calls` so assistants can report model-call traces |
| `SKEIN_API_DOCS` | `0` | Expose `/docs` and `/openapi.json` |
| `SKEIN_ASSISTANTS` | `[]` | JSON list of credentials to declare: `[{"name","purpose","key","callback_url"?}]`. Declared ones follow the file (change the key to rotate, remove the entry to revoke); console/CLI-issued ones are untouched |

## Repositories and pull requests

| Key | Default | Meaning |
| --- | --- | --- |
| `SKEIN_PLATFORM_REPOS` | `{}` | JSON platform → local repository path, sent with a dispatch; not needed when nodes configure `REPO_MAP` |
| `SKEIN_FORGE` | `github` | Code-hosting client (registered in `forge.py`) |
| `SKEIN_GITHUB_TOKEN` | empty | Token to open draft PRs. Fine-grained: `Contents: Read`, `Pull requests: Read and write`, and every repository that may receive a PR must be in scope — otherwise you get `422 not all refs are readable` (the branch is pushed; the PR is not opened; the audit row `pr_error` says why) |
| `SKEIN_GITHUB_REPOS` | `{}` | JSON platform → `"owner/repo"` or `{"slug": "owner/repo", "base": "develop"}`; the fallback when the worker's remote is not a GitHub URL |
| `SKEIN_GITHUB_BASE` | empty | Global PR base branch. Resolution: repository `base` → this → automatic (`develop` if the repository has it, else the default branch) |
| `SKEIN_GITHUB_API` | `https://api.github.com` | API base (GitHub Enterprise: `https://github.example.com/api/v3`) |
| `SKEIN_GITHUB_PR_DRAFT` | `1` | Open as draft |
| `SKEIN_TEST_ENVS` | `{}` | JSON repository slug → test environment URL, shown on the ticket's delivery card |

## Console Agent

| Key | Default | Meaning |
| --- | --- | --- |
| `SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` | empty | OpenAI-compatible gateway used by the console Agent; unset = the Agent answers 503, everything else works |
| `SKEIN_AGENT_MODEL` | `deepseek-v4-flash-vision-exp` | Model name passed to the gateway |
| `SKEIN_ROUTE_MODEL` | `route` | Model name used by the Watchdog's prompt proposals |

## Watchdog and scheduled jobs

| Key | Default | Meaning |
| --- | --- | --- |
| `SKEIN_WATCHDOG` | `0` | Run the Watchdog loop (separate process/unit) |
| `SKEIN_WATCHDOG_AUTO` | `0` | Propose prompt revisions. **Do not enable** — revisions are not wired to workers and cannot be promoted (see [architecture.md](architecture.md)) |
| `SKEIN_WATCHDOG_TICKETS` | `0` | Turn fresh signals into tickets (needs `SKEIN_CONTROL_URL` + `SKEIN_TOKEN`) |
| `SKEIN_WATCHDOG_DISPATCH` | `0` | Dispatch those tickets immediately |
| `SKEIN_WATCHDOG_INTERVAL` | `300` | Poll interval, seconds |
| `SKEIN_DAILY_TICKET_HOUR` / `SKEIN_TICKET_DAILY_CAP` | `-1` / `5` | Daily review ticket (hour, `-1` = off) and the daily cap for self-maintenance tickets |
| `SKEIN_DRIVER` | `0` | Run the scheduled-job driver |
| `SKEIN_JOBS_FILE` | empty | JSON with `schedules` and `commands` (see [deployment.md](deployment.md)) |
| `SKEIN_SCHEDULES` / `SKEIN_JOB_COMMANDS` | empty | The same as JSON strings instead of a file |
| `SKEIN_JOB_TIMEOUT` | `14400` | Per-job timeout, seconds |

## Optional integration: chat proxy

Mounted only when configured; without it the routes do not exist.

| Key | Meaning |
| --- | --- |
| `SKEIN_QA_CHAT_UPSTREAM` | An external chat service to put behind the control plane (`/api/v1/agent/*`, `/task/*`). The caller's own credentials are forwarded, cookies are not |
| `SKEIN_ASSERTION_KEY` | Signs a short-lived `X-Skein-User` identity header for that upstream; must match the upstream's verification key |

> Removed in 0.1.0: `SKEIN_INTAKE_NOTIFY_URL` and `SKEIN_DISPATCH` (the first is replaced by
> signed assistant webhooks, the second never had an effect). Setting them is harmless and
> ignored.

## Node agent

Documented in [`deploy/pod/node-agent.env.example`](../deploy/pod/node-agent.env.example):
`CONTROLLER_URL` (or `SKEIN_CONTROL_URL`, from which it is derived), `SKEIN_TOKEN`
(registration and transcript upload; `AGENT_TOKEN` only overrides the registration token),
`NODE_ID`, `NODE_NAME`, `PLATFORMS`, `REPO_MAP`, `MAX_WORKERS`, `CLAUDE_BIN`,
`PUSH_BRANCH`, `SANDBOX_ENABLED`, `WORKER_ALLOWED_TOOLS`, `IS_SANDBOX`,
`WORKER_FULL_BYPASS`.
