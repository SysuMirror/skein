# Execution node deployment

A node is a machine or container that can run a coding agent. The `node-agent` on it dials
**out** to the control plane and, on instruction, starts one isolated worker session per
ticket locally.

## What a node needs

- Outbound access: to the control plane (WebSocket), to the code host (fetch/push branches),
  to the model gateway.
- Node 22+, `git`, a coding agent (Claude Code by default).
- One local clone per repository it serves, with **push permission** (the worker pushes a
  fix branch; the control plane opens the PR).
- The control plane's master token (`SKEIN_TOKEN`; the node registers and uploads
  transcripts with it).

## One-time preparation

```bash
bash setup.sh          # Node / coding agent / the Skein repository (the node agent itself)
```

`setup.sh` accepts `NODE_MIRROR`, `NPM_REGISTRY`, `APT_MIRROR` and `SKEIN_REPO` to override
its defaults.

### 1. Clone the repositories the node serves

Each platform is a local directory. Create one **writable** deploy key per repository and
select it in the clone — with several keys you must, or git falls back to the default
`id_ed25519`:

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/<platform>_deploy -C "skein-node <platform> rw"
# add the public key to that repository's Deploy keys, with "Allow write access"
git clone git@github.com:<org>/<repo>.git ~/repos/<platform>
git -C ~/repos/<platform> config core.sshCommand \
  "ssh -i $HOME/.ssh/<platform>_deploy -o IdentitiesOnly=yes"
git -C ~/repos/<platform> push --dry-run origin HEAD:refs/heads/_pushcheck   # verify write access
```

A worker branches off whatever branch the clone is on. For "PR → develop" flows, check the
clone out on `develop`.

### 2. The coding agent's model gateway

Put the gateway configuration in `~/.claude/settings.json` (under `env`: `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, the model variables). Each ticket gets its own HOME, and this file is
copied into it.

### 3. Write `~/node-agent.env` (mode 600)

```ini
CONTROLLER_URL=wss://skein.example.com/api/agent/channel
SKEIN_CONTROL_URL=https://skein.example.com
SKEIN_TOKEN=<the control plane's SKEIN_TOKEN>   # also the registration token by default
NODE_ID=node-1
NODE_NAME=node one
PLATFORMS=my-service,another-service
REPO_MAP='{"my-service":"/home/me/repos/my-service","another-service":"/home/me/repos/another-service"}'
MAX_WORKERS=2                 # concurrency cap (not session count: idle sessions hold no process)
CLAUDE_BIN=/home/me/.local/node/bin/claude
```

Notes:

- `REPO_MAP`'s value needs its **single quotes**. `set -a; . env` strips bare quotes, and
  once the JSON's quotes are eaten the node agent will not start. Change it with a whole-line
  `sed`, not by hand:
  `sed -i "s|^REPO_MAP=.*|REPO_MAP='{\"my-service\":\"/path\"}'|" ~/node-agent.env`
- Which platforms can be dispatched to = the union of online nodes' `PLATFORMS`. Adding a
  platform requires restarting the node agent.
- One repository may serve several platform names (two keys in `REPO_MAP` pointing at the
  same directory).

### 4. Start it

Three ways; prefer the first two — they keep the agent running and restart it after a crash.

```bash
# a) container (no systemd): hand it to supervisord. Idempotent.
bash install-supervisor.sh

# b) a host with systemd
systemctl --user enable --now node-agent.service   # template in this directory

# c) a temporary, non-persistent run (debugging)
bash start-node-agent.sh
```

`install-supervisor.sh` writes `~/deploy/supervisord.conf` (left alone if it exists) and
`~/deploy/programs/skein-node-agent.conf`, then lets supervisord take over. Configuration is
loaded from `~/node-agent.env` only; **the token is never written into the conf**. Then:

```bash
supervisorctl -c ~/deploy/supervisord.conf status                    # state
supervisorctl -c ~/deploy/supervisord.conf tail skein-node-agent    # log
```

After editing `~/node-agent.env`:
`supervisorctl -c ~/deploy/supervisord.conf restart skein-node-agent`.

### 5. Confirm on the control plane

```bash
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://127.0.0.1:7800/internal/nodes
```

You should see your `NODE_ID`, `status=online` and the platforms matching `PLATFORMS`.

## After a container restart or rebuild

**A process started with `nohup` / `start-node-agent.sh` does not come back by itself** — a
restarted or rebuilt container loses it and the node stays offline until someone starts it
again by hand. This has happened in production. So keep it supervised:

- The repositories, Node and configuration live under `$HOME`; make it a volume and they
  survive a rebuild.
- `~/deploy/supervisord.conf` and `programs/*.conf` also live under `$HOME`. Many container
  platforms start `$HOME/deploy/supervisord.conf` at boot if it exists, which brings
  supervisord and with it the node agent back.
- **Check whether yours does**: after a rebuild, look at `supervisorctl … status`. If the
  platform does not start supervisord, add
  `~/.local/bin/supervisord -c ~/deploy/supervisord.conf` to the container's start command,
  or call `install-supervisor.sh` once from the image entrypoint.

If the console shows the node offline (运行 → 节点), check this first.

## Isolation boundary

Each ticket gets its own HOME (`~/repos/home-<workerId>`) and its own git worktree
(`wt-<workerId>`), both cleaned up when the ticket ends or is cancelled; the fix branch stays
in the shared clone so the PR can use it.

Many container environments forbid `unshare`/`bubblewrap`, so `SANDBOX_ENABLED` cannot be
used there and the isolation boundary is **the container itself** — run a node in a
dedicated, trusted container, not co-hosted with other services. Hosts that allow mount
namespaces can set `SANDBOX_ENABLED=1` for stronger isolation (see
[../../docs/sandbox.md](../../docs/sandbox.md)).

## Permission boundary

Workers do not inherit the control plane's token: `SKEIN_TOKEN`, `AGENT_TOKEN`,
`CONTROLLER_URL` and `SKEIN_CONTROL_URL` are stripped when a worker is started
(`services/node-agent/worker-env.mjs`). A worker sees the ticket, the repository and the
model configuration in its own HOME; it cannot call the control plane's `/internal/*`.

Workers push with the deploy key — so **a node's keys need write access to the target
repositories only**. Never put a personal account key that can reach every repository on a
node.
