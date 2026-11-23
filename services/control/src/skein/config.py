"""运行时配置。只从环境变量读，不读配置文件。

每个键的含义、默认值与示例见 ``.env.example``（根目录）与 ``docs/configuration.md``。
新开关的默认值一律复现「这个开关出现之前」的行为，所以老部署不改环境文件也能直接升级。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from skein.forge import RepoTarget, normalize_repos

_TRUE = ("1", "true", "yes")


def _flag(source: dict[str, str], key: str, default: bool) -> bool:
    return source.get(key, "1" if default else "0").strip().lower() in _TRUE


def _json(source: dict[str, str], key: str) -> dict:
    try:
        parsed = json.loads(source.get(key, "") or "{}")
    except json.JSONDecodeError:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class Config:
    db_path: str = "data/skein.db"
    blob_root: str = "data/blobs"
    token: str = ""
    host: str = "127.0.0.1"
    port: int = 7800
    route_model: str = "route"
    agent_model: str = "deepseek-v4-flash-vision-exp"
    llm_base: str = ""
    llm_key: str = ""
    schedules: str = ""
    job_commands: str = ""
    jobs_file: str = ""
    driver_enabled: bool = False
    job_timeout: int = 14400
    platform_repos: dict[str, str] = field(default_factory=dict)
    # 用哪家代码托管建 PR（见 skein/forge.py 的 FORGES）
    forge: str = "github"
    github_token: str = ""
    github_repos: dict[str, RepoTarget] = field(default_factory=dict)
    # 空 = 自动：仓库有 develop 分支用 develop，否则用仓库默认分支
    github_base: str = ""
    github_api: str = "https://api.github.com"
    github_pr_draft: bool = True
    # {repo_slug: url}：PR 合入 develop 后可看的测试环境
    test_envs: dict[str, str] = field(default_factory=dict)
    control_url: str = ""
    watchdog: bool = False
    watchdog_auto: bool = False
    watchdog_interval: int = 300
    watchdog_tickets: bool = False
    watchdog_dispatch: bool = False
    daily_ticket_hour: int = -1
    ticket_daily_cap: int = 5
    # ---- 登录鉴权 ----
    # 三种登录方式只启用一种，规则见 skein.auth_providers.resolve_method：
    #   配了 SSO_CLIENT_ID + SSO_CLIENT_SECRET → 外部登录系统；否则 LOCAL_LOGIN=1 → 本地账号；
    #   否则零配置的令牌模式。
    sso_provider: str = "unisso"
    sso_base: str = ""
    sso_client_id: str = ""
    sso_client_secret: str = ""
    sso_cookie_name: str = ""
    # None=按 SSO 响应种；""=host-only；其他值=显式域
    sso_cookie_domain: str | None = None
    # 是否要求登录主体是上游登录系统认定的「内部成员」。缺省 true 是为了与历史行为一致；
    # 面向公开用户的部署应显式设 SKEIN_SSO_REQUIRE_INTERNAL=0。
    sso_require_internal: bool = True
    local_login: bool = False
    # 第一次见到、上游没说他是管理员的外部身份：要不要自动建号。空 = 不建；member = 建成 member
    auth_auto_role: str = ""
    # 雁阵自己发的会话（令牌模式 / 本地账号）
    session_ttl_hours: float = 168.0
    session_cookie_name: str = ""
    session_cookie_secure: bool = True
    # ---- 对外给助手的公共 API ----
    # 控制台对外的地址，用来拼工单链接；不配就不带链接
    public_url: str = ""
    # 每个助手凭证每分钟最多请求数
    api_rate_limit: int = 60
    # 是否允许助手凭证上报模型调用留痕（POST /api/v1/llm/calls）。缺省关。
    api_trace: bool = False
    # 是否暴露 /docs 与 /openapi.json。缺省关：内部接口的清单不必对外。
    api_docs: bool = False
    # 日志格式：text（缺省）或 json（一行一个对象，便于采集）
    log_format: str = "text"
    # 控制台构建产物（dist 的上一级）的位置。空 = 源码目录旁的 services/console，
    # pip 安装或容器里用这个变量指过去
    console_dir: str = ""
    # 在环境文件里声明的助手凭证：[{"name": ..., "purpose": ..., "key": ...}, ...]
    assistants: list[dict] = field(default_factory=list)
    # ---- 可选集成（不配对应 env 就不加载、不暴露路由）----
    # 聊天代理上游 + 给上游签身份头用的密钥
    qa_chat_upstream: str = ""
    assertion_key: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        source = os.environ if env is None else env
        base = cls()
        platform_repos = {
            str(key): str(value) for key, value in _json(source, "SKEIN_PLATFORM_REPOS").items()
        }
        github_repos = normalize_repos(_json(source, "SKEIN_GITHUB_REPOS"))
        test_envs = {
            str(key): str(value) for key, value in _json(source, "SKEIN_TEST_ENVS").items()
        }
        try:
            assistants_raw = json.loads(source.get("SKEIN_ASSISTANTS", "") or "[]")
        except json.JSONDecodeError:
            assistants_raw = []
        assistants = [item for item in assistants_raw if isinstance(item, dict)] if isinstance(
            assistants_raw, list
        ) else []
        hours = float(source.get("SKEIN_SESSION_TTL_HOURS", base.session_ttl_hours) or 0)
        rate = int(source.get("SKEIN_API_RATE_LIMIT", base.api_rate_limit) or 0)
        return cls(
            db_path=source.get("SKEIN_DB", base.db_path),
            blob_root=source.get("SKEIN_BLOB_ROOT", base.blob_root),
            token=source.get("SKEIN_TOKEN", base.token),
            host=source.get("SKEIN_HOST", base.host),
            port=int(source.get("SKEIN_PORT", base.port)),
            route_model=source.get("SKEIN_ROUTE_MODEL", base.route_model),
            agent_model=source.get("SKEIN_AGENT_MODEL", base.agent_model),
            llm_base=source.get("SKEIN_LLM_BASE", base.llm_base),
            llm_key=source.get("SKEIN_LLM_KEY", base.llm_key),
            schedules=source.get("SKEIN_SCHEDULES", base.schedules),
            job_commands=source.get("SKEIN_JOB_COMMANDS", base.job_commands),
            jobs_file=source.get("SKEIN_JOBS_FILE", base.jobs_file),
            driver_enabled=_flag(source, "SKEIN_DRIVER", False),
            job_timeout=int(source.get("SKEIN_JOB_TIMEOUT", base.job_timeout)),
            platform_repos=platform_repos,
            forge=source.get("SKEIN_FORGE", base.forge),
            github_token=source.get("SKEIN_GITHUB_TOKEN", base.github_token),
            github_repos=github_repos,
            github_base=source.get("SKEIN_GITHUB_BASE", base.github_base),
            github_api=source.get("SKEIN_GITHUB_API", base.github_api),
            github_pr_draft=_flag(source, "SKEIN_GITHUB_PR_DRAFT", True),
            test_envs=test_envs,
            control_url=source.get("SKEIN_CONTROL_URL", base.control_url),
            watchdog=_flag(source, "SKEIN_WATCHDOG", False),
            watchdog_auto=_flag(source, "SKEIN_WATCHDOG_AUTO", False),
            watchdog_interval=int(
                source.get("SKEIN_WATCHDOG_INTERVAL", base.watchdog_interval)
            ),
            watchdog_tickets=_flag(source, "SKEIN_WATCHDOG_TICKETS", False),
            watchdog_dispatch=_flag(source, "SKEIN_WATCHDOG_DISPATCH", False),
            daily_ticket_hour=int(
                source.get("SKEIN_DAILY_TICKET_HOUR", base.daily_ticket_hour)
            ),
            ticket_daily_cap=int(
                source.get("SKEIN_TICKET_DAILY_CAP", base.ticket_daily_cap)
            ),
            sso_provider=source.get("SKEIN_SSO_PROVIDER", base.sso_provider),
            sso_base=source.get("SKEIN_SSO_BASE", base.sso_base),
            sso_client_id=source.get("SKEIN_SSO_CLIENT_ID", base.sso_client_id),
            sso_client_secret=source.get(
                "SKEIN_SSO_CLIENT_SECRET", base.sso_client_secret
            ),
            sso_cookie_name=source.get("SKEIN_SSO_COOKIE_NAME", base.sso_cookie_name),
            sso_cookie_domain=cls._cookie_domain(
                source.get("SKEIN_COOKIE_DOMAIN", base.sso_cookie_domain)
            ),
            sso_require_internal=_flag(source, "SKEIN_SSO_REQUIRE_INTERNAL", True),
            local_login=_flag(source, "SKEIN_LOCAL_LOGIN", False),
            auth_auto_role=source.get("SKEIN_AUTH_AUTO_ROLE", base.auth_auto_role).strip().lower(),
            session_ttl_hours=hours if hours > 0 else base.session_ttl_hours,
            session_cookie_name=source.get("SKEIN_SESSION_COOKIE_NAME", base.session_cookie_name),
            session_cookie_secure=_flag(source, "SKEIN_SESSION_COOKIE_SECURE", True),
            public_url=source.get("SKEIN_PUBLIC_URL", base.public_url),
            api_rate_limit=rate if rate > 0 else base.api_rate_limit,
            api_trace=_flag(source, "SKEIN_API_TRACE", False),
            api_docs=_flag(source, "SKEIN_API_DOCS", False),
            log_format=(source.get("SKEIN_LOG_FORMAT", base.log_format) or "text").strip().lower(),
            console_dir=source.get("SKEIN_CONSOLE_DIR", base.console_dir),
            assistants=assistants,
            assertion_key=source.get("SKEIN_ASSERTION_KEY", base.assertion_key),
            qa_chat_upstream=source.get("SKEIN_QA_CHAT_UPSTREAM", base.qa_chat_upstream),
        )

    @staticmethod
    def _cookie_domain(value: str | None) -> str | None:
        # "host" 或空串 = 不带 Domain（本地 http 调试）；缺省 = 按 SSO 响应。
        if value is None:
            return None
        if value.strip().lower() in ("", "host"):
            return ""
        return value
