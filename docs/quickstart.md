# Quickstart

Three ways in, shortest first. All of them end with a ticket, a worker's fix and a human
decision — that loop is the whole product.

## A. Watch the loop in five minutes (docker compose)

```bash
git clone https://github.com/SysuMirror/skein.git && cd skein
docker compose up --build
```

That starts the control plane with the console at <http://localhost:7800/console/>, a node
running a **mock** coding agent (no model key needed), and a one-shot demo script that
submits a ticket through the public API, dispatches it and follows it to `review`:

```bash
docker compose logs -f demo
```

Open the console, sign in with the token `demo-master-token`, and watch the ticket, the
dispatch and the worker's commit. The demo tokens are public; see
[deploy/docker/README.md](../deploy/docker/README.md) before reusing anything from it.

## B. Run it from source

Requirements: Python 3.10+ and Node 22+.

```bash
make install          # control plane, editable, with dev extras
make build-console    # vue-tsc + vite → services/console/dist
make run-control      # http://127.0.0.1:7800, data under services/data/
```

Sign in with `dev-token` (token mode: nothing is configured, so the shared token is the
login). `GET /api/session` reports `{"mode": "token"}`.

Create a ticket in the console (**需求 → 建单**) or with the API, then dispatch it — but
without a node there is nothing to dispatch to, which is what B is for next.

## C. Connect a node and do a real one

A node needs a machine reachable *from* it to the control plane, Node 22+, a coding agent
(Claude Code by default) and a clone of the repository it serves. On the node:

```bash
# 1. install: creates ~/repos, checks node, writes a starting env file
bash deploy/pod/setup.sh

# 2. clone the repository the node will fix, with a deploy key that can push
git clone git@github.com:example-org/example-app.git ~/repos/app

# 3. point the agent at the control plane
cat > ~/node-agent.env <<'ENV'
CONTROLLER_URL=wss://skein.example.com/api/agent/channel
SKEIN_CONTROL_URL=https://skein.example.com
SKEIN_TOKEN=<the same master token as the control plane>
NODE_ID=node-1
PLATFORMS=app
REPO_MAP={"app":"/home/agent/repos/app"}
MAX_WORKERS=1
PUSH_BRANCH=1
ENV

# 4. keep it running across reboots and crashes
bash deploy/pod/install-supervisor.sh
```

The node shows up under **运行 → 节点** within a few seconds. Now:

1. **需求 → 建单**: "Fix the off-by-one in the pagination helper".
2. **派单**, pick the platform `app`.
3. The worker creates a worktree, edits, commits, pushes; the console shows progress and
   then the delivery card with the branch, commits and diffstat — and, with a GitHub token
   configured, a draft PR.
4. **通过 / 驳回** — the human gate. Nothing was merged; the draft PR is yours to finish.

Details and the traps are in [deploy/pod/README.md](../deploy/pod/README.md) and
[worker-runtime.md](worker-runtime.md).

## D. Connect an assistant

So that the loop does not start with someone typing: issue a key and submit requests from
the assistant that already knows about them.

```bash
skein assistant issue --name forum-bot --purpose "turns forum posts into tickets"
# → skein_at_… (shown once)

curl -s -X POST https://skein.example.com/api/v1/tickets \
  -H "Authorization: Bearer $SKEIN_ASSISTANT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"Cannot upload avatars","source":"forum","external_id":"post:42","requester":"forum:278"}'
```

Full tutorial, API reference and callbacks: [assistants.md](assistants.md).

## Where to go next

| You want to… | Read |
| --- | --- |
| Understand the pieces and their boundaries | [architecture.md](architecture.md) |
| Choose a login method, roles, first admin | [authentication.md](authentication.md) |
| Deploy for real (systemd, docker, a K8s pod) | [deployment.md](deployment.md) |
| Know what every switch does | [configuration.md](configuration.md) |
| Add a provider, code host, model gateway, notifier | [extending.md](extending.md) |
| Know what the platform is for (and not for) | [product.md](product.md) |
