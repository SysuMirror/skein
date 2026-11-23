# Extending Skein

There are five places a deployment can plug something in. Each has a small interface, a
registry and a test with a fake; none of them requires touching the core.

| Extension point | Interface | Register in | Selected by |
| --- | --- | --- | --- |
| Login method | `IdentityProvider` | `auth_providers/__init__.py` → `PROVIDERS` | `SKEIN_SSO_PROVIDER` (external) or `SKEIN_LOCAL_LOGIN` (built-in) |
| Code host (draft PRs) | `ForgeClient` | `forge.py` → `FORGES` | `SKEIN_FORGE` |
| Model gateway (console Agent, Watchdog) | `ChatModel` | pass an instance to `create_app(model_client=…)` | — |
| Notification channel | `Channel` | `notify.py` → `CHANNELS` | (today: the signed webhook, used per assistant) |
| Intake source | HTTP (`POST /internal/intake` or `/api/v1/tickets`) | — | — |

## Login method

See [authentication.md](authentication.md#adding-a-login-method) for the protocol. An OIDC
provider would: (1) implement the authorization-code flow for `login` (redirect out and
back; the console would need a button that starts it), (2) call the userinfo/introspection
endpoint in `verify`, (3) `revoke` best-effort, `admin_claims = False` unless the provider
carries a claim you trust, `cached = False` because verification is a network call you want
to cache per request rather than per session… — the skeleton is a weekend project, and the
interface was designed for it. A GitHub login would map `login(sub=github id)` and set
`internal` from Org membership.

## Code host

```python
class ForgeClient(Protocol):
    name: str

    def repo_from_remote(self, remote: str) -> str | None:
        """The worker's git remote → this host's repository id (None if not ours)."""

    def repo_info(self, repo: str) -> dict | None: ...        # at least {"default_branch": …}
    def has_branch(self, repo: str, branch: str) -> bool: ...
    def create(self, repo, head, base, title, body, draft=True) -> PullRequest: ...
```

Register a builder:

```python
# forge.py
FORGES["gitlab"] = lambda cfg: GitLabClient(cfg.gitlab_token, api=cfg.gitlab_api) if cfg.gitlab_token else None
```

`PullRequestService` tries each configured forge's `repo_from_remote` in order (so two hosts
can coexist), falls back to the platform → repository mapping, and resolves the base branch
as documented in [release-workflow.md](release-workflow.md). New configuration keys belong
in `config.py`, `.env.example` and [configuration.md](configuration.md) — defaulting to "off".

## Model gateway

The console Agent calls one method:

```python
class ChatModel(Protocol):
    async def complete(self, model: str, messages: list[dict]) -> ChatResult:  # content + token usage
```

`OpenAICompatModel` is the default and works with any OpenAI-compatible `/chat/completions`
gateway. To use something else, build an instance and pass it where the app is assembled
(`create_app(..., model_client=…)` in `__main__.build`). Traces are recorded around the
call regardless of the implementation.

## Notification channel

`notify.Notifier` turns a ticket status change into a payload and hands it to a `Channel`
(`send(url, payload, secret)`). The built-in `WebhookChannel` posts JSON with an HMAC
signature; it is used per assistant, whose `callback_url` and secret live in the credential
store. A Slack/Matrix/email channel is a class with a `send` method; register it in
`CHANNELS` and select it where the notifier is assembled. Delivery is best-effort by
design — the API (`GET /api/v1/tickets/{ref}`) is the source of truth.

## Intake sources

Anything that can make an HTTP request is an intake source:

- **Assistants** (`/api/v1/tickets`) for external programs that act on behalf of users —
  preferred for anything outside your own infrastructure; the key is scoped and audited.
- **`/internal/intake`** (master token) for internal automation that models a
  forum-like thread: it merges follow-up messages into the active ticket of a thread.

Both are idempotent; both end up as ordinary tickets. If your source needs to be told about
status changes, either register a callback on an assistant credential or poll
`/api/v1/tickets`.

## What not to do

- Don't add a role or permission check outside `access.py`; add a row to the matrix.
- Don't put a provider's host, cookie name or claim names in the core.
- Don't make the core require a new service to start; if it is needed by one deployment,
  it is an integration.
