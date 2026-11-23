# Worker 运行环境：在哪跑、怎么隔离、选型依据

> worker 就是节点上的一个编码 Agent 会话。本文说明它在哪跑、隔离做到哪一层、以及为什么这么选。
> 具体的 `systemd-run` 参数见 [sandbox.md](sandbox.md)，实现见 `services/node-agent/sandbox.mjs`。

## 0. 结论

| 问题 | 现状 |
| --- | --- |
| 谁改代码 | worker（节点上的编码 Agent）；人只验收 |
| 代码在哪改 | 节点上仓库克隆里的一个独立 worktree（`wt-<workerId>`）；修复分支推回代码托管 |
| 会话模型 | **一单一会话**：每次派工新建 worker，结束或取消后清理 worktree 与 HOME |
| 默认隔离 | **节点/容器本身**。进程级沙箱（`systemd-run`）已实现但默认关闭，需要宿主一次性准备 |
| 凭据 | worker 拿不到控制面的令牌与地址；它能用的是节点上配好的模型网关配置和 deploy key |
| 红线 | 不代替人合入；卡死的 worker 必须能被真正终止 |

## 1. 一个 worker 的一生

```text
控制面：worker.create {worker_id, platform, repo}
  │
  ├─ 节点按 platform 找到本地仓库（REPO_MAP），没有 → worker.create_failed
  ├─ git worktree add --detach  wt-<workerId>        （与其它 worker 互不干扰）
  ├─ 建独立 HOME  home-<workerId>（拷入模型网关配置）
  ├─ 起编码 Agent 做一次初始化，拿到 session_id     → worker.created
  │
控制面：dispatch {worker_id, ticket}
  ├─ 在 worktree 里：git checkout -b fix/… → 改代码 → 自测 → commit
  ├─ 过程中流式回报 progress
  ├─ 结束：收集分支 / 提交 / diffstat；PUSH_BRANCH=1 时推送修复分支  → result
  │
  └─ 清理：git worktree remove + 删 HOME（分支留在共享仓库里，供开 PR）
```

取消（控制面发 `cancel`）时：向 worker 的**整个进程组**发 SIGTERM，10 秒未退再 SIGKILL，
然后清理 worktree 与 HOME。按进程组杀是因为编码 Agent 会派生子命令，只杀主进程会留下孤儿；
必须真杀是因为卡死的 worker 会持续消耗模型额度。

worker 的子进程用独立进程组启动（`spawn(..., {detached: true})`），这样才杀得到整组。

## 2. 隔离：三层，按需启用

| 层 | 边界 | 启用条件 | 对恶意仓库 |
| --- | --- | --- | --- |
| **L0 节点/容器** | worker 直接以节点用户运行 | 默认 | 不是安全边界；靠「节点专用、只跑可信仓库」保证 |
| **L1 进程沙箱** | `systemd-run` 瞬态 service：文件系统只读 + cgroup 资源上限 + 系统调用/能力收紧 | `SANDBOX_ENABLED=1`，且宿主完成了 [sandbox.md](sandbox.md) 的一次性准备 | 不够（仍共享宿主内核） |
| **L2 gVisor / 容器级** | 用户态内核，拦系统调用 | 未实现；node-agent 目前只会 `systemd-run` | 够 |

很多容器环境不允许 `unshare` / `bubblewrap` / mount namespace，L1 在那里开不起来——
这时**容器本身就是边界**。所以部署上的要求是：节点用专用容器，不和其它服务混部，
不要把敏感的东西放在节点的文件系统上。

L1 实际加了什么（`sandbox.mjs`）：

```text
资源    MemoryMax=3G  MemoryHigh=2500M  MemorySwapMax=0  CPUQuota=150%  TasksMax=384  RuntimeMaxSec=3600
文件    ProtectSystem=strict  ProtectHome=yes  只有 worktree 与 HOME 可写  /tmp 是 1G 的临时文件系统
权限    NoNewPrivileges  CapabilityBoundingSet=（空）  RestrictNamespaces  PrivateDevices  PrivateIPC
其它    UMask=0077  LockPersonality  ProtectKernel{Tunables,Modules}  Protect{ControlGroups,Clock,Hostname}
```

### 已知缺口

1. **网络白名单没有接进 node-agent**：[sandbox.md](sandbox.md) 里的 nft 规则要手工在宿主上配，
   node-agent 不负责。不配的话 worker 能访问节点能访问的一切网络。
2. **没有 L2**：仓库不完全可信时，当前没有更强的隔离档位可选。
3. **凭据不是 secretless**：worker 继承节点的 deploy key 和模型网关配置——它能推分支、能花额度。
   这是按「仓库可信」的前提做的取舍；不要把高权限的私钥放到节点上。
4. 沙箱默认关：忘了开就等于只有 L0。

## 3. 业界方案对比（选型依据）

### A. 进程级沙箱

**代表**：Anthropic `sandbox-runtime`（`srt`，bubblewrap / macOS seatbelt + 代理白名单）；
Claude Code 内置 sandboxing；devcontainer 方案。

- 隔离强度：中。文件与网络可限制，但仍共享宿主内核。
- 硬件：无要求。启动：毫秒级。运维：低。
- 适合：**可信仓库**的日常修补、CI 内跑 agent。
- 风险：2026-09 的 Beltdown 证明恶意仓库可通过 `git` 调用逃出 macOS 沙箱且**不触发权限提示**。

### B. 容器级强隔离（gVisor）

**代表**：Docker/containerd + `runsc`（用户态内核，拦系统调用）。

- 隔离强度：中高。syscall 层过滤，宿主内核暴露面显著缩小。
- 硬件：无要求（不需要 KVM）。启动：百毫秒到秒级。运维：中。
- 适合：仓库不完全可信时的默认档。
- 风险：不是零（gVisor 自身有 CVE 历史），但远好于裸进程。

### C. microVM / 托管沙箱

**代表**：Firecracker 类 microVM、各类托管 agent 沙箱服务。

- 隔离强度：高（硬件虚拟化边界）。
- 硬件：**需要 `/dev/kvm`**。启动：百毫秒到数秒。运维：高。
- 适合：多租户、不可信代码、需要快照/回滚。

### 逃逸教训

| 事件 | 时间 | 教训 |
| --- | --- | --- |
| Beltdown：恶意仓库逃出 Claude Code 的 macOS 沙箱（经 git 调用，无提示） | 2026-09 | 进程级沙箱不能对抗恶意仓库；凭据不能进 guest |
| OpenAI Codex 沙箱两次逃逸 | 2026-09 | 同上 |
| Cloudflare sandbox 逃逸 | 2026-09 | 沙箱实现越复杂，边界越要少 |
| Docker Desktop hypervisor guest→host | 2026-09 | 虚拟化层也非绝对；叠层 + 最小权限 |

行业收敛出的三条规矩：**① 不可信代码至少进容器/VM；② 真凭据不进 guest，只给占位符；
③ 出网走宿主侧、agent 改不了的代理。** 雁阵目前满足 ① 的一半（专用容器），
②③ 是上面的已知缺口。

### 对比表

| 维度 | A 进程沙箱 | B gVisor 容器 | C microVM / 托管 |
| --- | --- | --- | --- |
| 对恶意仓库 | 不够 | 够 | 最够 |
| 硬件要求 | 无 | 无 | KVM |
| 冷启动 | ms | 100ms–s | 100ms–s |
| 运维成本 | 低 | 中 | 高 |
| 凭据隔离 | 靠约定 | 可以做到 | 天生更好 |
| 快照/回滚 | 无 | 镜像 + 卷 | 支持 |

## 4. 凭据与出网（目标做法）

| 项 | 目标 | 现状 |
| --- | --- | --- |
| 仓库写权限 | 每仓库一把 deploy key，最好只允许推 `fix/*` 分支 | 每仓库一把 deploy key（已做到）；未限制分支 |
| 模型网关 | 按 worker 发短期 key，限额 | 节点上一份长期配置，所有 worker 共用 |
| 控制面密钥 | 不进 worker | 已做到（`worker-env.mjs` 剔除） |
| 出网 | 只走宿主代理，白名单：代码托管、模型网关、控制面 | 无限制 |
| 日志 | 不记录 token / secret / 密码 | — |

## 5. 红线与非目标

- **不代替人合入**；人审是硬闸。
- **worker 必须可终止**：取消要真的杀进程，不能只改状态。
- 不做多租户；不追求「任意代码安全执行」的通用平台——雁阵假定节点只跑自己团队的仓库。
