# Documentation

English is authoritative. Chinese translations live in [`zh-CN/`](zh-CN/README.md); they
may lag behind and say so.

## Start here

1. [../README.md](../README.md) — what Skein is, and the shortest path to seeing it work
2. [quickstart.md](quickstart.md) — demo, from source, a real node, an assistant
3. [architecture.md](architecture.md) — the three parts, three sources of truth, and what
   is deliberately not implemented
4. [product.md](product.md) — who it is for, the loop, the three work surfaces

## Operating it

- [deployment.md](deployment.md) — install (docker / systemd / K8s pod), upgrade and
  rollback, backup, self-checks
- [configuration.md](configuration.md) — every environment key, its default and meaning
- [authentication.md](authentication.md) — token / local / external login, roles, the
  permission matrix, first admin, adding a provider
- [assistants.md](assistants.md) — the public API and how to connect an external assistant
- [deploy/docker/README.md](../deploy/docker/README.md) — the docker compose demo
- [deploy/pod/README.md](../deploy/pod/README.md) — the node side
- [release-workflow.md](release-workflow.md) — `develop` and `main`, what deploys when
- [deploy-case-study.md](deploy-case-study.md) — one real deployment's configuration,
  with placeholders
- [../SECURITY.md](../SECURITY.md) — credentials, boundaries, how to report a problem

## Internals

- [control-plane.md](control-plane.md) — modules, routes, data tables
- [console.md](console.md) — pages, live snapshots, sign-in flow, what each role sees
- [worker-runtime.md](worker-runtime.md) — where a worker runs and how it is isolated
- [sandbox.md](sandbox.md) — the optional `systemd-run` sandbox around a worker
- [llm-trace.md](llm-trace.md) — model-call traces
- [extending.md](extending.md) — the extension points and how to add one
- [repo-metadata.md](repo-metadata.md) — the repository description and topics, kept in sync with GitHub

## Conventions

- [../CONTRIBUTING.md](../CONTRIBUTING.md) — branches, commits, tests, PR flow
- [../AGENTS.md](../AGENTS.md) — the same for coding agents working in this repository
