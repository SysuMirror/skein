# 部署案例：一个控制面 + 一个容器节点，服务八个代码仓库

> 这是雁阵的一份真实部署的配置思路，域名、账号、路径全部换成占位值。
> 它展示了「各部分怎么配在一起」，不是唯一做法。

## 场景

一个小团队维护着若干个 Web 项目（前端、后端、几个独立服务）。希望：

- 用户在社区/产品里提的反馈，能自动变成工单并派给编码 Agent 去修；
- Agent 修完推分支、自动开草稿 PR，**人工验收**后才进入发布流程；
- 维护者用统一登录进控制台看进度、验收、和 Agent 对话。

## 拓扑

```text
                    ┌──────────────────────────── 控制面所在主机 ────────────────────────────┐
浏览器 ── https://skein.example.com/console/ ── nginx ── 控制面 127.0.0.1:7800 ── SQLite + blobs │
                    │      │                                                        │
                    │      └── /api/agent/channel（WebSocket）                         │
                    └── /internal/intake ←── 入口机器人 / 外部系统                       │
                                    │
                    ┌───────────────┴──────────── 执行节点（一个容器）───────────────┐
                    │  node-agent ── 每单一个 worker（独立 worktree + 独立 HOME）       │
                    │  repos/project-a  repos/project-b  …（各一把可写 deploy key）     │
                    └──────────────────────────────────────────────────────────────────┘
```

只有控制面对外暴露 HTTPS；节点**主动拨出**连接控制面，不需要入站端口。

## 控制面

环境文件（`/etc/skein/env`，600）：

```ini
SKEIN_DB=/var/lib/skein/skein.db
SKEIN_BLOB_ROOT=/var/lib/skein/blobs
SKEIN_HOST=127.0.0.1
SKEIN_PORT=7800
SKEIN_CONTROL_URL=https://skein.example.com
SKEIN_TOKEN=<随机 32 字节>

# 控制台 Agent 用的模型
SKEIN_LLM_BASE=https://api.example-llm.com/v1
SKEIN_LLM_KEY=<key>
SKEIN_AGENT_MODEL=<model>

# 登录：用团队已有的 SSO（这里是 UniSSO）
SKEIN_SSO_BASE=https://sso.example.com
SKEIN_SSO_CLIENT_ID=<client id>
SKEIN_SSO_CLIENT_SECRET=<client secret>
SKEIN_SSO_REQUIRE_INTERNAL=1        # 只有团队成员能登录

# worker 推分支后自动开草稿 PR
SKEIN_GITHUB_TOKEN=<fine-grained PAT：Contents 只读 + Pull requests 读写>
SKEIN_GITHUB_REPOS={
  "project-a":       {"slug": "example-org/project-a", "base": "develop"},
  "project-a-forum": {"slug": "example-org/project-a", "base": "develop"},
  "project-b":       {"slug": "example-org/project-b", "base": "develop"}
}
SKEIN_TEST_ENVS={
  "example-org/project-a": "https://test-a.example.com/",
  "example-org/project-b": "https://test-b.example.com/"
}

# 外部 Watchdog
SKEIN_WATCHDOG=1
SKEIN_WATCHDOG_TICKETS=1
SKEIN_WATCHDOG_INTERVAL=300
```

几个配置上的要点，都是实际踩出来的：

- **同一个仓库可以对应多个平台名**。`project-a` 和 `project-a-forum` 指向同一个仓库，
  只是入口不同（产品内反馈 vs 社区帖子），方便在控制台里区分来源。
- **`SKEIN_GITHUB_TOKEN` 的仓库范围要覆盖所有会收 PR 的仓库**。private 仓库不划进去，
  开 PR 时会得到 `422 not all refs are readable`——分支已经推上去了，只是 PR 没建出来。
  审计表里的 `pr_error` 会记录具体原因。
- **基准分支**：每个仓库都显式写了 `base`。走「合入 develop 自动部署测试环境」流程的项目用
  `develop`；没有 develop 的仓库留空，会自动落到仓库默认分支。
- **`SKEIN_TEST_ENVS` 以仓库 slug 为键**，控制面启动时换算成平台名，显示在工单的交付卡片上
  （「测试环境 ↗」）。没有 Web 界面的项目不要配，配了是死链。
- **`SKEIN_SSO_REQUIRE_INTERNAL=1`**：这个团队只让自己的成员登录。如果产品面向公开用户、
  想让用户也能查看自己提的反馈，设为 `0`。

发布流程（团队约定，不由雁阵强制执行）：

```text
worker 推修复分支 → 控制面开草稿 PR（目标 develop）→ 合入 develop 触发测试环境自动部署
→ 人工在测试环境验证 → 再开一个 develop → main 的 PR，人工审批 → 上正式环境
```

## 执行节点

节点是一个长驻容器（没有 systemd），`~/node-agent.env`：

```ini
CONTROLLER_URL=wss://skein.example.com/api/agent/channel
AGENT_TOKEN=<与控制面 SKEIN_TOKEN 相同>
NODE_ID=node-1
NODE_NAME=主节点
PLATFORMS=project-a,project-a-forum,project-b,project-c,project-d,project-e
REPO_MAP='{"project-a":"/home/agent/repos/project-a","project-a-forum":"/home/agent/repos/project-a","project-b":"/home/agent/repos/project-b", …}'
MAX_WORKERS=2
CLAUDE_BIN=/home/agent/.local/node/bin/claude
SKEIN_CONTROL_URL=https://skein.example.com
SKEIN_TOKEN=<同一个 SKEIN_TOKEN>
PUSH_BRANCH=1
```

- **每个仓库一把可写的 deploy key**，并在克隆上用 `core.sshCommand` 指定。否则 git 会用默认的
  `id_ed25519`，推到没有授权的仓库时失败——而 worker 的报告里看不出这一点。
- **`MAX_WORKERS` 是并发上限**，也是模型额度的消耗上限。卡死的 worker 会一直烧额度，
  所以取消派单时节点会真的终止进程组（SIGTERM，10 秒后 SIGKILL）并清理工作目录。
- **`REPO_MAP` 的值要带单引号**，原因和改法见 [`deploy/pod/README.md`](../../deploy/pod/README.md)。
- 控制面能派的平台 = 在线节点上报的 `PLATFORMS`。改了 env 要重启 node-agent。
- 容器里通常开不了 mount namespace，隔离边界就是容器本身：用专用容器，不和其它服务混部。

## 测试环境的自动部署（与雁阵衔接的外部部分）

雁阵只负责到「草稿 PR」。合入 develop 之后的测试环境部署由团队自己的部署平台完成，
这里的做法是：代码托管配置 webhook → 部署平台按「仓库 + 分支」匹配部署记录 →
在节点里 `git fetch` + 增量构建 + 重启进程 + 健康检查，失败自动回滚到上一个好的版本。

实测的优化点（热部署从约 20–28 秒降到 12–14 秒）：

- 只 fetch 目标分支，不 `fetch --all`；
- 每次进容器执行命令都有固定开销（约 0.3 秒），把多次小写入合并成一次；
- 配了健康检查的服务，进程管理器的启动确认时间可以调短，以健康检查为准。

## 运维备忘

- **升级**：控制面 `git pull` + 重建控制台 + 重启；节点只需更新 `node-agent.mjs` 后重启
  （重启不影响已推送的分支和 PR）。
- **备份**：控制面每天备份 SQLite；节点上没有不可重建的数据（仓库可重新克隆，分支在远端）。
- **排查派单问题**：先看控制面「运行 → 最近派工」的失败原因；建 PR 失败看审计表里的 `pr_error`；
  worker 自身的失败（模型配额、仓库权限）在工单详情的交付卡片和节点日志里。
- **令牌与密钥**只放在控制面的环境文件和节点的 env 文件里（600），不进仓库、不进文档。
