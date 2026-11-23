"""定时作业驱动：到点开工，按 (job, 计划分钟) 去重，作业在独立线程里跑。"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from skein import runs

log = logging.getLogger("skein.driver")

Runner = Callable[[str, float], tuple[bool, str]]


@dataclass(frozen=True)
class Schedule:
    job: str
    at: str = ""  # "HH:MM"（兼容）
    cron: str = ""  # 5 段：分 时 日 月 周

    def matches(self, now: datetime) -> bool:
        if self.at:
            return now.strftime("%H:%M") == self.at
        if self.cron:
            return cron_matches(self.cron, now)
        return False


def _field_match(field: str, value: int, lo: int, hi: int) -> bool:
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            try:
                step = int(raw_step)
            except ValueError:
                continue
            if step <= 0:
                continue
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            raw_start, raw_end = part.split("-", 1)
            try:
                start, end = int(raw_start), int(raw_end)
            except ValueError:
                continue
        else:
            try:
                start = end = int(part)
            except ValueError:
                continue
        start, end = max(lo, start), min(hi, end)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _dow_match(field: str, weekday: int) -> bool:
    # weekday: 0=周日 … 6=周六；cron 里 0 和 7 都表示周日。
    if _field_match(field, weekday, 0, 6):
        return True
    return weekday == 0 and _field_match(field, 7, 7, 7)


def cron_matches(expr: str, now: datetime) -> bool:
    parts = expr.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    if not _field_match(minute, now.minute, 0, 59):
        return False
    if not _field_match(hour, now.hour, 0, 23):
        return False
    if not _field_match(month, now.month, 1, 12):
        return False
    dom_ok = _field_match(dom, now.day, 1, 31)
    dow_ok = _dow_match(dow, (now.weekday() + 1) % 7)
    if dom.strip() != "*" and dow.strip() != "*":
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def parse_schedules(raw: str) -> list[Schedule]:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    result = []
    for item in items:
        job = str(item.get("job") or "").strip()
        at = str(item.get("at") or "").strip()
        cron = str(item.get("cron") or "").strip()
        if not job:
            continue
        if len(at) == 5 and at[2] == ":":
            result.append(Schedule(job, at=at))
        elif len(cron.split()) == 5:
            result.append(Schedule(job, cron=cron))
    return result


def subprocess_runner(command: str, timeout: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            command, check=False, shell=True, capture_output=True, timeout=timeout, text=True
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {int(timeout)}s"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, output[-500:]


class Driver:
    def __init__(
        self,
        conn: sqlite3.Connection,
        schedules: list[Schedule],
        commands: dict[str, str],
        runner: Runner = subprocess_runner,
        timeout: float = 4 * 3600,
        inline: bool = False,
    ) -> None:
        self._conn = conn
        self._schedules = schedules
        self._commands = commands
        self._runner = runner
        self._timeout = timeout
        self._inline = inline
        self._active: set[str] = set()

    def due_jobs(self, now: datetime) -> list[str]:
        return [schedule.job for schedule in self._schedules if schedule.matches(now)]

    def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now()
        # 用计划分钟做键：同一天内 7 */2 * * * 可以跑多次，重复 tick 仍幂等。
        slot = now.strftime("%Y-%m-%dT%H:%M")
        started: list[str] = []
        for job in self.due_jobs(now):
            if job in self._active:
                continue
            run_id = runs.start(self._conn, job, slot)
            if run_id is None:
                continue
            command = self._commands.get(job)
            if not command:
                runs.finish(self._conn, run_id, "skipped", "no command configured")
                continue
            started.append(run_id)
            if self._inline:
                self._execute(run_id, job, command)
            else:
                self._active.add(job)
                threading.Thread(
                    target=self._execute, args=(run_id, job, command), daemon=True
                ).start()
        return started

    def _execute(self, run_id: str, job: str, command: str) -> None:
        try:
            ok, detail = self._runner(command, self._timeout)
            runs.finish(self._conn, run_id, "succeeded" if ok else "failed", detail)
            log.info("作业 %s 完成：%s", job, "成功" if ok else "失败")
        except Exception as exc:
            runs.finish(self._conn, run_id, "failed", str(exc))
            log.exception("作业 %s 异常", job)
        finally:
            self._active.discard(job)

    def run_forever(self, interval: float = 30.0, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("调度 tick 失败")
            stop.wait(interval)
