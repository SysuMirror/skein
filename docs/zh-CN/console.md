# 控制台

> Vue 3 + naive-ui；`services/console/dist` 由控制面挂在 `/console/`。

## 一、页面

一级导航三个工作面，外加一个管理员 Agent。工作面定义见 [product.md](product.md)。

| 路由 | 页面 | 数据来源 | 能做什么 |
| --- | --- | --- | --- |
| `/today` | 工作台 | `/internal/stream` | 阵眼、闭环流水线、验收、看卡点 |
| `/agent` | Agent | `/internal/agent/chat` | 管理员用自然语言查/操作 |
| `/tickets`、`/tickets/:id` | 需求 | `/internal/tickets`、`/internal/tickets/{id}` | 列表；通过/驳回；派单；详情抽屉（含交付卡片） |
| `/fleet` | 运行 · 总览 | `/internal/topology` | 力导网（节点 / 工单 / 会话）+ 最近派工 |
| `/nodes`、`/nodes/:id` | 运行 · 节点 | `/internal/nodes` | 在线状态、平台、容量、最后心跳 |
| `/workers`、`/workers/:id` | 运行 · Worker | `/internal/sessions` | 会话列表；transcript 时间线 |
| `/events` | 运行 · 事件 | `/internal/events` | 实时流 + 准入判定 + 按线索聚合 |
| `/calls` | 运行 · 会话 | `/internal/llm/sessions`、`/internal/llm/calls` | 每次模型调用：输入/输出/tokens/耗时 |
| `/signals` | 运行 · 信号 | `/internal/signals` | 派单超时、作业失败、节点离线 |
| `/about` | 说明 | — | 产品自述与闭环图 |

几个交互上的约定：

- **交付卡片**（工单详情）显示修复分支、目标分支、提交、变更，以及「打开 PR」和「测试环境」
  两个链接。后者来自 `SKEIN_TEST_ENVS`；没配就不显示。
- **最近派工**（运行总览）列出时间、工单、平台、节点、状态与说明，失败的行整行高亮——
  派单失败必须一眼能看到原因。
- **派单必须选平台**。工单已有在途派工时，按钮不再可用，接口也会拒绝（`already_dispatched`）。
- `/workers/:id`、`/tickets/:id` 支持深链，直接打开某个对象。

## 二、实时推送

`GET /internal/stream` 每 2 秒算一次快照（summary / signals / 最近 100 张工单 / topology / 最近 20 条事件），
和上一帧比对：变了发 `event: snapshot`，没变就每 15 秒发一次 `event: ping`。

- 客户端用 `fetch` 读流，不用 `EventSource`——后者无法带 `X-Skein-Token` 头。
- 断线按 1s → 2s → … → 15s 退避重连；401 清掉连接回到落地页。
- 顶栏的状态就是这条连接：连上显示「实时」，断开显示「轮询」，此时各页按 10–15 秒轮询。
- 每次连接都会先收到一帧完整快照，所以刷新页面不依赖 REST 首屏。

## 三、登录

控制台启动时请求 `/api/session` 决定走哪条路：

| 响应 | 模式 | 登录方式 |
| --- | --- | --- |
| `{"mode": "token"}` | 令牌模式 | 落地页填共享令牌，存在 `localStorage`（`skein.token`） |
| `{"mode": "sso", "user": …}` 或 401 | SSO 模式 | 邮箱 + 密码，由控制面转给配置的 SSO provider；会话在 Cookie 里 |
| 503 | SSO 上游不可达 | 保留本地登录状态并顶部提示降级，不把人踢出去 |

详见 [permission-model.md](permission-model.md)。

## 四、技术选型

| 项 | 选择 | 说明 |
| --- | --- | --- |
| 框架 | Vue 3 + Vite + TypeScript | 构建时 `vue-tsc` 做类型检查 |
| 组件库 | naive-ui | |
| 状态 | Pinia | |
| 图 | 自绘力导（`MeshGraph.vue`） | 数据装配层与绘制分离，换图库时只动绘制 |
| 实时 | SSE + 轮询回退 | 见第二节 |

## 五、视觉与动效

**视觉克制，唯一例外是工作台首屏。** 首屏允许一块数据可视化（阵眼雷达 + 闭环流水线），
因为它是唯一需要「一眼看懂全局」的页面；图上每个点、每一段都必须挂真实对象。
除此之外不引 3D、粒子、视差；其他页面的动效只出现在状态变化处。

- 所有循环动画尊重 `prefers-reduced-motion: reduce`。
- 雷达与流水线在无数据时安静降级（空环、零值），不做「演示数据」。
- 中文优先；原始 ID 只在排障时出现。

## 六、构建与冒烟

```bash
cd services/console && npm ci && npm run build     # 产物 dist/；控制面启动时挂载
node deploy/scripts/console-smoke.mjs               # 冒烟（需要控制面已在运行）
```

源码在 `services/console/src/`。品牌文案（「雁阵」「Worker」）直接写在组件里；
登录相关的提示按后端返回的错误码显示，不假定是哪一家 SSO。
