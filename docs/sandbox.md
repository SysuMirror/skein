# Sandbox: keeping a worker from taking the machine down

> Measured on a host (systemd 249.11, cgroup v2 unified, Ubuntu 22.04). Implementation:
> `services/node-agent/sandbox.mjs`. **Off by default** (`SANDBOX_ENABLED=1` plus the
> one-time preparation below); without it a worker runs as the node's user, and the
> isolation boundary is the node/container itself.

## Approach

A dedicated low-privilege user, a `systemd-run` transient **service**, cgroup v2, mount
protection and an nftables port allow list.

- Use `systemd-run --unit` (a service), **not `--scope`**: sandboxing options such as
  `ProtectSystem` only apply to services.
- The worker runs as `agent`, with its working directory under `/srv/agent/jobs/<job>`.
- Four layers of limits — resources, filesystem, syscalls, network — see the quotas below.
- Higher-assurance alternative: if the host's docker has `runsc` registered, run the agent
  with `docker run --runtime=runsc` or `runsc do` outside Kubernetes.

## One-time preparation (on the node)

```bash
useradd -r -m -d /srv/agent -s /bin/bash agent
install -d -o agent -g agent -m 700 /srv/agent/jobs
install -d -o agent -g agent -m 700 /srv/agent/jobs/<job>/{.home,.npm}
```

**Do not put the work directory on the root disk.** Keep it on a separate volume, a loop
image or an XFS project quota; filling the root disk takes production services down with it.

Network allow list (nftables, matched by cgroup):

```bash
nft add table inet agent
nft add chain inet agent out '{ type filter hook output priority 0; }'
nft add chain inet agent job
nft add rule inet agent out socket cgroupv2 level 2 "system.slice/agent-<job>.service" jump job
nft add rule inet agent job oifname "lo" tcp dport 8080 accept
nft add rule inet agent job udp dport 53 accept
nft add rule inet agent job tcp dport {80,443} ip daddr != {10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8} accept
nft add rule inet agent job drop
```

## Default quotas

| Item | Value |
| --- | --- |
| Memory | hard 3G, soft 2.5G, swap 0 |
| CPU | 150% (1.5 cores), weight 50 |
| PIDs | 384 |
| `/tmp` | 1G tmpfs (counts as memory) |
| One run | 1 hour, 30 s to wind down |
| I/O | read 50 MB/s, write 20 MB/s |
| Concurrency per host | 1 (the host is mostly busy with production) |

## What it cannot do

1. It shares the kernel (no gVisor); a kernel/LSM bug can escape.
2. World-readable files (`/etc`, `/var/log`, …) stay readable; `ProtectHome` only hides home
   directories.
3. On an allow-listed port any HTTP request is possible; nftables does not understand
   application layers.
4. No disk capacity/inode quota — filling the work volume takes down whatever shares it.
5. Once outbound 443 is open, anything read can be exfiltrated; side channels are out of
   scope.
6. If `SystemCallFilter` conflicts with Node, drop it first to locate the problem; **do not
   enable `MemoryDenyWriteExecute`** — it kills V8's JIT.

Only `runsc` defends against kernel bugs, and that needs images pulled — make room on the
root disk first if it is tight.

## Three moves when something goes wrong

1. Freeze and inspect: `systemctl freeze agent-<job>`, `systemd-cgtop -m` for memory/CPU/tasks.
2. Kill the group: `systemctl kill -s KILL --kill-who=all agent-<job>`, then `stop`,
   `reset-failed`.
3. Look at the damage: `cat /sys/fs/cgroup/system.slice/agent-<job>.service/memory.events`
   (oom_kill), `journalctl -u agent-<job> -n 200`, `df -h /`.

## Where this lives in the code

- `services/node-agent/sandbox.mjs` builds the `systemd-run` arguments; enabled only with
  `SANDBOX_ENABLED=1`.
- When disabled, behaviour is unchanged (spawn the agent directly) — the transitional mode.
- Enabling it without the one-time preparation makes workers fail on permissions or paths.
