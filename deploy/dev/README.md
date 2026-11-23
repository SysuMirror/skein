# Local development

Everything below runs the control plane end to end without touching any real credential —
the checklist for a new machine or a new teammate.

## Option 1: token mode (least setup)

No configuration beyond one shared token:

```bash
cd services/control
PYTHONPATH=src \
SKEIN_DB=data/dev.db SKEIN_BLOB_ROOT=data/blobs SKEIN_TOKEN=dev-token \
python -m skein
```

Open the console (below) and sign in with `dev-token`. The Agent needs a model: add
`SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` / `SKEIN_AGENT_MODEL`; without them the Agent answers 503
and everything else works.

## Option 2: local accounts

To exercise the account flows, enable the built-in local login:

```bash
cd services/control
PYTHONPATH=src SKEIN_LOCAL_LOGIN=1 \
SKEIN_DB=data/dev.db SKEIN_BLOB_ROOT=data/blobs SKEIN_TOKEN=dev-token \
python -m skein

# in another terminal — create the first owner (the console has no sign-up)
python -m skein user create --email you@example.com --role owner --password-stdin
```

You can also create members (`--role member`) and see what the console looks like for them:
read-only on tickets and operations, no Agent, no dispatch buttons.

## Option 3: SSO mode (against a fake provider)

To work on the sign-in flow, run the local fake that implements UniSSO's three endpoints:

```bash
python deploy/dev/mock-sso.py          # listens on 127.0.0.1:13099
```

Accounts (password `right-pass` for all): `admin@example.com` (admin), `member@example.com`
(member), `outsider@example.com` (not an internal member — refused by the default policy).

```bash
cd services/control
PYTHONPATH=src \
SKEIN_DB=data/dev.db SKEIN_BLOB_ROOT=data/blobs SKEIN_TOKEN=dev-token \
SKEIN_SSO_BASE=http://127.0.0.1:13099 \
SKEIN_SSO_CLIENT_ID=dev-client SKEIN_SSO_CLIENT_SECRET=dev-secret \
python -m skein
```

- The default policy requires an "internal member"; to simulate a deployment that serves
  the public, add `SKEIN_SSO_REQUIRE_INTERNAL=0`.
- The fake returns a host-only cookie, which works over plain local HTTP. In a real
  deployment the cookie domain comes from the provider's response, or is set explicitly
  with `SKEIN_COOKIE_DOMAIN=.example.com`.

## The console

```bash
cd services/console
npm ci && npm run build
cd ../..
node deploy/dev/preview-proxy.mjs services/console/dist http://127.0.0.1:7800 5173
```

Open `http://127.0.0.1:5173/console/`.

## A local node (optional)

To have tickets actually worked on there must be a node. Simplest: another node agent on
the same machine, pointed at the local control plane, with a local repository:

```bash
CONTROLLER_URL=ws://127.0.0.1:7800/api/agent/channel \
SKEIN_TOKEN=dev-token SKEIN_CONTROL_URL=http://127.0.0.1:7800 \
NODE_ID=dev-node PLATFORMS=demo REPO_MAP='{"demo":"/path/to/your/repo"}' \
node services/node-agent/node-agent.mjs
```

The rest of a node's preparation (the agent's model gateway configuration, a deploy key
with push access) is in [pod/README.md](../pod/README.md).

## Notes

- Machine-private scripts (with real keys) never go into the repository.
- For a real deployment see [docs/deployment.md](../../docs/deployment.md).
