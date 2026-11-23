#!/usr/bin/env bash
# 执行节点一次性准备（可重复执行）。适用于没有 systemd 的容器/pod，也适用于普通主机。
# 用法：在节点上 bash setup.sh
#
# 可用环境变量覆盖默认值：
#   NODE_MIRROR   Node 二进制镜像（默认官方 nodejs.org；国内可设 https://npmmirror.com/mirrors/node）
#   NPM_REGISTRY  npm 源（默认官方；国内可设 https://registry.npmmirror.com）
#   SKEIN_REPO    雁阵仓库地址（默认 GitHub 上的 SysuMirror/skein）
#   APT_MIRROR    apt 镜像（留空=不改系统源）
set -euo pipefail

NODE_MIRROR=${NODE_MIRROR:-https://nodejs.org/dist}
NPM_REGISTRY=${NPM_REGISTRY:-https://registry.npmjs.org}
SKEIN_REPO=${SKEIN_REPO:-https://github.com/SysuMirror/skein.git}
REPO_DIR="$HOME/repos/skein"

echo "== 系统依赖 =="
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi
if command -v apt-get >/dev/null 2>&1; then
  if [ -n "${APT_MIRROR:-}" ] && [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then
    $SUDO sed -i "s|http://archive.ubuntu.com/ubuntu/|$APT_MIRROR|g; s|http://security.ubuntu.com/ubuntu/|$APT_MIRROR|g" \
      /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true
  fi
  $SUDO apt-get update -qq
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq bubblewrap openssh-client git
fi

echo "== 目录 =="
mkdir -p "$HOME/repos" "$HOME/.local"

echo "== Node 22+ =="
if [ ! -x "$HOME/.local/node/bin/node" ]; then
  VER=$(curl -fsSL "$NODE_MIRROR/index.json" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(next(x["version"] for x in d if x["lts"]))')
  curl -fSL "$NODE_MIRROR/$VER/node-$VER-linux-x64.tar.xz" -o /tmp/node.tar.xz
  tar -xJf /tmp/node.tar.xz -C "$HOME/.local"
  mv "$HOME/.local/node-$VER-linux-x64" "$HOME/.local/node"
fi
export PATH="$HOME/.local/node/bin:$PATH"
node -v
grep -q '.local/node/bin' "$HOME/.bashrc" || echo 'export PATH="$HOME/.local/node/bin:$PATH"' >> "$HOME/.bashrc"

echo "== 编码 Agent（Claude Code）=="
npm config set registry "$NPM_REGISTRY" >/dev/null
command -v claude >/dev/null || npm i -g @anthropic-ai/claude-code
claude --version

echo "== 雁阵仓库（放 node-agent 本体）=="
if [ ! -d "$REPO_DIR/.git" ]; then
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  ssh-keyscan -t ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true
  git clone "$SKEIN_REPO" "$REPO_DIR"
fi
git -C "$REPO_DIR" pull --ff-only

echo "== Agent 网关配置 =="
if [ ! -f "$HOME/.claude/settings.json" ]; then
  echo "缺 $HOME/.claude/settings.json（env: ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL）。"
  echo "这是编码 Agent 用的模型网关配置，按你的模型供应商填好再继续。"
  exit 1
fi

echo
echo "== 完成。接下来："
echo "  1) 把要服务的代码仓库克隆到 ~/repos/（每个平台一个目录）"
echo "  2) 给每个仓库配好可推送的 deploy key（worker 要推修复分支）"
echo "  3) 照 deploy/pod/README.md 写 ~/node-agent.env"
echo "  4) bash install-supervisor.sh   （让 node-agent 常驻；临时调试可用 start-node-agent.sh）"
