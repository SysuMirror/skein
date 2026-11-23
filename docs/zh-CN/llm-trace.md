# 会话与模型调用留痕

> 不依赖外部观测平台；雁阵自己存、自己在控制台里看。

## 1. 存什么

SQLite `llm_calls`：

| 字段 | 说明 |
| --- | --- |
| `session_id` | 聊天 thread_id / 业务会话 ID |
| `actor` | 操作者（SSO 模式下是主体 ID；令牌模式下为空） |
| `source` | 谁产生的调用，例如 `skein-agent`（控制台 Agent）；外部服务自定义 |
| `model` | 模型名 |
| `prompt` / `response` | 截断到 16k 字符 |
| `prompt_tokens` / `completion_tokens` | 模型网关返回的用量；没有就 0 |
| `latency_ms` | 调用耗时 |
| `status` / `error` | `ok` / `error` |
| `created_at` | 服务端时间 |

## 2. 谁在写

- **控制台 Agent**：`source=skein-agent`，管理员对话的规划与总结各记一次（`agent.py`）。
- **外部服务**：任何拿到共享令牌的服务都可以 `POST /internal/llm/calls` 上报，
  用 `source` 区分来源（例如入口机器人每次分类、另一个聊天服务每次回答）。
- **worker（编码 Agent）**：暂未上报单次模型调用；它的 transcript 走会话权威（见
  [architecture.md](architecture.md) 第 7 节），另算。

## 3. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/internal/llm/calls` | 上报一次调用（共享令牌） |
| GET | `/internal/llm/sessions` | 按 session 聚合：调用数、tokens、最近模型/状态 |
| GET | `/internal/llm/calls?session_id=&source=&limit=` | 某会话的调用明细 |

留痕失败只写日志，不影响调用方的主流程。

## 4. 控制台

「运行 → 会话」：

- 左边按会话列出：来源、调用数、tokens、用户、最近时间；
- 右边是每次调用：模型、延迟、tokens、状态；展开看输入/输出。

## 5. 配置

无新增配置，`llm_calls` 表随 schema 自动建。

## 6. 还没做

- worker 单次模型调用上报；定时作业模型调用上报。
- 自动清理（保留天数）与敏感字段脱敏——**`prompt` / `response` 里可能有用户原文，
  目前永久保留**，部署时请注意磁盘与合规。
- 控制台按模型/用户搜索、成本换算。
