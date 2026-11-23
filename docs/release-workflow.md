# Release workflow

Two long-lived branches, one rule: nothing reaches `main` without a person.

```text
feature branch ──PR──► develop ──deploy──► test environment
                          │
                          └──PR (1 approval)──► main ──deploy──► production
```

## Branches

| Branch | Meaning | Protection |
| --- | --- | --- |
| `develop` | Integration. Pull requests land here first; deploys to the test environment. | No approval required; CI must pass |
| `main` | Released. Merging `develop` into `main` *is* a release. | One approval required (set in the repository settings) |

`develop` is the default base branch for pull requests. A deployment that prefers `main`
can point its flow there; the control plane's PR base is configurable per repository and
globally (`SKEIN_GITHUB_REPOS` / `SKEIN_GITHUB_BASE`), and for Skein's own repositories the
convention is: agents open PRs against `develop`.

## What the platform does and does not do

Skein's workers push fix branches and open **draft** pull requests. They never merge, never
approve, never deploy. Everything after "here is a draft PR" is the team's own process —
often a deploy platform watching `develop`, then a human moving `develop` to `main`.

When a worker's branch is pushed, the control plane resolves the PR base as: the
repository's configured base → the global base → `develop` if the repository has it, else
the repository's default branch. If the token cannot read the repository, the PR is not
created and the audit table records `pr_error` with the reason.

## Versioning

`CHANGELOG.md` follows Keep a Changelog; versions follow SemVer. One version number is used
by the control plane (`skein.__version__`), the console and the node agent; a test keeps
them equal. A release is a commit on `main` tagged `vX.Y.Z`, with the changelog section for
that version.

## Test environments

`SKEIN_TEST_ENVS` maps a repository slug to the URL of the environment that `develop`
deploys to. The console shows it as a link on the ticket's delivery card, so the reviewer
knows where to look after the PR is merged. Deploying that environment is the deployment
platform's job; Skein only links to it.
