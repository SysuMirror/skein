"""可选集成：不属于雁阵核心、只在特定部署里才需要的能力。

每个子模块自带 ``enabled`` 判定与 ``install(app, ...)``；没配对应的环境变量就不加载、
不暴露任何路由。核心（``skein.api``）只在启动时按配置逐个询问并挂载。
新增一种集成：在本包下加模块、实现 ``Integration``，然后在 ``skein.api`` 的集成清单里
登记一行即可。
"""

from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI


class Integration(Protocol):
    """一个可选集成的形状。"""

    name: str
    enabled: bool

    def install(self, app: FastAPI) -> None: ...


def install_all(app: FastAPI, integrations: list[Integration]) -> list[str]:
    """挂载所有已启用的集成，返回挂载了的名字。"""
    mounted: list[str] = []
    for item in integrations:
        if getattr(item, "enabled", False):
            item.install(app)
            mounted.append(item.name)
    return mounted
