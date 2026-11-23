"""日志：进程内环形缓冲（给 ``GET /internal/logs``），以及可选的 JSON 行格式。

``SKEIN_LOG_FORMAT=json`` 时，控制面与 uvicorn 的日志都从根 logger 以「一行一个 JSON
对象」输出，方便采集；缺省仍是给人看的文本。两种格式都会同时进环形缓冲。
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone

MAX_RECORDS = 2000

_records: deque[dict] = deque(maxlen=MAX_RECORDS)
_lock = threading.Lock()
_installed = False


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        item = {
            "at": record.created,
            "level": record.levelname.lower(),
            "component": record.name,
            "message": message,
        }
        with _lock:
            _records.append(item)


class JsonFormatter(logging.Formatter):
    """一行一个 JSON 对象：时间、级别、组件、正文，有异常就带上。"""

    def format(self, record: logging.LogRecord) -> str:
        item = {
            "at": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "component": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            item["exception"] = self.formatException(record.exc_info)
        return json.dumps(item, ensure_ascii=False)


def install(level: int = logging.INFO, fmt: str = "text", stream: object = None) -> None:
    """装上环形缓冲；``fmt="json"`` 时把输出也换成 JSON 行（``stream`` 缺省是 stderr）。

    uvicorn 自带的 handler 会被摘掉、改为向根 logger 传播，这样两种格式都只走一遍。
    """
    global _installed
    if _installed:
        return
    root = logging.getLogger()
    ring = _RingHandler()
    ring.setLevel(level)
    root.addHandler(ring)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    if fmt == "json":
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        handler.setLevel(level)
        root.handlers = [handler, ring]
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = []
            uvicorn_logger.propagate = True
    _installed = True


def recent(limit: int = 300, component: str | None = None, level: str | None = None) -> list[dict]:
    with _lock:
        items = list(_records)
    if component:
        items = [item for item in items if item["component"] == component]
    if level:
        wanted = level.lower()
        items = [item for item in items if item["level"] == wanted]
    return items[-max(0, limit):]
