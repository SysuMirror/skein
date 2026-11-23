# Deployment

Three parts: the control plane (required), execution nodes (needed to run workers), and
optional integrations (on demand). The smallest useful deployment is the control plane and
one shared token.

## Docker compose

The demo stack in `docker-compose.yml` is a working deployment shape too: `control` (the API
and the console) plus `node` (a real node agent). Replace the demo tokens, add your own
environment (an env file with `SKEIN_SSO_*`, a forge token, …), and drop the `demo` service.
See [deploy/docker/README.md](../deploy/docker/README.md).

## systemd (recommended for a plain server)

Requirements: Python 3.10+ (either `pip install services/control` or point `PYTHONPATH` at
`services/control/src`) and Node 22+ to build the console.

```bash
git clone https://github.com/SysuMirror/skein.git /opt/skein
cd /opt/skein/services/console && npm ci && npm run build
sudo bash /opt/skein/deploy/scripts/install-control.sh
```

`install-control.sh` is idempotent and does five things:

1. creates the data directory (`/var/lib/skein`, mode 700);
2. writes the environment file (`/etc/skein/env`, mode 600, previous copy kept as
   `.bak-<ts>`), generating `SKEIN_TOKEN` if you did not supply one;
3. installs `skein-control.service` from `deploy/systemd/` and starts it, and installs the
   management CLI as `/usr/local/bin/skein`;
4. adds the daily backup cron (03:17, 7 days kept);
5. checks `/health/live` and prints which login method the deployment uses.

**With no variables at all it is token mode**: open the console and sign in with the token
from the environment file. To enable more, put the variables in the calling environment:

```bash
sudo SKEIN_SSO_BASE=https://sso.example.com \
     SKEIN_SSO_CLIENT_ID=… SKEIN_SSO_CLIENT_SECRET=… \
     bash /opt/skein/deploy/scripts/install-control.sh
```

Not using systemd? Run `python -m skein` yourself with the same variables. The console is
served by the control plane: if `services/console/dist` exists it is mounted at `/console/`
(`SKEIN_CONSOLE_DIR` points at it when the layout differs). Behind a reverse proxy, start
from `deploy/nginx/skein.conf` — SSE must not be buffered and `/api/agent/channel` is a
WebSocket.

## Kubernetes pod

A node is easiest to run as a pod (that is one of the reference deployments): a long-lived
container with a persistent volume for the repository clones, `node-agent.env`, and
supervisord starting the agent (`deploy/pod/install-supervisor.sh`). The control plane
itself needs a single writer — run **one** replica with a persistent volume for `SKEIN_DB`
and `SKEIN_BLOB_ROOT`; see [deploy/pod/README.md](../deploy/pod/README.md) for the node and
[control-plane.md](control-plane.md) for why one replica.

## Environment variables

All of them, with defaults and meanings, are in [configuration.md](configuration.md).
`.env.example` at the repository root is the annotated copy.

## Upgrade and rollback

```bash
cd /opt/skein && git pull --ff-only
cd services/console && npm ci && npm run build
sudo systemctl restart skein-control
curl -s http://127.0.0.1:7800/health/ready
```

The database schema migrates itself at startup — additive only (new tables and new columns
with defaults), so an upgrade does not delete anything. Roll back the code with git; if you
ever need to roll back the database, stop the service and replace `SKEIN_DB` with a backup
(and remove the `-wal` / `-shm` files next to it).

Console users sign in again after an upgrade if they were holding a session issued by an
older version; nothing else is lost.

## Backup

`deploy/scripts/backup-skein.sh` takes a consistent snapshot through SQLite's online backup
API (copying the file under WAL can be inconsistent):

```bash
SKEIN_DB=/var/lib/skein/skein.db SKEIN_BACKUP_DIR=/var/lib/skein/backups \
  bash deploy/scripts/backup-skein.sh
```

Restore: stop `skein-control`, replace `skein.db` (remove `-wal`/`-shm`), start. Back up
`SKEIN_BLOB_ROOT` together with the database — the sessions reference it.

## Scheduled jobs (optional)

The driver runs commands on a cron schedule, deduplicated per (job, minute), and records
outcomes in `runs`; failures become Watchdog signals. With a jobs file:

```json
{
  "schedules": [{"job": "nightly-check", "cron": "15 3 * * *"}],
  "commands": {"nightly-check": "/usr/local/bin/nightly-check.sh"}
}
```

`SKEIN_DRIVER=1 SKEIN_JOBS_FILE=/etc/skein/jobs.json`.

## Self-checks

```bash
curl -s http://127.0.0.1:7800/health/live      # process alive
curl -s http://127.0.0.1:7800/health/ready     # database readable
curl -s http://127.0.0.1:7800/api/session      # {"mode": "token" | "local" | "sso"}
curl -s http://127.0.0.1:7800/metrics | head   # Prometheus counts
. /etc/skein/env
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://127.0.0.1:7800/internal/summary
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://127.0.0.1:7800/internal/nodes   # online
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" 'http://127.0.0.1:7800/internal/logs?level=warning&limit=20'
node deploy/scripts/console-smoke.mjs          # console smoke test
skein user list                                # who has an account
```

## What one real deployment looks like

[deploy-case-study.md](deploy-case-study.md): one control plane and one container node
serving several repositories, with SSO and draft PRs (all placeholders).
