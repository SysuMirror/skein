# Worker runtime: where a worker runs, how it is isolated, and why

> A worker is one coding-agent session on a node. This page explains where it runs, how far
> isolation goes, and the reasoning behind the choice. The concrete `systemd-run` arguments
> are in [sandbox.md](sandbox.md); the implementation is `services/node-agent/sandbox.mjs`.

## Summary

| Question | Current answer |
| --- | --- |
| Who edits code | the worker (a coding agent on a node); a person only accepts |
| Where | a dedicated git worktree (`wt-<workerId>`) inside the node's clone; the fix branch is pushed to the code host |
| Session model | **one worker per ticket**: created for the dispatch, worktree and HOME removed after |
| Default isolation | **the node/container itself**. A process sandbox (`systemd-run`) exists but is off by default and needs one-time host preparation |
| Credentials | a worker never sees the control plane's token or URL; it can use the model-gateway configuration and deploy key that are on the node |
| Red line | never merge for a person; a stuck worker must actually be killable |

## A worker's life

```text
control plane: worker.create {worker_id, platform, repo}
  │
  ├─ node resolves the platform to a local clone (REPO_MAP); missing → worker.create_failed
  ├─ git worktree add --detach  wt-<workerId>        (independent of other workers)
  ├─ create an independent HOME  home-<workerId> (model-gateway settings copied in)
  ├─ start the coding agent once to get a session_id → worker.created
  │
control plane: dispatch {worker_id, ticket}
  ├─ in the worktree: git checkout -b fix/… → edit → self-test → commit
  ├─ streams progress meanwhile
  ├─ at the end: collect branch / commits / diffstat; with PUSH_BRANCH=1 push the branch → result
  │
  └─ cleanup: git worktree remove + delete the HOME (the branch stays in the shared clone for the PR)
```

On cancel the control plane sends `cancel`; the node sends SIGTERM to the worker's **whole
process group**, SIGKILL 10 seconds later, then cleans the worktree and the HOME. Killing
the group matters because coding agents spawn subprocesses; leaving orphans or a stuck
worker keeps burning model budget. The child is started detached
(`spawn(..., {detached: true})`) precisely so the group can be killed.

## Isolation: three levels, opt-in

| Level | Boundary | Enabled by | Against a malicious repository |
| --- | --- | --- | --- |
| **L0 node/container** | the worker runs as the node's user | default | not a security boundary; relies on a dedicated node with trusted repositories |
| **L1 process sandbox** | `systemd-run` transient unit: read-only filesystem, cgroup limits, tightened syscalls/capabilities | `SANDBOX_ENABLED=1` and the one-time host preparation in [sandbox.md](sandbox.md) | not enough (shares the host kernel) |
| **L2 gVisor / container-level** | user-space kernel intercepting syscalls | not implemented; the node agent only knows `systemd-run` | enough |

Many container environments do not allow `unshare` / bubblewrap / mount namespaces, so L1
cannot be enabled there — in those places **the container is the boundary**. Hence the
deployment rule: a dedicated container for the node, not co-hosted with other services, and
nothing sensitive on the node's filesystem.

What L1 actually adds (`sandbox.mjs`):

```text
resources  MemoryMax=3G  MemoryHigh=2500M  MemorySwapMax=0  CPUQuota=150%  TasksMax=384  RuntimeMaxSec=3600
files      ProtectSystem=strict  ProtectHome=yes  only the worktree and HOME are writable  /tmp is a 1G tmpfs
rights     NoNewPrivileges  CapabilityBoundingSet= (empty)  RestrictNamespaces  PrivateDevices  PrivateIPC
other      UMask=0077  LockPersonality  ProtectKernel{Tunables,Modules}  Protect{ControlGroups,Clock,Hostname}
```

### Known gaps

1. **No network allow list in the node agent**: the nftables rules in
   [sandbox.md](sandbox.md) are configured by the host, not by the agent. Without them a
   worker reaches everything the node can reach.
2. **No L2**: there is no stronger level for repositories you do not fully trust.
3. **Not secretless**: a worker inherits the node's deploy key and model-gateway settings —
   it can push branches and spend budget. That is a trade-off made for "the repository is
   trusted"; do not put high-privilege private keys on a node.
4. The sandbox is off by default; forgetting to enable it means L0 only.

## How this compares (why these choices)

### A. Process-level sandbox

**Examples**: Anthropic `sandbox-runtime` (`srt`: bubblewrap / macOS seatbelt plus a proxy
allow list); Claude Code's built-in sandboxing; devcontainers.

- Strength: medium. Files and network can be limited, the host kernel is shared.
- Hardware: none. Start: milliseconds. Ops: low.
- Fits: day-to-day fixes on **trusted** repositories, agents inside CI.
- Risk: the 2026-09 Beltdown finding showed a malicious repository escaping the macOS
  sandbox *through `git`* without a permission prompt.

### B. Container-level strong isolation (gVisor)

**Examples**: Docker/containerd with `runsc`.

- Strength: medium-high. Syscalls are filtered; the host kernel surface shrinks.
- Hardware: none (no KVM needed). Start: 100 ms–1 s. Ops: medium.
- Fits: the default level when repositories are not fully trusted.
- Risk: not zero (gVisor has had CVEs), but far better than a bare process.

### C. microVM / hosted sandboxes

**Examples**: Firecracker-class microVMs, hosted agent sandbox services.

- Strength: high (hardware virtualization boundary).
- Hardware: **needs `/dev/kvm`**. Start: 100 ms–several seconds. Ops: high.
- Fits: multi-tenancy, untrusted code, snapshot/rollback.

### Escape lessons

| Incident | When | Lesson |
| --- | --- | --- |
| Beltdown: a malicious repository escaped Claude Code's macOS sandbox via `git`, silently | 2026-09 | a process sandbox cannot withstand a malicious repository; credentials must not be in the guest |
| OpenAI Codex sandbox escapes (twice) | 2026-09 | same |
| Cloudflare sandbox escape | 2026-09 | the more a sandbox implements, the fewer boundaries it should have |
| Docker Desktop hypervisor guest→host | 2026-09 | the virtualization layer is not absolute either; layer it, and keep least privilege |

The industry has converged on three rules: **① untrusted code goes into at least a
container/VM; ② real credentials never enter the guest, only placeholders; ③ egress goes
through a host-side proxy the agent cannot change.** Skein meets half of ① (a dedicated
container); ② and ③ are the gaps above.

| Dimension | A process | B gVisor | C microVM / hosted |
| --- | --- | --- | --- |
| Malicious repository | not enough | enough | most |
| Hardware | none | none | KVM |
| Cold start | ms | 100 ms–s | 100 ms–s |
| Ops cost | low | medium | high |
| Credential isolation | by convention | achievable | inherently better |
| Snapshot/rollback | none | image + volume | supported |

## Credentials and egress (target state)

| Item | Target | Today |
| --- | --- | --- |
| Repository write access | one deploy key per repository, ideally restricted to `fix/*` | one deploy key per repository (done); branches not restricted |
| Model gateway | short-lived per-worker keys with quotas | one long-lived configuration on the node, shared by workers |
| Control-plane keys | never in a worker | done (`worker-env.mjs` strips them) |
| Egress | host proxy only, allow-listed: code host, model gateway, control plane | unrestricted |
| Logs | never log tokens/secrets/passwords | — |

## Red lines and non-goals

- **Never merge for a person**; human review is the hard gate.
- **A worker must be killable**: cancelling kills processes, not just changes a row.
- No multi-tenancy; this is not a general "execute arbitrary code safely" platform — Skein
  assumes a node runs its own team's repositories.
