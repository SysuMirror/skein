# Skein（雁阵）

[English](README.md) | [简体中文](README.zh-CN.md)

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Skein**（中文名「雁阵」：雁群飞行时排成的队形）是一个自托管的控制面：把工单变成由编码
Agent 在自己节点上完成的修复分支与草稿 PR。

它接收一路真实的反馈——论坛帖、聊天消息、监控告警——变成**工单**。每张工单被派给运行在
你自己机器（**节点**）上的编码 Agent：一个隔离的 **Worker** 在独立的 git worktree 里改代码、
提交、推送修复分支；控制面把结果开成草稿 PR，交到人面前。人决定通过还是驳回。**没有任何东西
会自己合入。**

## 它解决什么

- **反馈散落各处。** 需求和缺陷分散在论坛帖、聊天记录和某个人的记忆里；同一件事被报三次，
  副本各自漂移。
- **Agent 改了什么看不见。** 代码被改过——哪条分支、哪些提交、改了什么、到底跑没跑通？没有
  记录，验收就只能靠猜。
- **Agent 需要一块地方干活。** 机器、仓库、凭据、隔离的工作目录，以及一个能停下来的办法。
  每来一个请求都手工安排一遍，撑不过第一个下午。

Skein 做的是中间这一段：带来源的工单、隔离的执行、可交付的结果，以及一道人审的闸门。

## 它怎么工作

```text
反馈 / 事件 ──► 工单 ──► 派给节点上的编码 Agent ──► 修复分支 + 草稿 PR ──► 人验收
     ▲                                                                        │
     └────────────────────── 外部助手、控制台、Watchdog ◄─────────────────────┘
```

- **工单（ticket）**——工作单元，记录它从哪来（来源 + 来源编号），重试时按幂等键去重。
- **节点（node）**——主动**拨出**连接控制面的机器（不需要入站端口），上面有它服务的仓库克隆。
- **Worker**——一张工单一个编码 Agent 会话，独占一个 `git worktree` 和一个 HOME，结束后被杀掉
  并清理。
- **派单（dispatch）**——把工单交给某个「平台」（节点服务的一个仓库）；控制面挑一个有容量的
  节点，剩下的由 Worker 完成。
- **草稿 PR**——Worker 推送分支后由控制面按集成分支开出来。Skein 从不合入。

## 快速开始

**五分钟看完整闭环**（内置 mock 编码 Agent，不需要模型 key）：

```bash
git clone https://github.com/SysuMirror/skein.git && cd skein
docker compose up --build
# 控制台：http://localhost:7800/console/   令牌：demo-master-token
docker compose logs -f demo                  # 一张单走完整条流水线
```

**或者从源码跑**（Python 3.10+、Node 22+）：

```bash
make install && make build-console && make run-control
# 控制台：http://127.0.0.1:7800/console/   令牌：dev-token
```

**再接一个真正的节点**——一台装了编码 Agent（默认 Claude Code）并克隆了你的仓库的机器——
真正做一单：

```bash
bash deploy/pod/setup.sh                  # 在节点上：环境检查、~/repos、起始环境文件
bash deploy/pod/install-supervisor.sh     # 让节点代理常驻
```

完整步骤见英文版 [docs/quickstart.md](docs/quickstart.md)（中文译文尚未跟上）；节点细节：[deploy/pod/README.md](deploy/pod/README.md)。

**接入一个助手**，让闭环不必从「有人手打一张单」开始：

```bash
skein assistant issue --name forum-bot --purpose "turns forum posts into tickets"
curl -s -X POST $SKEIN_URL/api/v1/tickets \
  -H "Authorization: Bearer $SKEIN_ASSISTANT_KEY" -H "Content-Type: application/json" \
  -d '{"title":"Cannot upload avatars","source":"forum","external_id":"post:42"}'
```

五分钟接入教程与接口参考：[docs/assistants.md](docs/assistants.md)。

## 核心概念

| 概念 | 是什么 |
| --- | --- |
| **工单 Ticket** | 一个请求，带来源、提出人、线索与状态（`staged → assigned → running → review → done / rejected`）。 |
| **线索 Thread** | 工单来自的那段对话；后续消息会并入在途工单。 |
| **平台 Platform** | 节点服务的一个仓库。派单时要选平台。 |
| **节点 Node** | 装了节点代理、仓库克隆与编码 Agent 的机器。 |
| **Worker** | 节点上的一个 Agent 会话：一张单、独立 worktree 与 HOME、自动清理。 |
| **派单 Dispatch** | 工单 → 节点的分配，带上平台、状态、Worker 以及后来的 PR。 |
| **助手 Assistant** | 用自己的凭证、经 `/api/v1` 提交工单的外部程序。 |
| **Watchdog** | 控制面之外的可选回路：把事实（派单超时、作业失败）变成信号，需要时变成工单。 |

## 架构

```text
services/control     控制面（Python/FastAPI，一个进程，唯一写入者）
  ├─ 工单、线索、准入、审计                    SQLite（一个文件）+ 内容寻址的 transcript
  ├─ 节点通道（WebSocket）                     节点拨入；派单、进度、结果
  ├─ /api/v1（助手）、/internal（控制台）       两套身份，互不通用
  └─ 控制台挂在 /console/
services/console     控制台（Vue 3 + naive-ui）：工作台、需求、运行、Agent、助手
services/node-agent  节点代理（Node）：反向通道、worktree 隔离、Agent 会话
deploy/              systemd 单元、节点安装脚本、Docker 镜像、nginx 模板
docs/                运维与设计文档（英文权威；译文在 docs/zh-CN）
```

三份权威，互不合并：领域库（SQLite）、会话库（transcript 是内容寻址文件）、代码（分支在你
的代码托管平台上）。改动各部分如何衔接之前，先读 [docs/architecture.md](docs/architecture.md)。

## 配置概览

所有开关都可选；一个都不配就是**令牌模式**（用主令牌登录），核心闭环照常可用。部署最常碰的：

| 变量 | 用途 |
| --- | --- |
| `SKEIN_TOKEN` | 主令牌：机器、节点、命令行；令牌模式下人用它登录 |
| `SKEIN_SSO_*` / `SKEIN_LOCAL_LOGIN` | 用外部登录系统或本地账号登录 |
| `SKEIN_GITHUB_TOKEN` + `SKEIN_GITHUB_REPOS` | Worker 推送后自动开草稿 PR |
| `SKEIN_TEST_ENVS` | 在交付卡片上给测试环境链接 |
| `SKEIN_ASSISTANTS`、`SKEIN_PUBLIC_URL` | 助手凭证与工单链接 |
| `SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` | 控制台 Agent 的模型网关 |
| `SKEIN_WATCHDOG*` | 可选的外部控制回路 |

带注释的清单在 [`.env.example`](.env.example)；逐项说明见
[docs/configuration.md](docs/configuration.md)（暂无译文）。

## 文档

从 [docs/zh-CN/README.md](docs/zh-CN/README.md) 进入中文译文；英文权威版在
[docs/README.md](docs/README.md)。几份最常看的：

- [docs/quickstart.md](docs/quickstart.md) —— 演示、源码、真节点、助手
- [docs/architecture.md](docs/architecture.md) —— 三个部分与它们的边界
- [docs/authentication.md](docs/authentication.md) —— 三种登录方式、角色与权限矩阵
- [docs/assistants.md](docs/assistants.md) —— 公共 API
- [docs/deployment.md](docs/deployment.md) —— 安装、升级、备份、自检
- [docs/configuration.md](docs/configuration.md) —— 每一个开关
- [docs/extending.md](docs/extending.md) —— 加一种登录方式、代码托管、模型网关、通知通道

中文译文在 [docs/zh-CN/](docs/zh-CN/README.md)，可能落后于英文版，目录里如实标注了状态。

## 状态

早期公开发布（`0.1.0`）。核心闭环——入口建单、工单、派单、隔离的 Worker、交付、验收——已经
实现并有真实部署在跑；助手凭证与带签名的回调、三种登录方式、按角色的权限同样已经实现。
明确**尚未实现**、并在文档里如实写明的：Watchdog 的提示词修订接入 Worker、gVisor 级隔离、
跨节点续接会话、回调重试。见 [CHANGELOG.md](CHANGELOG.md) 与
[docs/architecture.md](docs/architecture.md) 最后一节。

## 安全

凭据只放在仓库之外的一个环境文件里。Worker 拿不到控制面的令牌；助手凭证与主令牌分开；
每个请求都按角色与权限判定。口令如何存储、限速、轮换，以及怎么私下报告漏洞：见
[SECURITY.md](SECURITY.md)。

## 贡献

[CONTRIBUTING.md](CONTRIBUTING.md) 写了开发流程、测试（必须断网可跑）、提交约定与
`develop → main` 的发布流程。提问与想法见 [SUPPORT.md](SUPPORT.md)。本项目遵循
[贡献者公约](CODE_OF_CONDUCT.md)。

## 许可证

[Apache-2.0](LICENSE)。
