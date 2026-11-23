"""滑动窗口限速：登录尝试、公共 API 调用共用。

每个键（来源 IP、账号名、助手凭证）各自一个窗口。进程内实现，不落盘：控制面只有一个
进程，重启即清零，这对「挡住撞库和刷接口」已经够用，也不引入额外的存储依赖。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

# 键的数量上限：超过后清掉已经空了的窗口，防止被随机键撑爆内存
_MAX_KEYS = 100_000


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = max(1, int(limit))
        self.window_s = float(window_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> tuple[bool, float]:
        """记一次尝试。返回 (放行与否, 还要等几秒才有名额)。"""
        now = self._clock()
        with self._lock:
            if key not in self._hits and len(self._hits) >= _MAX_KEYS:
                self._evict(now - self.window_s)
            window = self._hits.setdefault(key, deque())
            self._trim(window, now)
            if len(window) >= self.limit:
                return False, self._wait_for(window, now)
            window.append(now)
            return True, 0.0

    def retry_after(self, key: str) -> float:
        """不记一次、只看：这个键现在被限到了吗？返回 0 表示没有，否则是还要等几秒。"""
        now = self._clock()
        with self._lock:
            window = self._hits.get(key)
            if window is None:
                return 0.0
            self._trim(window, now)
            return self._wait_for(window, now) if len(window) >= self.limit else 0.0

    def snapshot(self, key: str) -> tuple[int, float]:
        """这个键还剩几个名额、最早空出一个名额是几秒后。给限速响应头用。"""
        now = self._clock()
        with self._lock:
            window = self._hits.get(key)
            if window is None:
                return self.limit, 0.0
            self._trim(window, now)
            return max(0, self.limit - len(window)), self._wait_for(window, now)

    def _trim(self, window: deque[float], now: float) -> None:
        cutoff = now - self.window_s
        while window and window[0] <= cutoff:
            window.popleft()

    def _wait_for(self, window: deque[float], now: float) -> float:
        """最早的那次尝试滑出窗口还要几秒——那就是名额空出来的时刻。"""
        return max(0.0, window[0] + self.window_s - now) if window else 0.0

    def _evict(self, cutoff: float) -> None:
        for key in [k for k, w in self._hits.items() if not w or w[-1] <= cutoff]:
            self._hits.pop(key, None)
        if len(self._hits) >= _MAX_KEYS:
            # 全是活跃窗口：整体清空。宁可短暂放宽，也不让内存无限涨
            self._hits.clear()
