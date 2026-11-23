# 控制面（services/control）

> 2026-11-21 · 重构后的当前状态：不绑定任何部署专属系统、无影子对比、无旧版兼容；控制面只做雁阵自己的事。

## 一、模块

| 模块 | 文件 | 说明 |
| --- | --- | --- |
| 领域库 | `skein/db.py` | SQLite WAL，schema v12；工单/线程/事件/运行/调用/Agent 消息 |
| 准入 | `skein/admission.py` | 确定性 ignore/record/process；不调模型 |
| 控制台 Agent | `skein/agent.py` | 管理员用自然语言查/操作雁阵；工具调用 + 审计 + 调用留痕 |
| 工单/线程 | `skein/store.py` | Thread 归并、建单、去重 |
| 节点通道 | `skein/channel.py` | WebSocket 注册/心跳/建 worker/派单/结果；多节点 |
| 调度 | `skein/scheduler.py` | 在线窗口、容量、平台匹配 |
| 定时作业 | `skein/driver.py`、`runs.py` | cron 到点执行，按计划分钟去重 |
| Watchdog | `skein/watchdog.py`、`watchdog_agent.py` | 确定性信号 + 自动建单 + 每日巡检 |
| 会话权威 | `skein/session_store.py` | 索引 + 内容寻址 blob；逐轮幂等 |
| 登录鉴权 | `skein/auth.py`、`skein/auth_providers/` | 可插拔。不配 SSO 即令牌模式：共享令牌 `SKEIN_TOKEN`，人在控制台填同一个令牌登录，功能完整；配了 `SKEIN_SSO_CLIENT_ID` / `SKEIN_SSO_CLIENT_SECRET` 才启用 SSO。核心只有 `Principal`、`AccessPolicy`、`SessionVerifier` 与 `IdentityProvider` 协议；UniSSO 是内置 provider（`auth_providers/unisso.py`），注册表在 `auth_providers/__init__.py`。`skein/sso.py` 是兼容层，现由这两者实现 |
| 聊天代理（可选集成） | `skein/integrations/chat_proxy.py`、`skein/qa_proxy.py` | 把一个外部聊天服务挂到控制面后面：配了 `SKEIN_QA_CHAT_UPSTREAM` 才挂载 `/api/v1/agent/*`、`/task/*`，转发并带短时签名身份头；不配则路由不存在 |
| 调用留痕 | `skein/llm_trace.py` | 聊天服务与控制台 Agent 上报的输入、输出、tokens、耗时 |
| 草稿 PR | `skein/forge.py` | worker 推送修复分支后，控制面用 GitHub API 建 draft PR；基准分支按「仓库配置 → 全局配置 → 自动」解析 |
| 控制台 | `services/console/` | Vue 3；工作台 / Agent / 需求 / 运行（原「平台」页已删除） |

已删除：`poll.py`、`ingest.py`、`forum_source.py`（改为外部入口建单 `/internal/intake`）、
`shadow.py`、`review.py`（影子对比）、`compat.py`（旧版写入口）、
`host.py`、`router/`（旧 Router 常驻链路）、`outbox.py`、`channels.py`（无消费者的对外写）、
旧控制台、旧的部署脚本与旧面板迁移脚本。

## 二、跑与测

```bash
make test        # 单元测试
make lint        # ruff
make run-control # 控制面（HTTP；不配 SSO 就是令牌模式）
```

## 三、环境变量

| 变量 | 默认 | 用途 |
| --- | --- | --- |
| `SKEIN_DB` | `data/skein.db` | 领域库路径 |
| `SKEIN_BLOB_ROOT` | `data/blobs` | transcript 内容寻址目录 |
| `SKEIN_TOKEN` | 空 | 共享令牌：节点与机器接口用 `X-Skein-Token`；令牌模式下人在控制台登录页填同一个令牌。为空且未配 SSO 则机器接口 401 |
| `SKEIN_HOST` / `SKEIN_PORT` | `127.0.0.1` / `7800` | 监听地址 |
| `SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` | 空 | OpenAI 兼容网关；Agent 与 Watchdog 共用 |
| `SKEIN_AGENT_MODEL` | `deepseek-v4-flash-vision-exp` | 控制台 Agent 模型 |
| `SKEIN_DISPATCH` | `0` | 历史遗留，**当前无效果**：控制面读取它但没有任何逻辑使用。自动派单由 Watchdog 的 `SKEIN_WATCHDOG_DISPATCH` 控制 |
| `SKEIN_DRIVER` / `SKEIN_JOBS_FILE` | `0` / 空 | 定时作业驱动 |
| `SKEIN_PLATFORM_REPOS` | 空 | 平台 → 仓库路径 JSON |
| `SKEIN_WATCHDOG*` | 关 | 信号、自动建单、每日巡检 |
| `SKEIN_SSO_PROVIDER` | `unisso` | SSO provider 名字，须在 `skein/auth_providers/__init__.py` 的注册表里登记过 |
| `SKEIN_SSO_BASE` | 空 | SSO 服务地址 |
| `SKEIN_SSO_CLIENT_ID` / `SKEIN_SSO_CLIENT_SECRET` | 空 | 应用凭据；**两者都配了才启用 SSO**，否则是令牌模式 |
| `SKEIN_SSO_COOKIE_NAME` | 空 | 会话 Cookie 名；不填用 provider 自己的缺省 |
| `SKEIN_COOKIE_DOMAIN` | 空 | Cookie 域。不填=按 SSO 响应种；`host`=不带 Domain（本地 http 调试） |
| `SKEIN_SSO_REQUIRE_INTERNAL` | `1` | 是否要求主体是「内部成员」。`1`=要求（与历史行为一致）；面向公开用户的部署设 `0` |
| `SKEIN_QA_CHAT_UPSTREAM` | 空 | 聊天代理（可选集成）的上游，即一个外部聊天服务的地址；配了才挂载 `/api/v1/agent/*`、`/task/*` |
| `SKEIN_ASSERTION_KEY` | 空 | 给聊天上游签短时身份头 `x-skein-user` 的密钥；须与上游校验用的密钥相同 |
| `SKEIN_INTAKE_NOTIFY_URL` | 空 | 来源为外部社区帖/评论的工单公开状态变化时，回帖通知该社区的上游；不配时退回 `SKEIN_QA_CHAT_UPSTREAM`，两者都没有则不回帖 |
| `SKEIN_GITHUB_TOKEN` | 空 | GitHub API 令牌；配置后 worker 推送分支的结果自动建草稿 PR（`skein/forge.py`） |
| `SKEIN_GITHUB_REPOS` | 空 | platform → `owner/repo` 或 `{"slug": "owner/repo", "base": "..."}` JSON；remote 不是 GitHub 时的兜底，`base` 是该仓库的 PR 基准分支 |
| `SKEIN_GITHUB_BASE` | 空（自动） | PR 基准分支的全局配置。解析顺序：仓库配置 → 本项 → 自动（仓库有 `develop` 用 `develop`，否则用仓库默认分支） |
| `SKEIN_GITHUB_API` | `https://api.github.com` | GitHub API 地址 |
| `SKEIN_GITHUB_PR_DRAFT` | `1` | 是否建 draft PR |


鉴权核心（`skein/auth.py`）里没有任何默认域名或 Cookie 名，全部来自配置。入口级别分 `admin`（控制台/管理接口）与 `user`（面向普通用户的接口，历史别名 `market`）；管理入口默认要求主体带 admin 标记（`AccessPolicy.admin_requires_admin`），是否还要求「内部成员」由 `SKEIN_SSO_REQUIRE_INTERNAL` 决定。新增一种 SSO 只需加一个实现 `IdentityProvider` 协议的模块并在注册表登记，不用改核心。

## 四、接口

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/health/live` | 无 | 存活 |
| GET | `/health/ready` | 无 | DB 就绪 |
| GET | `/api/session` | 无 | 登录态探测：令牌模式返回 `{"mode":"token"}`；SSO 模式回验 Cookie 会话（未登录/会话无效 401），`surface=market`（别名 `user`）按 user 级别校验 |
| POST | `/api/session/login` | 无 | 邮箱+密码 → SSO provider → 种 Cookie；未配 SSO 返回 404 |
| POST | `/api/session/logout` | 无 | 注销上游会话（上游不可达也照清）并清 Cookie |
| GET/POST 等 | `/api/v1/agent/*`、`/task/*` | 由上游鉴权 | 聊天代理（可选集成）：仅配了 `SKEIN_QA_CHAT_UPSTREAM` 时存在；原样转发到上游，调用方凭据透传、Cookie 不转发，已登录者附签名身份头 |
| GET | `/internal/summary` | 令牌/管理员 | 计数概览 |
| GET | `/internal/stream` | 令牌/管理员 | SSE 快照 |
| POST | `/internal/tickets` | 令牌 | 外部建单（Watchdog/脚本） |
| POST | `/internal/intake` | 令牌 | 外部入口建单（始终存在）：来源/thread/requester/摘要；dedup 幂等，评论/楼中楼自动 followup。回帖通知外部社区是可选的，见 `SKEIN_INTAKE_NOTIFY_URL` |
| POST | `/internal/tickets/{id}/dispatch` | 令牌/管理员 | 派给节点 |
| POST | `/internal/tickets/{id}/cancel` | 令牌/管理员 | 取消在途派工 |
| POST | `/internal/tickets/{id}/review` | 管理员 | approve / reject |
| POST | `/internal/agent/chat` | 管理员会话（SSO 模式）/ 令牌持有者（令牌模式） | 控制台 Agent 对话 |
| GET | `/internal/agent/thread/{id}` | 同上 | Agent 历史 |
| POST | `/internal/llm/calls` | 令牌 | 上报一次模型调用 |
| GET | `/internal/llm/sessions` / `calls` | 令牌/管理员 | 会话与调用明细 |
| GET | `/internal/nodes` / `signals` | 令牌/管理员 | 节点/信号 |
| POST | `/internal/worker/turn` | 令牌 | transcript 上传 |
| WS | `/api/agent/channel` | 协议内 token | 节点反向拨入 |

## 五、数据

`events`、`threads`、`tickets`、`assignments`、`runs`、
`prompt_revisions`、`prompt_pointers`、`audit`、`kv`、`sessions`、`session_turns`、
`dispatches`、`nodes`、`llm_calls`、`agent_messages`。

## 六、高可用与备份

- `skein-control` systemd `Restart=always`；节点 WebSocket 自动重连，重启不丢状态。
- 每日 `deploy/scripts/backup-skein.sh` 用 SQLite backup API 做一致性快照，目录取 `SKEIN_BACKUP_DIR`（脚本缺省 `/srv/skein/backups`），保留 `SKEIN_BACKUP_DAYS` 天（缺省 7）。
- 单机 SQLite 仍是单写点；要多控制面需换 Postgres。当前规模用「进程自愈 + 每日备份 + 多节点执行」满足。
