# 登录与权限模型

> 鉴权可插拔：不配 SSO 时是令牌模式，零配置可用；配了才走 SSO。策略可配置，不绑定任何一家身份源。

## 0. 结论

- **两种调用方**：机器（节点、外部系统、脚本）带共享令牌 `X-Skein-Token`；人用浏览器登录。
- **人怎么登录取决于有没有配 SSO**：
  - 没配 → **令牌模式**：登录页填同一个共享令牌。功能完整，操作者在留痕里记为 `token-holder`。
  - 配了 → **SSO 模式**：用 SSO 账号登录，控制面把 Cookie 回验成 `Principal`。
- **两个入口级别**：`admin`（控制台与管理接口）和 `user`（面向普通用户的接口，历史别名 `market`）。
- **谁能进由 `AccessPolicy` 决定**，与协议无关：
  - 管理入口默认要求主体带 **admin** 标记；
  - 是否还要求主体是「内部成员」由 `SKEIN_SSO_REQUIRE_INTERNAL` 决定。**缺省是要求**（为与历史行为一致）；
    面向公开用户的部署应设为 `0`。
- **不建本地账号表。** 身份只来自 SSO provider；雁阵只认 provider 给出的标志。
- **SSO 模式下共享令牌不能冒充人**：带令牌调 Agent 接口会得到 403 `admin_sso_required`
  （Agent 的操作要留痕到具体的人）。令牌模式下没有可验的人，令牌持有者即操作者。

## 1. 主体

`Principal`（`skein/auth.py`）是验明身份的人，字段与具体 SSO 无关：

```text
sub          provider 里的主体 ID
name / email / picture
internal     该部署口径下的「内部成员」
admin        管理员
super_admin  超级管理员（自动算 admin）
roles        provider 返回的角色列表
```

`principal_from_payload()` 读 `{"user": {...}}` 形态的响应：`is_admin` / `admin` / `roles` 里的 `admin`
都算 admin，`is_super_admin` / `roles` 里的 `super_admin` 算 super_admin。

## 2. 入口策略

| 入口 | level | 判定 |
| --- | --- | --- |
| `/console/*`、`/internal/*`（人类）、Agent 对话 | `admin` | （可选）`internal=true`，且 `admin=true` |
| 聊天代理（`/api/v1/agent/*`、`/task/*`）、`/api/session?surface=user` | `user` | （可选）`internal=true` |

- 会话回验结果缓存 60 秒，但缓存的是「会话对应的主体」而不是授权结论——每次请求仍按入口重新判，
  所以先用 `user` 入口登录再访问控制台不能绕过 `admin` 判定。
- 错误码（控制台前端依赖这些字符串，保持稳定）：`no_sso`（没带会话）、`invalid_sso`（会话无效）、
  `not_internal`、`not_admin`、`admin_sso_required`、`sso_unavailable`（上游不可达，HTTP 503）、
  `sso_not_configured`（令牌模式下调登录接口，HTTP 404）。
- **上游不可达 ≠ 未登录**：SSO 挂了返回 503，控制台保留本地登录状态并提示降级，不把人踢出去。

## 3. 加一种 SSO

控制面只依赖 `IdentityProvider` 协议（`skein/auth.py`）：

```python
class IdentityProvider(Protocol):
    name: str
    cookie_names: tuple[str, ...]          # 第一个是本 provider 种的 Cookie，后面可跟历史旧名

    def login(self, email, password) -> tuple[Principal, dict]:
        """返回 (主体, 会话信息)。会话信息至少含 session；
        可带 cookie_name / cookie_domain / cookie_max_age 覆盖默认值。"""

    def verify(self, session) -> Principal | None: ...   # 无效返回 None
    def revoke(self, session) -> None: ...               # 登出；不可达时抛 AuthUnavailable
```

步骤：

1. 在 `services/control/src/skein/auth_providers/` 下新建模块，实现上面的协议；
2. 提供一个 `build(config) -> IdentityProvider | None`（未配置返回 `None`）；
3. 在 `auth_providers/__init__.py` 的 `PROVIDERS` 里登记名字。

核心的登录/登出/鉴权代码不需要改。选哪种由 `SKEIN_SSO_PROVIDER` 指定。

内置的 provider 只有一个：**UniSSO**（`auth_providers/unisso.py`），协议是三个接口
（`/api/oauth/password/verify`、`/api/oauth/session/verify`、`/api/oauth/session/revoke`）。

## 4. 配置项

| 变量 | 含义 | 缺省 |
| --- | --- | --- |
| `SKEIN_TOKEN` | 共享令牌。机器调用用它；令牌模式下人也用它登录 | 无（必填，否则 `/internal/*` 全部 401） |
| `SKEIN_SSO_PROVIDER` | provider 名字 | `unisso` |
| `SKEIN_SSO_BASE` | SSO 服务地址 | 无 |
| `SKEIN_SSO_CLIENT_ID` / `SKEIN_SSO_CLIENT_SECRET` | 应用凭据。**两者都配了才启用 SSO** | 无 |
| `SKEIN_SSO_COOKIE_NAME` | 会话 Cookie 名；不填用 provider 自己的缺省 | 无 |
| `SKEIN_COOKIE_DOMAIN` | Cookie 域。不填=按 SSO 响应种；`host`=不带 Domain（本地 http 调试） | 无 |
| `SKEIN_SSO_REQUIRE_INTERNAL` | 是否要求「内部成员」。`1`=要求（历史行为） | `1` |

鉴权核心（`skein/auth.py`）和 `api.py` 里没有任何默认域名、Cookie 名或 Cookie 域；provider 模块里只定义自己的 Cookie 名缺省（UniSSO 是 `unisso_sid`），域名一律来自配置。示例配置一律用占位值。

## 5. 给上游服务的身份签名头

聊天代理（可选集成）转发请求时，如果调用方在控制台已登录，会额外带一个短时签名头
`X-Skein-User: v1.<base64url>.<hmac>`，让上游知道这个人是谁：

- 载荷：`sub / internal / admin / super_admin / email / name / exp`，60 秒有效；
- 签名密钥是 `SKEIN_ASSERTION_KEY`，与上游共享同一个值（600 权限，不进仓库）；
- 浏览器自带的同名头会被剥掉，不能伪造；没配密钥时不带这个头，由上游用自己的方式鉴权。

## 6. 本地联调

`deploy/dev/mock-sso.py` 是一个实现了 UniSSO 三个接口的假 SSO，账号与用法见
[`deploy/dev/README.md`](../../deploy/dev/README.md)。
