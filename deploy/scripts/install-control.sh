#!/usr/bin/env bash
# Skein control plane installer: data dir → env file → systemd units → start → checks.
# Idempotent: re-running overwrites the env file and units (each keeps a .bak-<ts> copy).
#
# Zero configuration works: with no SKEIN_* variables at all this is "token mode" — the
# console signs in with the shared token. Put any of the variables below in the calling
# environment to enable more; they are written to the env file verbatim. The full list
# with explanations lives in .env.example at the repository root:
#   login:      SKEIN_LOCAL_LOGIN, SKEIN_SSO_PROVIDER / _BASE / _CLIENT_ID / _CLIENT_SECRET,
#               SKEIN_SSO_COOKIE_NAME, SKEIN_COOKIE_DOMAIN, SKEIN_SSO_REQUIRE_INTERNAL,
#               SKEIN_SESSION_TTL_HOURS, SKEIN_SESSION_COOKIE_SECURE, SKEIN_AUTH_AUTO_ROLE
#   assistants: SKEIN_ASSISTANTS (JSON), SKEIN_PUBLIC_URL, SKEIN_API_RATE_LIMIT,
#               SKEIN_API_TRACE, SKEIN_API_DOCS
#   repos/PRs:  SKEIN_FORGE, SKEIN_GITHUB_TOKEN, SKEIN_GITHUB_REPOS, SKEIN_GITHUB_BASE,
#               SKEIN_TEST_ENVS, SKEIN_PLATFORM_REPOS
#   agent/LLM:  SKEIN_LLM_BASE, SKEIN_LLM_KEY, SKEIN_AGENT_MODEL
#   watchdog:   SKEIN_WATCHDOG*, SKEIN_DAILY_TICKET_HOUR, SKEIN_TICKET_DAILY_CAP
#   ops:        SKEIN_LOG_FORMAT, SKEIN_QA_CHAT_UPSTREAM, SKEIN_ASSERTION_KEY
# Credentials only ever land in the env file (600) and are never echoed.
set -euo pipefail

SKEIN_REPO=${SKEIN_REPO:-/opt/skein}
SKEIN_ENV=${SKEIN_ENV:-/etc/skein/env}
SKEIN_DATA=${SKEIN_DATA:-/var/lib/skein}
SKEIN_DB="$SKEIN_DATA/skein.db"
UNIT_DIR=${UNIT_DIR:-/etc/systemd/system}
LISTEN=${SKEIN_LISTEN:-127.0.0.1:7800}
TS=$(date +%Y%m%d%H%M%S)

# 可选变量：调用环境里有才写入。格式：变量名|是否需要 shell 引号
OPTIONAL=(
  "SKEIN_SSO_PROVIDER" "SKEIN_SSO_BASE" "SKEIN_SSO_CLIENT_ID" "SKEIN_SSO_CLIENT_SECRET"
  "SKEIN_SSO_COOKIE_NAME" "SKEIN_COOKIE_DOMAIN" "SKEIN_SSO_REQUIRE_INTERNAL"
  "SKEIN_LOCAL_LOGIN" "SKEIN_AUTH_AUTO_ROLE" "SKEIN_SESSION_TTL_HOURS"
  "SKEIN_SESSION_COOKIE_NAME" "SKEIN_SESSION_COOKIE_SECURE"
  "SKEIN_ASSISTANTS" "SKEIN_PUBLIC_URL" "SKEIN_API_RATE_LIMIT" "SKEIN_API_TRACE"
  "SKEIN_API_DOCS" "SKEIN_LOG_FORMAT" "SKEIN_FORGE"
  "SKEIN_QA_CHAT_UPSTREAM" "SKEIN_ASSERTION_KEY"
  "SKEIN_GITHUB_TOKEN" "SKEIN_GITHUB_BASE" "SKEIN_LLM_BASE" "SKEIN_LLM_KEY"
  "SKEIN_AGENT_MODEL"
  "SKEIN_WATCHDOG" "SKEIN_WATCHDOG_AUTO" "SKEIN_WATCHDOG_INTERVAL"
  "SKEIN_WATCHDOG_TICKETS" "SKEIN_WATCHDOG_DISPATCH"
  "SKEIN_DAILY_TICKET_HOUR" "SKEIN_TICKET_DAILY_CAP"
)
# JSON 类变量：值里有引号/空格，必须整体加单引号
JSON_VARS=" SKEIN_GITHUB_REPOS SKEIN_TEST_ENVS SKEIN_PLATFORM_REPOS SKEIN_ASSISTANTS "

emit_optional() {
  local name="$1"
  [ -n "${!name:-}" ] || return 0
  if [[ "$JSON_VARS" == *" $name "* ]]; then
    printf "%s='%s'\n" "$name" "${!name}"
  else
    printf "%s=%s\n" "$name" "${!name}"
  fi
}

echo "== 1/5 初始化数据目录 =="
install -d -m 700 "$SKEIN_DATA"

echo "== 2/5 写 $SKEIN_ENV =="
install -d -m 700 "$(dirname "$SKEIN_ENV")"
if [ -f "$SKEIN_ENV" ]; then cp "$SKEIN_ENV" "$SKEIN_ENV.bak-$TS"; fi
{
  echo "SKEIN_DB=$SKEIN_DB"
  echo "SKEIN_BLOB_ROOT=$SKEIN_DATA/blobs"
  echo "SKEIN_HOST=${LISTEN%:*}"
  echo "SKEIN_PORT=${LISTEN##*:}"
  echo "SKEIN_CONTROL_URL=http://$LISTEN"
  # 没给令牌就生成一个；只在这里出现一次，请立刻保存
  echo "SKEIN_TOKEN=${SKEIN_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
  for name in "${OPTIONAL[@]}"; do emit_optional "$name"; done
} > "$SKEIN_ENV"
chmod 600 "$SKEIN_ENV"

echo "== 3/5 装 systemd unit 与命令行 =="
for unit in skein-control skein-watchdog; do
  target="$UNIT_DIR/$unit.service"
  [ -f "$target" ] && cp "$target" "$target.bak-$TS"
  sed -e "s|@SKEIN_REPO@|$SKEIN_REPO|g" -e "s|@SKEIN_ENV@|$SKEIN_ENV|g" \
    "$SKEIN_REPO/deploy/systemd/$unit.service" > "$target"
done
systemctl daemon-reload
systemctl enable --now skein-control

# 管理命令行：/usr/local/bin/skein（读同一个环境文件，走控制面的 HTTP 管理接口）
if [ -x "$SKEIN_REPO/deploy/scripts/skein-cli" ] || chmod +x "$SKEIN_REPO/deploy/scripts/skein-cli" 2>/dev/null; then
  cat > /usr/local/bin/skein <<CLI
#!/usr/bin/env bash
set -euo pipefail
export SKEIN_HOME="$SKEIN_REPO" SKEIN_ENV_FILE="$SKEIN_ENV"
exec "$SKEIN_REPO/deploy/scripts/skein-cli" "\$@"
CLI
  chmod 755 /usr/local/bin/skein
fi
# watchdog（外部自维护回路）按需启用：systemctl enable --now skein-watchdog

echo "== 3b/5 定时备份 =="
if [ -x "$SKEIN_REPO/deploy/scripts/backup-skein.sh" ] || chmod +x "$SKEIN_REPO/deploy/scripts/backup-skein.sh" 2>/dev/null; then
  (crontab -l 2>/dev/null | grep -v 'backup-skein.sh';
   echo "17 3 * * * SKEIN_DB=$SKEIN_DB $SKEIN_REPO/deploy/scripts/backup-skein.sh") | crontab -
fi

echo "== 4/5 自检 =="
sleep 2
curl -fsS "http://$LISTEN/health/live"
echo
mode=$(curl -s "http://$LISTEN/api/session" | sed -n 's/.*"mode":"\([a-z]*\)".*/\1/p')
case "${mode:-unknown}" in
  token) echo "login: token mode — sign in with SKEIN_TOKEN from $SKEIN_ENV" ;;
  local) echo "login: local accounts — create the first one: skein user create --email you@example.com --role owner --password-stdin" ;;
  sso)   echo "login: external login provider configured" ;;
  *)     echo "login: unknown (check: journalctl -u skein-control)" ;;
esac

echo "== 5/5 完成 =="
echo "查看令牌：grep '^SKEIN_TOKEN=' $SKEIN_ENV"
