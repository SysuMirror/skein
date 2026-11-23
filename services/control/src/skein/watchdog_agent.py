"""Watchdog 独立进程：外部控制回路。

- 默认只记录信号；
- `SKEIN_WATCHDOG_TICKETS=1` 时把新信号变成**自维护工单**（走控制面 API，dedup 幂等），
  可选直接派给 worker（`SKEIN_WATCHDOG_DISPATCH=1`）；
- `SKEIN_DAILY_TICKET_HOUR` 指定小时时，每天建一张「每日巡检」工单；
- `SKEIN_WATCHDOG_AUTO=1` 时仍可把模型建议写成 Worker 提示词金丝雀（永不自动升格）。

人只做验收：自动建单只会产出修复分支，不会合入、不会部署。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from skein import db, prompts, store, watchdog
from skein.config import Config
from skein.llm_http import HttpLLM
from skein.summary import summary as build_summary

log = logging.getLogger("skein.watchdog")

WATCHDOG_PROMPT = (
    "你是 Watchdog，只负责观察和提出 Worker 执行提示词的修订建议。"
    "只输出 JSON：{\"text\":\"修订后的提示词\"}。"
    "只能改 Worker 的执行段，不能改准入、Router、工具权限或对外措辞；"
    "改动要小，保留原有结构和 Markdown 标记。"
)

WORK_ORDER = (
    "\n要求：先定位根因；能在本仓库修的就改、自测并提交到修复分支；"
    "不能修就在总结里写清阻塞与建议。不要改基线分支，不要部署。"
)


class TicketClient:
    """控制面建单 API 的最小客户端（stdlib，无第三方依赖）。"""

    def __init__(self, base: str, token: str, timeout: float = 8.0) -> None:
        self._base = base.rstrip("/")
        self._token = token
        self._timeout = timeout

    def create(
        self,
        *,
        title: str,
        summary: str,
        platform: str,
        dedup_key: str,
        dispatch: bool,
    ) -> dict:
        body = json.dumps(
            {
                "title": title,
                "summary": summary,
                "platform": platform,
                "dedup_key": dedup_key,
                "dispatch": dispatch,
            },
            ensure_ascii=False,
        ).encode()
        request = urllib.request.Request(
            self._base + "/internal/tickets",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Skein-Token": self._token,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json.loads(response.read() or b"{}")


def signal_ticket(signal: dict) -> tuple[str, str]:
    kind = str(signal.get("kind") or "unknown")
    if kind == "dispatch_timeout":
        title = f"[自维护] 派单超时：工单 {signal.get('ticket_id')} @ 节点 {signal.get('node_id')}"
    elif kind == "run_failed":
        title = f"[自维护] 作业失败：{signal.get('job')} {signal.get('day')}"
    elif kind == "node_offline_with_work":
        title = (
            "[自维护] 节点离线且有在途工单："
            f"{signal.get('node_id')} / {signal.get('ticket_id')}"
        )
    else:
        title = f"[自维护] Watchdog 信号：{kind}"
    summary = "信号：" + json.dumps(signal, ensure_ascii=False) + WORK_ORDER
    return title, summary


def _day_start(now: float) -> float:
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def auto_count_today(conn, now: float) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE dedup_key LIKE 'auto:%' AND created_at >= ?",
        (_day_start(now),),
    ).fetchone()
    return int(row[0])


def ticket_signals(
    conn,
    client,
    signals: list[dict],
    *,
    daily_cap: int,
    dispatch: bool,
    platform: str = "skein",
    now: float | None = None,
) -> list[dict]:
    now = now if now is not None else time.time()
    remaining = max(0, daily_cap - auto_count_today(conn, now))
    results: list[dict] = []
    for signal in signals:
        if remaining <= 0:
            log.info("自维护工单已达今日上限 %s，剩余信号只记录", daily_cap)
            break
        dedup = f"auto:signal:{signal.get('kind')}:{signal.get('id')}"
        if store.find_ticket_by_dedup(conn, dedup):
            continue
        title, summary = signal_ticket(signal)
        try:
            result = client.create(
                title=title,
                summary=summary,
                platform=platform,
                dedup_key=dedup,
                dispatch=dispatch,
            )
        except (urllib.error.URLError, OSError, ValueError):
            log.exception("建单失败 signal=%s", signal.get("id"))
            continue
        if result.get("created"):
            remaining -= 1
        results.append(result)
    return results


def daily_review(
    conn,
    client,
    *,
    hour: int,
    dispatch: bool,
    platform: str = "skein",
    now: float | None = None,
) -> dict | None:
    if hour < 0:
        return None
    now = now if now is not None else time.time()
    local = time.localtime(now)
    if local.tm_hour != hour:
        return None
    date = time.strftime("%Y-%m-%d", local)
    dedup = f"auto:daily:{date}"
    if store.find_ticket_by_dedup(conn, dedup):
        return None
    snapshot = {
        "summary": build_summary(conn),
        "signals": watchdog.signals(conn, now=now),
    }
    text = json.dumps(snapshot, ensure_ascii=False)[:4000]
    try:
        return client.create(
            title=f"[自维护] 每日巡检 {date}",
            summary=(
                "今日快照："
                + text
                + "\n要求：先看有没有异常；一切正常就只输出巡检结论，"
                "不要改文件、不要建分支；发现异常按工单方式处理。" + WORK_ORDER
            ),
            platform=platform,
            dedup_key=dedup,
            dispatch=dispatch,
        )
    except (urllib.error.URLError, OSError, ValueError):
        log.exception("每日巡检建单失败")
        return None


def _seen_key(signal: dict) -> str:
    return f"watchdog:seen:{signal.get('kind')}:{signal.get('id')}"


def _propose(conn, llm, model: str, signal: dict) -> None:
    part = "runtime/worker"
    state = prompts.state(conn, part)
    current_text = prompts.current(conn, part)
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": WATCHDOG_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"signal": signal, "current_prompt": current_text},
                    ensure_ascii=False,
                ),
            },
        ],
        "max_tokens": 1200,
    }
    try:
        raw = llm.complete(request)
        text = raw.get("text") if isinstance(raw, dict) else None
        if not isinstance(text, str) or not text.strip():
            log.warning("watchdog 提议为空，signal=%s", signal.get("id"))
            return
        revision = prompts.propose(
            conn,
            part,
            text.strip(),
            state["stable_revision"],
            "watchdog",
            str(signal.get("id")),
        )
        log.info("watchdog 提议进入金丝雀 revision=%s signal=%s", revision, signal.get("id"))
    except prompts.PromptError as exc:
        log.warning("watchdog 提议被拒绝：%s", exc)
    except Exception:
        log.exception("watchdog 提议失败")


def observe(
    conn,
    *,
    auto_propose: bool = False,
    llm=None,
    model: str = "route",
    now: float | None = None,
) -> list[dict]:
    fresh: list[dict] = []
    for signal in watchdog.signals(conn, now=now):
        key = _seen_key(signal)
        if store.kv_get(conn, key):
            continue
        store.kv_set(conn, key, str(int(time.time())))
        db.record_audit(conn, "watchdog", "signal", signal)
        fresh.append(signal)
        if auto_propose and llm is not None:
            _propose(conn, llm, model, signal)
    return fresh


def run_forever(
    conn,
    *,
    interval: int = 300,
    auto_propose: bool = False,
    llm=None,
    model: str = "route",
    tickets: bool = False,
    dispatch: bool = False,
    daily_hour: int = -1,
    daily_cap: int = 5,
    client=None,
) -> None:
    log.info(
        "Watchdog 启动：interval=%ss auto=%s tickets=%s dispatch=%s daily_hour=%s",
        interval,
        auto_propose,
        tickets,
        dispatch,
        daily_hour,
    )
    while True:
        try:
            fresh = observe(conn, auto_propose=auto_propose, llm=llm, model=model)
            if fresh:
                log.info("Watchdog 新信号 %s 条", len(fresh))
            if client is not None:
                if tickets and fresh:
                    made = ticket_signals(
                        conn, client, fresh, daily_cap=daily_cap, dispatch=dispatch
                    )
                    if made:
                        log.info("自维护建单 %s 张：%s", len(made), made)
                review = daily_review(
                    conn, client, hour=daily_hour, dispatch=dispatch
                )
                if review:
                    log.info("每日巡检建单：%s", review)
        except Exception:
            log.exception("Watchdog 轮询失败")
        time.sleep(max(10, interval))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    if not cfg.watchdog:
        log.info("SKEIN_WATCHDOG 未开启")
        return
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    llm = (
        HttpLLM(cfg.llm_base, cfg.llm_key)
        if cfg.watchdog_auto and cfg.llm_base and cfg.llm_key
        else None
    )
    if cfg.watchdog_auto and llm is None:
        log.warning("SKEIN_WATCHDOG_AUTO=1 但没有模型网关，只记录信号")
    client = None
    if cfg.watchdog_tickets:
        if cfg.control_url and cfg.token:
            client = TicketClient(cfg.control_url, cfg.token)
        else:
            log.warning("SKEIN_WATCHDOG_TICKETS=1 但缺 SKEIN_CONTROL_URL/SKEIN_TOKEN，不建单")
    run_forever(
        conn,
        interval=cfg.watchdog_interval,
        auto_propose=cfg.watchdog_auto,
        llm=llm,
        model=cfg.route_model,
        tickets=cfg.watchdog_tickets,
        dispatch=cfg.watchdog_dispatch,
        daily_hour=cfg.daily_ticket_hour,
        daily_cap=cfg.ticket_daily_cap,
        client=client,
    )


if __name__ == "__main__":
    main()
