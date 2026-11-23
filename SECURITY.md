# Security policy

## Reporting a vulnerability

Please report security issues privately — **do not open a public issue**.

- Preferred: GitHub's private vulnerability reporting, *Security → Advisories → Report a
  vulnerability* on this repository (this keeps the discussion private and lets us credit
  you).
- Or email `security@example.com` (placeholder — the maintainers will replace this with a
  real address).

Please include what you can: affected version or commit, a description, steps to
reproduce, configuration that matters (login method, whether nodes are involved), the
impact you see, and any suggested fix. We will acknowledge the report, agree on a timeline
with you, fix it on a private branch, and publish an advisory once a release with the fix
is out. Please do not disclose the issue publicly before that.

## Supported versions

The project is at an early stage: fixes land on `develop` and go out with the next release
from `main`. Run a released version, not a random snapshot.

## Credentials in this repository

Nothing secret belongs in git. The deployment keeps its credentials in one environment
file outside the repository (mode 600), listed here so operators know what exists:

| What | Where it lives | Scope it should have |
| --- | --- | --- |
| `SKEIN_TOKEN` | control plane env file | the master credential: machine calls to `/internal/*`, node registration, the management CLI |
| `SKEIN_GITHUB_TOKEN` | control plane env file | fine-grained token: `Contents: Read`, `Pull requests: Read and write` on the repositories Skein opens PRs against |
| `SKEIN_SSO_CLIENT_SECRET` | control plane env file | the identity provider's client credentials |
| `SKEIN_LLM_KEY` | control plane env file | the model gateway key used by the console Agent |
| assistant keys (`skein_at_…`) | the assistant's own environment | only what the public API offers; one key per assistant |
| node deploy keys | each node, one per repository | write access to that one repository only |

If a credential leaks: rotate it, then rewrite history if it was committed. Assistant keys
are stored as SHA-256 only, so rotate them with `skein assistant rotate <name>`; local
passwords are stored as scrypt hashes.

## What the platform protects

- **Separate credentials per job.** Workers on a node never see `SKEIN_TOKEN`; they get
  their own HOME and worktree and cannot call the control plane. Assistants get their own
  key and cannot use the master token, dispatch, review, cancel, or read other assistants'
  tickets.
- **Humans and machines are distinct.** A signed-in person has an account and a role
  (member < admin < owner); the shared token is a machine identity and cannot act as a
  person where attribution matters (the console Agent).
- **Least privilege by default.** Unknown identities are not provisioned unless the
  deployment opts in; local accounts are created by an owner; when the identity provider
  stops reporting an admin flag, admin rights stop too (see `docs/authentication.md`).
- **Password storage.** Local accounts use scrypt (N=2^15, r=8, p=1 — the OWASP minimum,
  32 MiB per hash). Login attempts are rate limited per source and per account; sessions
  are HttpOnly cookies with same-site protection and expire.
- **Optional routes stay off.** Integrations and trace ingestion are mounted only when
  configured.

## Out of scope

- Vulnerabilities in third-party dependencies — report those upstream; we bump versions.
- Anything that requires control-plane admin access, root on a node, or the master token
  to begin with (those are trusted by definition; tell us anyway if the impact is bigger
  than that trust implies).
