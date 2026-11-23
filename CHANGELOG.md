# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-23

First public release.

### Added

- **Tickets and threads.** External sources submit a request (dedup by source + id); a
  ticket tracks it from intake to acceptance, and a ticket's thread collects follow-ups.
- **Nodes and workers.** Nodes dial in over a WebSocket channel, register the platforms
  they serve, and run one coding agent per ticket in an isolated worktree and HOME.
- **Draft pull requests.** When a worker pushes a branch, the control plane opens a draft
  PR against the integration branch (`develop` if it exists). Nothing merges itself.
- **Console.** Live overview, tickets, delivery cards with branch/commit/diffstat, node
  and worker views, session transcripts, model-call traces, watchdog signals.
- **Console Agent.** Ask the platform in natural language; it reads the real state and
  only dispatches or cancels when explicitly asked.
- **Watcher (Watchdog).** Reads facts from the database (dispatch timeouts, failed jobs,
  offline nodes with work), optionally turns them into tickets, capped per day.
- **Assistant integration.** A versioned public API (`/api/v1`) with per-assistant keys:
  submit a request idempotently, query your own tickets, optional signed status webhooks.
- **Identity and access.** Roles `owner` / `admin` / `member` with one permission matrix;
  three login methods (shared token, local accounts with scrypt, pluggable external
  provider); machine and human identities kept apart.
- **Runs anywhere.** Zero-configuration token mode, systemd units, node installer, a
  docker compose demo with a mock worker, and `python -m skein` management commands.
- **Observability.** `/health/live`, `/health/ready`, Prometheus counts at `/metrics`,
  in-process log ring at `/internal/logs`, optional JSON logs, an audit trail for every
  write.

### Notes

- The chat-proxy integration (`/api/v1/agent/*`) is an optional route mounted only when
  `SKEIN_QA_CHAT_UPSTREAM` is set; assistant keys are not valid on it.

[Unreleased]: https://github.com/SysuMirror/skein/compare/v0.1.0...develop
[0.1.0]: https://github.com/SysuMirror/skein/releases/tag/v0.1.0
