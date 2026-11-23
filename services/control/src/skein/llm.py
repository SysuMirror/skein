"""模型调用接口。真实实现走 A 上的薄网关，这里只定义契约。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, request: dict) -> dict:
        """发送一次补全请求，返回解析后的 JSON 对象。"""
