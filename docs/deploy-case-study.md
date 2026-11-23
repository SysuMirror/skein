# Case study: one control plane, one container node, eight repositories

> The configuration of one real deployment, with every domain, account and path replaced by
> a placeholder. It shows how the parts fit together; it is not the only way.

## The situation

A small team maintains a handful of web projects (a front end, a back end, several
stand-alone services) and wants:

- feedback from users, in the community or in the product, to become tickets that a coding
  agent fixes;
- the agent to push a branch and open a draft PR, with **human acceptance** before anything
  enters the release flow;
- maintainers to sign in once and see progress, accept work and talk to the agent.

## Topology

```text
                    ┌──────────────────────── control plane host ─────────────────────────┐
browser ─ https://skein.example.com/console/ ─ nginx ─ control plane 127.0.0.1:7800 ─ SQLite + blobs │
                    │      │                                                          │
                    │      └── /api/agent/channel (WebSocket)                           │
                    └── /api/v1/tickets ←── assistants / internal systems                │
                                    │
                    ┌───────────────┴─────────── execution node (one container) ────────┐
                    │  node-agent ── one worker per ticket (own worktree + own HOME)     │
                    │  repos/project-a  repos/project-b  … (one writable deploy key each)│
                    └───────────────────────────────────────────────────────────────────┘
```

Only the control plane is exposed over HTTPS; nodes **dial out**, so no inbound port is
needed on them.

## Control plane

Environment file (`/etc/skein/env`, mode 600):

```ini
SKEIN_DB=/var/lib/skein/skein.db
SKEIN_BLOB_ROOT=/var/lib/skein/blobs
SKEIN_HOST=127.0.0.1
SKEIN_PORT=7800
SKEIN_CONTROL_URL=https://skein.example.com
SKEIN_TOKEN=<32 random bytes>

# console Agent model
SKEIN_LLM_BASE=https://api.example-llm.com/v1
SKEIN_LLM_KEY=<key>
SKEIN_AGENT_MODEL=<model>

# sign-in with the team's existing provider (UniSSO in this case)
SKEIN_SSO_BASE=https://sso.example.com
SKEIN_SSO_CLIENT_ID=<client id>
SKEIN_SSO_CLIENT_SECRET=<client secret>
SKEIN_SSO_REQUIRE_INTERNAL=1        # team members only

# draft PRs after a worker pushes a branch
SKEIN_GITHUB_TOKEN=<fine-grained token: Contents read + Pull requests read/write>
SKEIN_GITHUB_REPOS={
  "project-a":       {"slug": "example-org/project-a", "base": "develop"},
  "project-a-forum": {"slug": "example-org/project-a", "base": "develop"},
  "project-b":       {"slug": "example-org/project-b", "base": "develop"}
}
SKEIN_TEST_ENVS={
  "example-org/project-a": "https://test-a.example.com/",
  "example-org/project-b": "https://test-b.example.com/"
}

# the external watchdog
SKEIN_WATCHDOG=1
SKEIN_WATCHDOG_TICKETS=1
SKEIN_WATCHDOG_INTERVAL=300

# the first assistant: the forum bot
SKEIN_PUBLIC_URL=https://skein.example.com
SKEIN_ASSISTANTS=[{"name":"forum-bot","purpose":"turns forum posts into tickets","key":"skein_at_…"}]
```

Configuration lessons, all learned the hard way:

- **One repository can answer to several platform names.** `project-a` and `project-a-forum`
  point at the same repository through different intakes (in-product feedback vs forum
  posts), which keeps the console able to show where a ticket came from.
- **`SKEIN_GITHUB_TOKEN` must be scoped to every repository that may receive a PR.** A
  private repository outside the token's scope yields `422 not all refs are readable` — the
  branch is pushed, the PR is not created. The audit table records the reason as `pr_error`.
- **Base branches**: every repository states `base` explicitly. Projects whose integration
  flow is "merge to develop, test environment redeploys" use `develop`; repositories without
  it leave `base` empty and get their default branch.
- **`SKEIN_TEST_ENVS` is keyed by repository slug**; the control plane maps it to platform
  names at startup so the delivery card can link "test environment ↗". Do not configure it
  for projects without a web UI — it would be a dead link.
- **`SKEIN_SSO_REQUIRE_INTERNAL=1`** keeps the console to team members. A deployment that
  serves the public sets it to `0` and creates `member` accounts for people who should see
  the console.
- **Admins provision themselves on first sign-in** (the provider reports the admin flag);
  members are created by an owner: `skein user create --email … --role member`. See
  [authentication.md](authentication.md).

The release flow (a team convention, not enforced by Skein):

```text
worker pushes a fix branch → control plane opens a draft PR against develop
→ merging develop redeploys the test environment → a person verifies there
→ a develop → main PR, approved by a person → production
```

## Execution node

The node is a long-lived container (no systemd) with `~/node-agent.env`:

```ini
CONTROLLER_URL=wss://skein.example.com/api/agent/channel
SKEIN_CONTROL_URL=https://skein.example.com
SKEIN_TOKEN=<the same master token>
NODE_ID=node-1
NODE_NAME=primary node
PLATFORMS=project-a,project-a-forum,project-b,project-c,project-d,project-e
REPO_MAP='{"project-a":"/home/agent/repos/project-a","project-a-forum":"/home/agent/repos/project-a","project-b":"/home/agent/repos/project-b", …}'
MAX_WORKERS=2
CLAUDE_BIN=/home/agent/.local/node/bin/claude
PUSH_BRANCH=1
```

- **One writable deploy key per repository**, selected in each clone with
  `core.sshCommand`. Otherwise git uses the default `id_ed25519` and pushes fail silently
  for the worker.
- **`MAX_WORKERS` bounds concurrency and therefore model spend.** A stuck worker keeps
  spending, which is why cancelling kills the process group (SIGTERM, SIGKILL after
  10 seconds) and cleans the work directory.
- **Quote the `REPO_MAP` value**; see [deploy/pod/README.md](../deploy/pod/README.md) for
  why and how to fix it when it is wrong.
- The platforms the control plane can dispatch to are exactly what online nodes report in
  `PLATFORMS`. Changing the env requires a node-agent restart.
- Containers usually cannot create mount namespaces, so the container itself is the
  isolation boundary: a dedicated container, not co-hosted with other services.

## Test-environment deployment (the part outside Skein)

Skein's responsibility ends at the draft PR. Redeploying the test environment after a merge
to `develop` is the team's deploy platform's job: a code-hosting webhook, a deployment
record matched by repository and branch, then `git fetch` inside the node, an incremental
build, a restart and a health check, rolling back to the last good version on failure.

Measured optimisations (hot deploy from ~20–28 s down to ~12–14 s):

- fetch only the target branch, never `fetch --all`;
- every `exec` into the container costs ~0.3 s; batch small writes into one;
- for services with a health check, shorten the process manager's start confirmation and
  trust the health check.

## Operations notes

- **Upgrade**: pull, rebuild the console, restart the control plane; for a node, update
  `node-agent.mjs` and restart it (pushed branches and PRs are unaffected).
- **Backup**: the control plane backs up SQLite daily; a node holds nothing that cannot be
  recreated (repositories can be cloned again, branches live on the remote).
- **Debugging dispatches**: the "recent dispatches" table shows why a dispatch failed; PR
  failures are `pr_error` in the audit table; worker failures (model quota, repository
  permissions) show on the delivery card and in the node log.
- **Tokens and keys** live only in the control plane's environment file and the node's env
  file (600); never in the repository or the docs.
