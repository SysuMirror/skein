"""进程入口：``python -m skein``

- 不带参数：启动控制面（systemd 单元用的就是它）。``serve`` 同义。
- ``user …`` / ``assistant …``：管理命令，见 ``skein.cli``。
- ``version``：打印版本。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

import uvicorn

from skein import __version__, cli, db, logbuf
from skein.api import create_app
from skein.assistants import AssistantStore
from skein.auth_providers import build as build_login
from skein.config import Config
from skein.driver import Driver, parse_schedules
from skein.forge import PullRequestService, build_forges, github_repo_from_remote
from skein.notify import Notifier

USAGE = """usage: python -m skein [COMMAND]

  serve            run the control plane (default when no command is given)
  user …           manage who can sign in and what they may do
  assistant …      manage assistant credentials for the public API
  version          print the version
"""


def test_envs_by_platform(cfg: Config) -> dict[str, str]:
    """SKEIN_TEST_ENVS 以仓库 slug 为键；控制台按平台查，这里换好键。"""
    envs: dict[str, str] = {}
    for platform, target in cfg.github_repos.items():
        url = cfg.test_envs.get(target.slug, "")
        if url:
            envs[platform] = url
    for platform, remote in cfg.platform_repos.items():
        url = cfg.test_envs.get(github_repo_from_remote(remote) or "", "")
        if url:
            envs.setdefault(platform, url)
    return envs


def build(cfg: Config | None = None) -> tuple:
    """按配置装好整个控制面（不启动 HTTP 服务）。配置自相矛盾时以清晰的消息退出。"""
    try:
        cfg = cfg if cfg is not None else Config.from_env()
        Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.blob_root).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.blob_root).mkdir(parents=True, exist_ok=True)
        conn = db.connect(cfg.db_path)
        db.migrate(conn)
        login, directory, _ = build_login(cfg, conn)
    except ValueError as exc:
        raise SystemExit(f"invalid configuration: {exc}") from exc

    log = logging.getLogger("skein")
    log.info("login method: %s (%s)", login.mode, login.provider.name)
    assistants = AssistantStore(conn)
    declared = assistants.declare(cfg.assistants)
    if declared:
        log.info("assistant credentials declared in env: %s", ", ".join(declared))

    forges = build_forges(cfg)
    pr_service = None
    if forges:
        pr_service = PullRequestService(
            forges,
            repos=cfg.github_repos,
            base=cfg.github_base,
            draft=cfg.github_pr_draft,
        )
    app = create_app(
        conn,
        cfg.blob_root,
        cfg.token,
        platform_repos=cfg.platform_repos,
        pr_service=pr_service,
        test_envs=test_envs_by_platform(cfg),
        login=login,
        directory=directory,
        assistants=assistants,
        notifier=Notifier(conn, assistants.callback_target, public_url=cfg.public_url),
        api_rate_limit=cfg.api_rate_limit,
        api_trace=cfg.api_trace,
        public_url=cfg.public_url,
        docs=cfg.api_docs,
        qa_chat_upstream=cfg.qa_chat_upstream,
        qa_user_secret=cfg.assertion_key,
        llm_base=cfg.llm_base,
        llm_key=cfg.llm_key,
        agent_model=cfg.agent_model,
        console_dir=cfg.console_dir or None,
    )
    return app, cfg, conn


def serve() -> None:
    try:
        cfg = Config.from_env()
    except ValueError as exc:
        raise SystemExit(f"invalid configuration: {exc}") from exc
    logbuf.install(fmt=cfg.log_format)
    log = logging.getLogger("skein")
    app, cfg, conn = build(cfg)
    log.info("control plane started db=%s port=%s", cfg.db_path, cfg.port)
    if not cfg.token:
        log.warning("SKEIN_TOKEN is empty: the console, nodes and scripts cannot authenticate")
    schedules_raw = cfg.schedules
    commands_raw = cfg.job_commands
    if cfg.jobs_file and Path(cfg.jobs_file).is_file():
        data = json.loads(Path(cfg.jobs_file).read_text(encoding="utf-8"))
        schedules_raw = json.dumps(data.get("schedules") or [])
        commands_raw = json.dumps(data.get("commands") or {})
        log.info("job configuration read from %s", cfg.jobs_file)
    if cfg.driver_enabled and schedules_raw:
        schedules = parse_schedules(schedules_raw)
        try:
            commands = json.loads(commands_raw or "{}")
        except json.JSONDecodeError:
            commands = {}
        if schedules:
            driver = Driver(conn, schedules, commands, timeout=cfg.job_timeout)
            threading.Thread(target=driver.run_forever, daemon=True).start()
    uvicorn.run(app, host=cfg.host, port=cfg.port)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    if not args or args[0] == "serve":
        serve()
    elif args[0] in ("version", "--version", "-V"):
        print(f"skein {__version__}")
    elif args[0] in ("user", "assistant"):
        raise SystemExit(cli.run(args))
    elif args[0] in ("-h", "--help", "help"):
        print(USAGE, end="")
    else:
        print(f"unknown command: {args[0]}\n\n{USAGE}", end="", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
