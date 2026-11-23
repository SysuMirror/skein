# Repository metadata

The GitHub description and topics for this repository, kept here so they can be reviewed
and changed like anything else. Update this file first, then apply it.

## Description (one line, ≤ 120 characters)

```text
Self-hosted control plane that turns tickets into agent-authored pull requests on your own nodes.
```

## Topics

```text
ai-agents
agent-orchestration
coding-agents
developer-tools
self-hosted
workflow-automation
task-scheduler
claude-code
fastapi
vue3
sqlite
websocket
python
typescript
```

Rationale per topic: the first eight say what the project is and who it is for; the rest
are what it is actually built with, one topic per dependency a reader would care about.

## Applying it

With `gh` signed in as someone who can administer the repository:

```bash
gh api -X PATCH repos/SysuMirror/skein \
  -f description='Self-hosted control plane that turns tickets into agent-authored pull requests on your own nodes.'

gh api -X PUT repos/SysuMirror/skein/topics \
  -f names[]=ai-agents -f names[]=agent-orchestration -f names[]=coding-agents \
  -f names[]=developer-tools -f names[]=self-hosted -f names[]=workflow-automation \
  -f names[]=task-scheduler -f names[]=claude-code -f names[]=fastapi -f names[]=vue3 \
  -f names[]=sqlite -f names[]=websocket -f names[]=python -f names[]=typescript
```

## Settings that go with them

- **Homepage**: leave empty until there is a docs site; the README is the front door.
- **Discussions**: enable — the issue templates and SUPPORT.md point people there for
  questions.
- **Issues**: on, with the two forms in `.github/ISSUE_TEMPLATE/`.
- **Wiki**: off (docs live in the repository so they are reviewed with the code).
- **Private vulnerability reporting**: enable, so SECURITY.md's preferred channel works.
