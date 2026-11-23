"""滑动窗口限速。"""

from __future__ import annotations

from skein import ratelimit


def make(limit: int = 3, window: float = 60.0):
    clock = {"now": 1000.0}
    limiter = ratelimit.RateLimiter(limit, window, clock=lambda: clock["now"])
    return limiter, clock


def test_allows_up_to_the_limit_then_refuses():
    limiter, _ = make(3)
    assert [limiter.hit("k") for _ in range(3)] == [(True, 0.0)] * 3
    allowed, wait = limiter.hit("k")
    assert (allowed, wait) == (False, 60.0)


def test_the_window_slides():
    limiter, clock = make(2, window=10.0)
    limiter.hit("k")
    clock["now"] += 4
    limiter.hit("k")
    assert limiter.hit("k")[0] is False
    clock["now"] += 6.5  # 第一次尝试滑出窗口
    assert limiter.hit("k") == (True, 0.0)


def test_keys_are_independent():
    limiter, _ = make(1)
    assert limiter.hit("a")[0] is True
    assert limiter.hit("b")[0] is True
    assert limiter.hit("a")[0] is False


def test_peeking_does_not_count_as_an_attempt():
    limiter, clock = make(2, window=10.0)
    assert limiter.retry_after("k") == 0.0
    limiter.hit("k")
    limiter.hit("k")
    assert limiter.retry_after("k") == 10.0
    clock["now"] += 11
    assert limiter.retry_after("k") == 0.0
    assert limiter.hit("k")[0] is True


def test_snapshot_describes_the_remaining_budget():
    limiter, clock = make(3, window=10.0)
    assert limiter.snapshot("k") == (3, 0.0)
    limiter.hit("k")
    limiter.hit("k")
    assert limiter.snapshot("k") == (1, 10.0)
    limiter.hit("k")
    assert limiter.snapshot("k") == (0, 10.0)
    clock["now"] += 3
    assert limiter.snapshot("k") == (0, 7.0)


def test_idle_keys_do_not_accumulate(monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_KEYS", 4)
    limiter, clock = make(1, window=5.0)
    for index in range(10):
        limiter.hit(f"k{index}")
        clock["now"] += 10  # 让上一个键的窗口过期
    assert len(limiter._hits) <= 4
