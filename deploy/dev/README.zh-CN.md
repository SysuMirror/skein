# 本地联调

换机器或新同学上手时，按这里把控制面完整跑起来，不碰任何真实凭据。

## 方式一：令牌模式（最省事）

什么都不配，只有一个共享令牌：

```bash
cd services/control
PYTHONPATH=src \
SKEIN_DB=data/dev.db SKEIN_BLOB_ROOT=data/blobs SKEIN_TOKEN=dev-token \
python -m skein
```

打开控制台（见下），在登录页填 `dev-token` 即可。Agent 对话需要模型，再加
`SKEIN_LLM_BASE` / `SKEIN_LLM_KEY` / `SKEIN_AGENT_MODEL`；不配时 Agent 接口返回 503，其余功能不受影响。

## 方式二：SSO 模式（用假 SSO）

想联调登录流程时，用一个实现了 UniSSO 三个接口的本地假服务：

```bash
python deploy/dev/mock-sso.py          # 监听 127.0.0.1:13099
```

账号（密码都是 `right-pass`）：`admin@example.com`（管理员）、`member@example.com`（普通成员）、
`outsider@example.com`（非内部成员，默认策略下被拒）。

```bash
cd services/control
PYTHONPATH=src \
SKEIN_DB=data/dev.db SKEIN_BLOB_ROOT=data/blobs SKEIN_TOKEN=dev-token \
SKEIN_SSO_BASE=http://127.0.0.1:13099 \
SKEIN_SSO_CLIENT_ID=dev-client SKEIN_SSO_CLIENT_SECRET=dev-secret \
python -m skein
```

- 默认策略要求登录者是「内部成员」；想模拟面向公开用户的部署，加 `SKEIN_SSO_REQUIRE_INTERNAL=0`。
- 假服务返回的是 host-only Cookie，本地 http 直接可用。真实部署里 Cookie 域来自 SSO 的响应，
  也可以用 `SKEIN_COOKIE_DOMAIN=.example.com` 显式指定。

## 控制台

```bash
cd services/console
npm ci && npm run build
cd ../..
node deploy/dev/preview-proxy.mjs services/console/dist http://127.0.0.1:7800 5173
```

打开 `http://127.0.0.1:5173/console/`。

## 一个本地节点（可选）

要让工单真的被处理，还得有执行节点。最简单的办法是在同一台机器上再起一个 node-agent，
指向本机控制面，并准备一个本地仓库：

```bash
CONTROLLER_URL=ws://127.0.0.1:7800/api/agent/channel \
AGENT_TOKEN=dev-token SKEIN_TOKEN=dev-token SKEIN_CONTROL_URL=http://127.0.0.1:7800 \
NODE_ID=dev-node PLATFORMS=demo REPO_MAP='{"demo":"/path/to/your/repo"}' \
node services/node-agent/node-agent.mjs
```

节点需要的其余准备（编码 Agent 的模型网关配置、可推送的 deploy key）见
[deploy/pod/README.md](../pod/README.md)。

## 说明

- 机器私有的启动脚本（含真实密钥）不要入库。
- 正式部署见 [docs/deployment.md](../../docs/deployment.md)。
