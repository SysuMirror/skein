#!/usr/bin/env bash
# 启动/重启节点上的 node-agent。没有 systemd 的容器用 setsid 常驻；
# 有 systemd 的主机建议改用 deploy/pod/node-agent.service。
# 前提：~/node-agent.env 已按 README 填好。
set -euo pipefail

ENV_FILE="$HOME/node-agent.env"
AGENT_DIR="$HOME/repos/skein/services/node-agent"
LOG="$HOME/node-agent.log"
[ -f "$ENV_FILE" ] || { echo "缺 $ENV_FILE（照 deploy/pod/README.md 填）"; exit 1; }
[ -f "$AGENT_DIR/node-agent.mjs" ] || { echo "缺 $AGENT_DIR（先跑 setup.sh）"; exit 1; }

# 只停旧的 node-agent，不碰其它 node 进程（worker 的编码 Agent 也是 node）
OLD=$(pgrep -f "node node-agent.mjs" || true)
if [ -n "$OLD" ]; then
  kill $OLD
  for _ in 1 2 3 4 5; do pgrep -f "node node-agent.mjs" >/dev/null || break; sleep 1; done
fi

cd "$AGENT_DIR"
set -a; . "$ENV_FILE"; set +a
export PATH="$HOME/.local/node/bin:$PATH"
setsid nohup node node-agent.mjs >> "$LOG" 2>&1 < /dev/null &
sleep 2
tail -n 5 "$LOG"
