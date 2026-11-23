# 执行节点部署

一个节点 = 一台能跑编码 Agent 的机器或容器。节点上的 `node-agent` 反向拨入控制面，
按指令在本机为每张工单起一个隔离的 worker 会话。

## 它需要什么

- 能出网：到控制面（WebSocket）、到代码托管（拉/推分支）、到模型网关
- Node 22+、`git`、一个编码 Agent（默认 Claude Code）
- 要服务的代码仓库各一份本地克隆，且**有推送权限**（worker 把修复推成分支，控制面再开 PR）
- 控制面的共享令牌（`SKEIN_TOKEN`，节点用它注册和上报 transcript）

## 一次性准备

```bash
bash setup.sh          # 装 Node / 编码 Agent / 拉取雁阵仓库（放 node-agent 本体）
```

`setup.sh` 可用环境变量覆盖默认值：`NODE_MIRROR`、`NPM_REGISTRY`、`APT_MIRROR`、`SKEIN_REPO`。

### 1. 克隆要服务的仓库

每个「平台」对应一个本地目录。给每个仓库配一把**可写**的 deploy key，
并在克隆上指定用它（多个仓库各有各的 key 时必须这样做，否则会用默认 `id_ed25519`）：

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/<平台>_deploy -C "skein-node <平台> rw"
# 把公钥加到该仓库的 Deploy keys，勾选 Allow write access
git clone git@github.com:<org>/<repo>.git ~/repos/<平台>
git -C ~/repos/<平台> config core.sshCommand \
  "ssh -i $HOME/.ssh/<平台>_deploy -o IdentitiesOnly=yes"
git -C ~/repos/<平台> push --dry-run origin HEAD:refs/heads/_pushcheck   # 验证可写
```

仓库默认停在哪个分支，worker 就从哪个分支拉修复分支。走「PR → develop」流程的，
把克隆切到 `develop`。

### 2. 编码 Agent 的模型网关

`~/.claude/settings.json` 里放模型网关配置（`env` 下的 `ANTHROPIC_BASE_URL` /
`ANTHROPIC_AUTH_TOKEN` 等）。worker 每单一个独立 HOME，这份配置会被拷进去。

### 3. 写 `~/node-agent.env`（权限 600）

```ini
CONTROLLER_URL=wss://skein.example.com/api/agent/channel
AGENT_TOKEN=<控制面的 SKEIN_TOKEN>
NODE_ID=node-1
NODE_NAME=节点一
PLATFORMS=my-service,another-service
REPO_MAP='{"my-service":"/home/me/repos/my-service","another-service":"/home/me/repos/another-service"}'
MAX_WORKERS=2                 # 并发上限（不是会话数；空闲会话不占进程）
CLAUDE_BIN=/home/me/.local/node/bin/claude
SKEIN_CONTROL_URL=https://skein.example.com
SKEIN_TOKEN=<同一个 SKEIN_TOKEN>
```

注意：

- `REPO_MAP` 的值要**带单引号**。`set -a; . env` 会剥掉裸引号，JSON 里的引号一旦被吃掉
  node-agent 就起不来。改它时用 `sed` 整行替换，别手工编辑：
  `sed -i "s|^REPO_MAP=.*|REPO_MAP='{\"my-service\":\"/path\"}'|" ~/node-agent.env`
- 控制面能派哪些平台 = 在线节点上报的 `PLATFORMS`。加平台后必须重启 node-agent 才生效。
- 同一个仓库可以服务多个平台名（`REPO_MAP` 里两个名字指向同一目录）。

### 4. 启动

三种方式，按环境选一种。**推荐前两种**——它们会让 node-agent 常驻：崩溃自动重启。

```bash
# a) 容器（没有 systemd）：交给 supervisord。幂等，可重复执行。
bash install-supervisor.sh

# b) 有 systemd 的主机
systemctl --user enable --now node-agent.service   # 模板见同目录

# c) 临时起一下、不要常驻（调试用）
bash start-node-agent.sh
```

`install-supervisor.sh` 做的事：写 `~/deploy/supervisord.conf`（已存在则不动）和
`~/deploy/programs/skein-node-agent.conf`，然后交给 supervisord。它只从 `~/node-agent.env`
加载配置，**令牌不会写进 conf**。之后：

```bash
supervisorctl -c ~/deploy/supervisord.conf status          # 看状态
supervisorctl -c ~/deploy/supervisord.conf tail skein-node-agent   # 看日志
```

改了 `~/node-agent.env` 后要重启：`supervisorctl -c ~/deploy/supervisord.conf restart skein-node-agent`。

### 5. 在控制面确认

```bash
curl -s -H "X-Skein-Token: $SKEIN_TOKEN" http://127.0.0.1:7800/internal/nodes
```

应看到你的 `NODE_ID`、`status=online`、`platforms` 与 `PLATFORMS` 一致。

## 容器重启或重建后

**用 `nohup`/`start-node-agent.sh` 起的进程不会自己回来**——容器一重启或重建它就没了，
节点一直离线，直到有人再手工起一次。这是实际发生过的故障。所以要常驻：

- 仓库、Node、配置都在 `$HOME` 下，把它挂成卷，容器重建后这些都在。
- `~/deploy/supervisord.conf` 和 `programs/*.conf` 也在 `$HOME` 下。很多容器平台的入口脚本
  会在启动时拉起 `$HOME/deploy/supervisord.conf`（如果存在），这样 supervisord 连同
  node-agent 都会跟着容器自动起来。
- **确认你的平台是否这么做**：容器重建后看一眼 `supervisorctl … status`。如果平台不拉起
  supervisord，就把 `~/.local/bin/supervisord -c ~/deploy/supervisord.conf` 加进容器的启动命令，
  或在镜像/入口里调用一次 `install-supervisor.sh`。

在控制台「运行 → 节点」里节点显示离线，先查这个。

## 隔离边界

每单一个独立 HOME（`~/repos/home-<workerId>`）和独立 git worktree（`wt-<workerId>`），
单子结束或取消后自动清理；修复分支留在共享仓库里，供开 PR 用。

很多容器环境不允许 `unshare`/`bubblewrap`，这时 `SANDBOX_ENABLED` 开不起来，
隔离边界就是**容器本身**——只在一个可信的、专用的容器里跑节点，
不要和其它服务混部。能开 mount namespace 的机器可以打开 `SANDBOX_ENABLED=1` 加强隔离
（见 `docs/sandbox.md`）。

## 权限边界

worker 不继承控制面令牌：`SKEIN_TOKEN`、`AGENT_TOKEN`、`CONTROLLER_URL`、
`SKEIN_CONTROL_URL` 在拉起 worker 时被剔除（`services/node-agent/worker-env.mjs`）。
worker 只看得到工单、仓库与自己 HOME 下的模型配置；不能调控制面的 `/internal/*`。

worker 用 deploy key 推分支——所以**给节点的 key 只需要目标仓库的写权限**，
不要放一个能访问全部仓库的个人账号私钥。
