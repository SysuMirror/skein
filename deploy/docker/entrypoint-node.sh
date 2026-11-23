#!/usr/bin/env bash
# Node container entrypoint: create the demo repository on the persistent volume once,
# then hand over to the node agent.
set -euo pipefail

ROOT=${DEMO_REPO_ROOT:-/srv/repos}
APP="$ROOT/app"

if [ ! -d "$ROOT/origin.git" ]; then
  echo "[demo] creating the demo repository under $ROOT"
  mkdir -p "$ROOT"
  git init --bare -q "$ROOT/origin.git"
  git clone -q "$ROOT/origin.git" "$APP"
  cd "$APP"
  git config user.email "demo@example.com"
  git config user.name "Skein demo"
  printf '# Demo app\n\nA tiny repository the demo worker fixes.\n' > README.md
  printf 'def add(a, b):\n    return a - b  # FIXME: should be a + b\n' > calculator.py
  git add README.md calculator.py
  git commit -q -m "demo app with a deliberately broken add()"
  git branch -M main
  git push -q origin main
  git checkout -q main
fi

cd "$APP"
exec node /app/node-agent/node-agent.mjs
