# 部署

> 三部分：控制面（必装）、执行节点（要有 worker 就得装）、可选集成（按需）。
> 最小部署只需要控制面 + 一个共享令牌。

## 一、控制面

依赖：Python 3.10+（`pip install -e services/control` 或直接 `PYTHONPATH=services/control/src`）、Node 18+（构建控制台）。

```bash
git clone https://github.com/SysuMirror/skein.git /opt/skein
cd /opt/skein/services/console && npm ci && npm run build
sudo bash /opt/skein/deploy/scripts/install-control.sh
```

`install-control.sh` 是幂等的，做四件事：

1. 建数据目录（`/var/lib/skein`，700）；
2. 写环境文件（`/etc/skein/env`，600；已有则备份为 `.bak-<ts>`），没给 `SKEIN_TOKEN` 就生成一个；
3. 装 `skein-control.service`（模板在 `deploy/systemd/`）并启动；
4. 装每日备份的 cron（03:17，保留 7 天）。

**不传任何变量就是令牌模式**：装完打开控制台，用环境文件里的 `SKEIN_TOKEN` 登录即可。
要启用 SSO 等能力，把对应变量放在调用环境里再跑脚本，例如：

```bash
sudo SKEIN_SSO_BASE=https://sso.example.com \
     SKEIN_SSO_CLIENT_ID=… SKEIN_SSO_CLIENT_SECRET=… \
     bash /opt/skein/deploy/scripts/install-control.sh
```

不使用 systemd 或想手工部署的，照下面的环境变量自己起 `python -m skein` 即可。
控制台随控制面托管：`services/console/dist` 存在就挂在 `/console/`。

对外用 nginx 反代时参考 `deploy/nginx/skein.conf`（注意 SSE 不能缓冲、节点通道是 WebSocket）。

## 二、环境变量

### 必填

| 变量 | 含义 |
| --- | --- |
| `SKEIN_TOKEN` | 共享令牌。节点、外部系统、脚本都用请求头 `X-Skein-Token` 调用；令牌模式下人也用它登录控制台 |

### 基础

| 变量 | 含义 | 缺省 |
| --- | --- | --- |
| `SKEIN_DB` | 领域库路径（SQLite） | `data/skein.db` |
| `SKEIN_BLOB_ROOT` | 会话正文的内容寻址文件根目录 | `data/blobs` |
| `SKEIN_HOST` / `SKEIN_PORT` | 监听地址 | `127.0.0.1:7800` |
| `SKEIN_CONTROL_URL` | 控制面对外地址（节点上报 transcript 用） | 无 |
| `SKEIN_ROUTE_MODEL` | 事件路由用的模型名 | `route` |
| `SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` / `SKEIN_AGENT_MODEL` | 控制台 Agent 用的模型。不配时 Agent 接口返回 503，其余功能不受影响 | 无 |

### 登录鉴权

见 [permission-model.md](permission-model.md)。要点：`SKEIN_SSO_CLIENT_ID` 与
`SKEIN_SSO_CLIENT_SECRET` 都配了才启用 SSO；`SKEIN_SSO_REQUIRE_INTERNAL` 缺省为 `1`。

### 交付：自动开草稿 PR

worker 把修复推成分支后，控制面可以用 GitHub API 开一个草稿 PR：

| 变量 | 含义 | 缺省 |
| --- | --- | --- |
| `SKEIN_GITHUB_TOKEN` | fine-grained token，只需要目标仓库的 `Contents: Read-only` + `Pull requests: Read and write`。不配则不开 PR | 无 |
| `SKEIN_GITHUB_REPOS` | JSON：平台 → 仓库。值可以是 `"owner/repo"`，或 `{"slug": "owner/repo", "base": "develop"}` | `{}` |
| `SKEIN_GITHUB_BASE` | 全局基准分支。留空 = 自动：仓库有 `develop` 就用 `develop`，否则用仓库默认分支 | 自动 |
| `SKEIN_GITHUB_PR_DRAFT` | 是否开成草稿 | `1` |
| `SKEIN_GITHUB_API` | GitHub API 地址（自建 GitHub Enterprise 用） | `https://api.github.com` |
| `SKEIN_TEST_ENVS` | JSON：仓库 → 测试环境地址，显示在工单的交付卡片上 | `{}` |

基准分支的解析顺序：仓库级 `base` → 全局 `SKEIN_GITHUB_BASE` → 自动探测。
**令牌要能读到目标仓库**：private 仓库必须被划进 token 的仓库范围，否则开 PR 会报
`not all refs are readable`。令牌只放控制面，不进节点。

### 节点与派单

| 变量 | 含义 | 缺省 |
| --- | --- | --- |
| `SKEIN_PLATFORM_REPOS` | JSON：平台 → 仓库本地路径，随派单下发给节点。节点自己配了 `REPO_MAP` 时不需要 | `{}` |
| `SKEIN_DISPATCH` | 历史遗留，**当前无效果**：控制面读取它但没有任何逻辑使用。自动派单由 Watchdog 的 `SKEIN_WATCHDOG_DISPATCH` 控制 | `0` |

能派哪些平台 = 控制面仓库映射 ∪ 在线节点上报的平台。派单必须带平台；
工单已有在途派工时拒绝重复派单。

### 外部 Watchdog 与定时作业

| 变量 | 含义 | 缺省 |
| --- | --- | --- |
| `SKEIN_WATCHDOG` | 启用 Watchdog 回路 | `0` |
| `SKEIN_WATCHDOG_AUTO` | 允许 Watchdog 自动提议提示词修订（受改动范围与回滚次数约束）。**暂不要开启**：修订尚未接到 worker 上，也没有升格入口（见 [architecture.md](architecture.md) 第 6 节） | `0` |
| `SKEIN_WATCHDOG_TICKETS` | 巡检发现问题时自动建单 | `0` |
| `SKEIN_WATCHDOG_DISPATCH` | 建单后自动派工 | `0` |
| `SKEIN_WATCHDOG_INTERVAL` | 巡检间隔（秒） | `300` |
| `SKEIN_DAILY_TICKET_HOUR` / `SKEIN_TICKET_DAILY_CAP` | 每日汇总的时刻与建单上限 | `-1` / `5` |
| `SKEIN_DRIVER` | 启用定时作业驱动 | `0` |
| `SKEIN_JOBS_FILE` | 作业配置文件路径（含 `schedules` 与 `commands`） | 无 |
| `SKEIN_SCHEDULES` / `SKEIN_JOB_COMMANDS` | 不用文件时，直接用 JSON 字符串给作业计划与命令 | 无 |
| `SKEIN_JOB_TIMEOUT` | 单个作业超时（秒） | `14400` |

定时作业驱动到点执行命令、按 `(作业名, 计划分钟)` 去重、结果写进 `runs` 表；
作业失败会作为信号留给 Watchdog。`SKEIN_JOBS_FILE` 的格式：

```json
{
  "schedules": [{"job": "nightly-check", "cron": "15 3 * * *"}],
  "commands": {"nightly-check": "/usr/local/bin/nightly-check.sh"}
}
```

Watchdog 作为独立进程运行（`python -m skein.watchdog_agent`，模板 `deploy/systemd/skein-watchdog.service`）。

### 可选集成

**配置了对应变量才启用**；不配就不加载、不暴露路由。

| 变量 | 启用的能力 |
| --- | --- |
| `SKEIN_QA_CHAT_UPSTREAM` | 聊天代理：把一个外部聊天服务挂到控制面后面（`/api/v1/agent/*`、`/task/*`） |
| `SKEIN_ASSERTION_KEY` | 给上面那个上游签短时身份头 `X-Skein-User` 用的密钥 |
| `SKEIN_INTAKE_NOTIFY_URL` | 工单公开状态变化时回调外部社区（如回帖告知「已修复」）。不配时退回用 `SKEIN_QA_CHAT_UPSTREAM`；两者都没有则不回调 |

外部入口建单（`POST /internal/intake`）属于核心能力，始终可用，不需要配置。

## 三、执行节点

见 [`deploy/pod/README.md`](../../deploy/pod/README.md)。要点：

- 节点用 `CONTROLLER_URL`（WebSocket）反向拨入控制面，不需要对节点开放入站端口；
- `REPO_MAP` 把平台名映射到本地仓库克隆，`PLATFORMS` 是它对外声明能接的平台；
- worker 不继承控制面令牌；节点上的 deploy key 只需要目标仓库的写权限。

## 四、升级与回滚

```bash
cd /opt/skein && git pull --ff-only
cd services/console && npm ci && npm run build
sudo systemctl restart skein-control
curl -s http://127.0.0.1:7800/health/ready
```

数据库结构由控制面启动时自动迁移（只加列/表，不删）。回滚代码用 git；
万一需要回退数据库，停服务后用备份替换 `SKEIN_DB`。

## 五、备份

`deploy/scripts/backup-skein.sh` 用 SQLite 的在线备份 API 做一致性快照（WAL 模式下直接拷文件可能不一致）：

```bash
SKEIN_DB=/var/lib/skein/skein.db SKEIN_BACKUP_DIR=/var/lib/skein/backups bash deploy/scripts/backup-skein.sh
```

恢复：停 `skein-control` → 用备份替换 `skein.db`（并清掉旁边的 `-wal` / `-shm`）→ 启动。

## 六、自检

```bash
curl -s http://127.0.0.1:7800/health/live      # 进程活着
curl -s http://127.0.0.1:7800/health/ready     # 数据库可读写
curl -s http://127.0.0.1:7800/api/session      # 令牌模式：{"mode":"token"}；SSO 模式未登录：401
. /etc/skein/env
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://127.0.0.1:7800/internal/summary
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://127.0.0.1:7800/internal/nodes   # 节点 online
node deploy/scripts/console-smoke.mjs          # 控制台冒烟
```

## 七、一个真实的部署长什么样

见 [deploy-case-study.md](deploy-case-study.md)：一个控制面 + 一个容器节点，
服务多个代码仓库、带 SSO 和自动开 PR 的配置思路（全部用占位值）。
