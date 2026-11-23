# Authentication and authorization

Two questions are kept apart on purpose:

- **Who is this?** — answered by one login method. The provider only reports an identity.
- **What may they do?** — answered by Skein's own accounts and roles, in one permission
  matrix (`services/control/src/skein/access.py`).

That split is what lets a deployment switch login methods without touching authorization,
and what keeps the matrix reviewable: it is one table, and the table below is generated
from it (a test fails if they drift).

## Kinds of caller

| Kind | Credential | Where it may go |
| --- | --- | --- |
| **Session** (a signed-in person) | HttpOnly cookie | The console and `/internal/*`, limited by role |
| **Machine** | the master token `SKEIN_TOKEN` in the `X-Skein-Token` header | `/internal/*` as automation, node registration, the CLI; never acts as a person |
| **Assistant** | its own key, `Authorization: Bearer skein_at_…` | Only `/api/v1/*` (see [assistants.md](assistants.md)) |

The master token is a machine identity even in token mode, where a person trades it for a
browser session at the sign-in form — after that the browser holds only the session cookie,
never the token. In every mode the master token is refused where an action must be
attributed to a person (the console Agent).

## Permission matrix

Roles are ordered: `member` < `admin` < `owner`. A permission names the lowest role that
has it, so every higher role includes it.

<!-- matrix:start -->
| Permission | member | admin | owner | machine token | What it covers |
| --- | --- | --- | --- | --- | --- |
| `read` | yes | yes | yes | yes | Read everything the console shows: tickets, threads, events, sessions, nodes, signals, model-call traces |
| `ticket.file` | yes | yes | yes | yes | File a ticket (without dispatching it) |
| `ticket.operate` | no | yes | yes | yes | Dispatch, review or cancel a ticket; submit through `/internal/intake` |
| `ops.ingest` | no | yes | yes | yes | Upload worker transcript turns and model-call records |
| `agent.chat` | no | yes | yes | signed-in only | Talk to the console Agent (its actions are attributed to a person) |
| `assistant.manage` | no | yes | yes | yes | Issue, rotate and revoke assistant credentials |
| `user.read` | no | yes | yes | yes | List users and their roles |
| `user.manage` | no | no | yes | yes | Create users, set roles and passwords, disable accounts, link login identities |
<!-- matrix:end -->

Each route declares what it needs (`require("ticket.operate")`); there is no second place
where roles are checked.

## Login methods

Exactly one **human** login method is active at a time. Configuration decides which, and a
contradictory configuration refuses to start with a message naming the variables:

| Rule | Method | What the console asks for |
| --- | --- | --- |
| `SKEIN_SSO_CLIENT_ID` + `SKEIN_SSO_CLIENT_SECRET` set | `sso` — an external identity provider | email and password |
| otherwise `SKEIN_LOCAL_LOGIN=1` | `local` — accounts in Skein's own database | email and password |
| otherwise (the default) | `token` — nothing to configure | the master token |

`GET /api/session` says which one: `{"mode": "token" | "local" | "sso"}`, and with a valid
session it also returns the user (`sub`, `role`) and the permissions to show or hide
controls. An expired or unknown session is reported as signed out, not as an error.

### Token mode (zero configuration)

Whoever holds the master token is the owner of the deployment — the environment file is in
their hands anyway. Sign in with it once; Skein issues a session cookie. There is no user
table to manage; machine callers keep using the header.

### Local accounts

Accounts are created by an owner, from the command line (the control plane is the only
writer, so the CLI talks to its management API):

```bash
skein user create --email you@example.com --role owner --password-stdin
skein user list
skein user role you@example.com admin
skein user passwd you@example.com --password-stdin     # also signs them out everywhere
skein user disable former@example.com
```

Passwords are stored as **scrypt** hashes (`N=2^15, r=8, p=1` — OWASP's minimum
parameters, 32 MiB and some tens of milliseconds per hash) with a per-hash salt; the hash
format carries its own parameters so raising them later keeps old hashes valid. Passwords
never appear in `argv` (use `--password-stdin` or the interactive prompt). Login attempts
are rate limited per source address (10/min) and, for local accounts, per account
(20 failures are enough to lock it for 15 minutes); an unknown account costs the same as a
wrong password because a dummy hash is computed either way.

Sessions are Skein's own: a 256-bit random token, stored only as its SHA-256, delivered as
an `HttpOnly`, `SameSite=Lax` cookie (add `Secure` unless you deliberately run plain HTTP
by setting `SKEIN_SESSION_COOKIE_SECURE=0`). They expire after `SKEIN_SESSION_TTL_HOURS`
(default 168), are revoked on logout, password change, or disabling the account, and role
changes take effect on the next request. Cross-site write requests that carry the cookie
are refused (`sec-fetch-site: cross-site` → 403).

**First admin.** After enabling local login the account table is empty and nobody can sign
in. Create the first owner from the host before handing out the URL:

```bash
skein user create --email you@example.com --role owner --password-stdin
```

If you forget, nothing is broken: the master token still reaches the management API from
the host (the same `skein …` commands), and the console simply shows the sign-in form.

### External identity provider

Setting `SKEIN_SSO_CLIENT_ID` and `SKEIN_SSO_CLIENT_SECRET` (plus `SKEIN_SSO_BASE`) turns on
the provider named by `SKEIN_SSO_PROVIDER` — `unisso` by default, the only one built in.
The provider sees the sign-in form's email and password, returns who the person is, and
Skein stores the resulting session in the cookie the provider names.

UniSSO is not OIDC: the protocol is three plain POST endpoints on the provider's base URL —
`/api/oauth/password/verify` (credentials → user and session), `/api/oauth/session/verify`
(session → user), `/api/oauth/session/revoke` (logout). It reports `internal`, `is_admin`
and `is_super_admin`; whether "internal" is required is `SKEIN_SSO_REQUIRE_INTERNAL`
(default `1`, kept from earlier versions — set `0` for deployments that also serve the
public). Nothing about the provider's host, cookie name or cookie domain is in the core:
`SKEIN_SSO_COOKIE_NAME` and `SKEIN_COOKIE_DOMAIN` override what the response says, and
`SKEIN_COOKIE_DOMAIN=host` keeps the cookie host-only for plain-HTTP debugging.

**Sessions are still Skein's**: the provider's session is used once at sign-in; what the
browser keeps is the session cookie, so a provider outage does not log anyone out. An
outage does answer `503 sso_unavailable` to someone who is not signed in yet, and the
console keeps showing what it already loaded.

## Accounts, identities and roles

One `users` row per person; a login method is an **identity** attached to that row
(`identities(provider, subject)`), which is why one person can have several login methods
and why changing the login method later does not lose accounts.

- **First sight of an identity**: if the provider says the person is an admin
  (`is_admin` → `admin`, `is_super_admin` → `owner`), an account is created with that role
  — an upgrade must not lock out the people who already administer the deployment.
  Everybody else is not provisioned unless the deployment sets
  `SKEIN_AUTH_AUTO_ROLE=member`.
- **Never merged by email.** An email reported by a provider is unverified; it is a display
  attribute, not a key. To attach a second login method to an existing person, link it
  explicitly: `skein user link usr_… --provider unisso --subject …`.
- **What the provider grants is a ceiling for elevated roles** (providers that authoritatively
  report admin flags, i.e. `admin_claims`): `member` is Skein's own grant and unaffected,
  but `admin` and `owner` are only effective while the provider still says so. The check is
  live on every request (the identity itself is cached for at most a minute), so revoking
  someone upstream takes effect like it always did, and a provider hiccup that clears the
  flag for a moment heals by itself instead of demoting everyone permanently.
- **An owner can always lower a role** and it stays lowered — the provider cannot push
  someone back up that an owner demoted to `member`. The last enabled owner cannot be
  demoted or disabled.

## Adding a login method

A login method is a module implementing one protocol:

```python
class IdentityProvider(Protocol):
    name: str
    cookie_names: tuple[str, ...]          # cookies this provider accepts (newest first)

    def login(self, identifier: str, secret: str) -> tuple[Principal, dict]:
        """Return (identity, session info). Session info carries at least `session`;
        optionally cookie_name / cookie_domain / cookie_max_age."""

    def verify(self, session: str) -> Principal | None: ...   # None = invalid
    def revoke(self, session: str) -> None: ...
```

Optionally declare two attributes: `admin_claims = True` when the provider's admin flag is
authoritative (see above), and `cached = False` when verification is a local lookup (Skein's
own sessions do this, so revocation is immediate).

1. Add a module under `services/control/src/skein/auth_providers/` implementing the
   protocol, with a `build(config) -> IdentityProvider | None` (None when not configured).
2. Register the name in `PROVIDERS` in `auth_providers/__init__.py`.
3. Select it with `SKEIN_SSO_PROVIDER`.

Nothing in `api.py`, `auth.py` or the matrix changes. Skeletons for OIDC and GitHub are not
implemented; [extending.md](extending.md) sketches how each would map onto the protocol.

## Rejection codes

The console shows text for these; they are part of the contract:

| Code | HTTP | Meaning |
| --- | --- | --- |
| `no_sso` | 401 | no session cookie on a route that needs one |
| `invalid_sso` | 401 | the session is unknown, expired or revoked |
| `invalid_token` | 401 | the `X-Skein-Token` header is present but wrong |
| `not_internal` | 403 | the identity is not an internal member and the deployment requires it |
| `not_admin` | 403 | no account (or not eligible) — or, in the older wording, not an admin |
| `account_disabled` | 403 | the account exists but is disabled |
| `invalid_credentials` | 401 | wrong email/password |
| `password_verify_forbidden` | 403 | the provider refuses password verification for this application |
| `too_many_attempts` | 429 | login rate limit (per source address or per account) |
| `sso_unavailable` | 503 | the provider is unreachable or failing — a person is *not* logged out by this |
| `sign_in_required` | 403 | a machine tried an action that must be attributed to a person |
| `cross_site_request` | 403 | a write request carrying a session cookie was made cross-site |

## Checking a deployment

```bash
curl -s https://skein.example.com/api/session                 # {"mode": "sso", "provider": "unisso"}
curl -s -H "Cookie: unisso_sid=…" https://skein.example.com/api/session   # role and permissions
skein user list                                               # who has what
```

A provider that answers "invalid" and a provider that does not answer are different: the
first logs the person out, the second must not.
