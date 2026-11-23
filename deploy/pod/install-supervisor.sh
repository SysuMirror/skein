#!/usr/bin/env bash
# 把 node-agent 交给 supervisord 管理（容器里没有 systemd 时的常驻方案）。
#
# 幂等，可重复执行。前提：
#   - 已跑过 setup.sh（有 Node、编码 Agent、雁阵仓库）
#   - ~/node-agent.env 已按 README 填好
#
# 之后 node-agent 由 supervisord 负责：崩溃自动重启；容器重建后，
# 只要容器的入口会拉起 supervisord（很多容器平台默认这么做），它就会跟着起来。
# 不确定平台是否拉起 supervisord 的，启动时手动跑一次：
#   ~/.local/bin/supervisord -c ~/deploy/supervisord.conf
set -euo pipefail

ENV_FILE="$HOME/node-agent.env"
AGENT_DIR="$HOME/repos/skein/services/node-agent"
DEPLOY_DIR="$HOME/deploy"
[ -f "$ENV_FILE" ] || { echo "缺 $ENV_FILE（照 deploy/pod/README.md 填）"; exit 1; }
[ -f "$AGENT_DIR/node-agent.mjs" ] || { echo "缺 $AGENT_DIR（先跑 setup.sh）"; exit 1; }
# 优先用 setup.sh 装的 Node（~/.local/node/bin，版本较新）；非登录 shell 的 PATH 里
# 系统自带的旧 node 可能排在前面，所以不直接用 command -v。
NODE_BIN="$HOME/.local/node/bin/node"
[ -x "$NODE_BIN" ] || NODE_BIN="$(command -v node || true)"
[ -n "$NODE_BIN" ] || { echo "找不到 node（setup.sh 装在 ~/.local/node/bin）"; exit 1; }

mkdir -p "$DEPLOY_DIR/programs" "$DEPLOY_DIR/logs"

# 基础配置：只在不存在时写，避免覆盖已有的
if [ ! -e "$DEPLOY_DIR/supervisord.conf" ]; then
  cat > "$DEPLOY_DIR/supervisord.conf" <<CONF
[unix_http_server]
file=$DEPLOY_DIR/supervisor.sock

[supervisord]
logfile=$DEPLOY_DIR/supervisord.log
pidfile=$DEPLOY_DIR/supervisord.pid
nodaemon=false

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://$DEPLOY_DIR/supervisor.sock

[include]
files = $DEPLOY_DIR/programs/*.conf
CONF
  echo "已写 $DEPLOY_DIR/supervisord.conf"
fi

# 这里用带引号的 here-doc 原样写出模板，再用 sed 填入路径：
# command 里的 $1/$2 是留给运行时的 bash -c 取的，不能在这里被展开。
cat > "$DEPLOY_DIR/programs/skein-node-agent.conf" <<'CONF'
[program:skein-node-agent]
; 先加载 ~/node-agent.env 再启动。不把变量写进这里：env 文件里有令牌，保持它 600、只有一份。
command=/bin/bash -c 'set -a; . "$1"; set +a; exec "$2" node-agent.mjs' skein-agent "@ENV_FILE@" "@NODE_BIN@"
directory=@AGENT_DIR@
autostart=true
autorestart=true
startsecs=5
startretries=10
; node-agent 收到 SIGTERM 会先终止在途 worker 再退出，最多等 10 秒
stopsignal=TERM
stopwaitsecs=15
stopasgroup=false
killasgroup=false
stdout_logfile=@DEPLOY_DIR@/logs/skein-node-agent.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
redirect_stderr=true
CONF
sed -i \
  -e "s|@ENV_FILE@|$ENV_FILE|g" \
  -e "s|@NODE_BIN@|$NODE_BIN|g" \
  -e "s|@AGENT_DIR@|$AGENT_DIR|g" \
  -e "s|@DEPLOY_DIR@|$DEPLOY_DIR|g" \
  "$DEPLOY_DIR/programs/skein-node-agent.conf"
echo "已写 $DEPLOY_DIR/programs/skein-node-agent.conf"

# 若之前用 nohup 手工起过一份，先停掉，避免两个 agent 抢 peer 端口、重复注册。
#
# 怎么认「手工起的」：遍历 /proc，找可执行文件是 node、参数以 node-agent.mjs 结尾的进程，
# 再排除掉 supervisord 自己管理的那个（supervisorctl pid <program> 给出它的 pid）。
# 不做命令行文本匹配：容器入口脚本（PID 1）的命令行里就含有 "supervisord"、"node-agent"
# 等文字，nohup 起的进程被托孤后父进程正是它，按文本匹配会误判；也不能按父进程的
# 可执行文件名判断——supervisord 的 exe 是 python。
SUPCTL="$HOME/.local/bin/supervisorctl"
MANAGED_PID=""
if "$SUPCTL" -c "$DEPLOY_DIR/supervisord.conf" pid >/dev/null 2>&1; then
  MANAGED_PID=$("$SUPCTL" -c "$DEPLOY_DIR/supervisord.conf" pid skein-node-agent 2>/dev/null || echo 0)
fi
manual_agents() {
  local pid exe
  for status in /proc/[0-9]*/status; do
    pid=${status#/proc/}; pid=${pid%/status}
    [ "$pid" = "$MANAGED_PID" ] && continue
    exe=$(readlink "/proc/$pid/exe" 2>/dev/null || true)
    case "$exe" in */node) ;; *) continue ;; esac
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'node-agent\.mjs *$' || continue
    echo "$pid"
  done
}
for pid in $(manual_agents); do
  echo "停掉手工启动的 node-agent (pid $pid)"
  kill "$pid" 2>/dev/null || true
done
for _ in $(seq 1 20); do
  [ -z "$(manual_agents)" ] && break
  sleep 1
done

[ -x "$SUPCTL" ] || { echo "缺 $SUPCTL（pip install --user supervisor）"; exit 1; }
# 判断 supervisord 是否在跑要用 `pid`，不能用 `status`：有 program 处于
# FATAL/BACKOFF 时 status 的退出码是 3，但 supervisord 本身明明活着。
if ! "$SUPCTL" -c "$DEPLOY_DIR/supervisord.conf" pid >/dev/null 2>&1; then
  "$HOME/.local/bin/supervisord" -c "$DEPLOY_DIR/supervisord.conf"
  sleep 1
fi
# program 之前被标成 FATAL（例如第一次启动时端口被手工起的实例占着）时要显式拉起
"$SUPCTL" -c "$DEPLOY_DIR/supervisord.conf" update
"$SUPCTL" -c "$DEPLOY_DIR/supervisord.conf" start "skein-node-agent:" >/dev/null 2>&1 || true
sleep 2
"$SUPCTL" -c "$DEPLOY_DIR/supervisord.conf" status
