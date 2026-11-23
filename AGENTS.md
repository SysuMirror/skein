# Notes for coding agents

You are working on Skein, a control plane that turns tickets into agent-authored pull
requests. The same rules apply whether a human or a dispatched worker edits this repository.

## Run things

```bash
make install        # once: control plane, editable, with dev extras
make test           # pytest (no network needed)
make lint           # ruff
make build-console  # vue-tsc + vite (services/console)
cd services/node-agent && npm test
make check-docs     # relative links in Markdown
```

Python lives under `services/control/src/skein/`; tests under `services/control/tests/`.
The console is Vue 3 + naive-ui under `services/console/src/`.

## Conventions that matter

- Read `docs/architecture.md` before changing how the pieces fit together, and
  `CONTRIBUTING.md` for commit and PR rules.
- Keep the core deployment-agnostic: no real domains, addresses, credentials or internal
  hostnames anywhere in the diff.
- Authorization lives in one place, `skein/access.py`, and is enforced per route with
  `require(...)`. Never check roles or claims anywhere else.
- Every new configuration key needs a line in `.env.example` and `docs/configuration.md`,
  and its default must reproduce the previous behaviour.
- Tests must pass without network access; external services are faked.
