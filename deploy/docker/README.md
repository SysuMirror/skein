# Docker demo

`docker compose up` from the repository root starts three things:

| Service | What it is |
| --- | --- |
| `control` | The control plane and the console at <http://localhost:7800/console/> |
| `node` | A real node agent with a **mock** coding agent, so no model key is needed |
| `demo` | A one-shot script: submits a ticket through the public API, dispatches it, follows it |

The demo plays the parts a human normally plays: the assistant submits, the operator
dispatches, and then you watch the worker report back. When `demo` exits, the ticket is in
`review` and waits for a person — nothing merges itself.

## Watch it

```bash
docker compose up --build
# in another terminal
docker compose logs -f demo
```

Then open <http://localhost:7800/console/> and sign in with the token
`demo-master-token` (token mode is the zero-configuration login method). You will see the
ticket, its dispatch, the worker's commit, and the delivery card.

## What is demo-only

- Both tokens and the assistant key are public: they are written in `docker-compose.yml`.
  Never reuse them outside this stack.
- `node` runs `deploy/docker/mock-agent.mjs` through a `claude` shim. It emits the same
  stream-json protocol as a real agent and commits a note on the fix branch; it never calls
  a model. A production node runs a real coding agent — see `docs/worker-runtime.md`.
- The repository the worker edits is created on the `node-repos` volume
  (`/srv/repos/app`, pushed to a bare origin next to it) so `git push` works without
  GitHub. PR creation stays off because no forge token is configured.
- The console link in the demo output uses `localhost`; inside the compose network the
  control plane is just `control`.

## Cleanup

```bash
docker compose down -v      # also removes both volumes (database, demo repository)
```

## Slow mirrors

The images use the default Debian, npm and PyPI sources. Where those are slow, pass a
mirror as a build argument; nothing in the repository changes:

```bash
APT_MIRROR=mirrors.example.com NPM_REGISTRY=registry.example.com PIP_INDEX_URL=https://pypi.example.com/simple docker compose build
```

## Reusing the images

`deploy/docker/Dockerfile.control` and `Dockerfile.node` are ordinary images: point them at
your own environment file (`-e SKEIN_SSO_*`, a real `SKEIN_GITHUB_TOKEN`, …) and they are
the same binaries a systemd deployment runs. Nothing in the demo is compiled differently.
