"""pytest 夹具。共用的工具函数在 helpers.py。"""

from __future__ import annotations

import pytest
from helpers import Rig, make_app

from skein import passwords


@pytest.fixture(autouse=True)
def fast_hashes(monkeypatch):
    """测试里把 scrypt 的代价调到最低；真实参数的形状另有专门用例核对。"""
    monkeypatch.setattr(passwords, "_N", 1 << 10)
    monkeypatch.setattr(passwords, "_burn_hash", None)


@pytest.fixture()
def rigs(tmp_path):
    """``rigs(provider=None, **kwargs)`` 建一个装好的控制面；参数见 helpers.make_app。
    同一个测试里建几个时各自用独立的库文件。"""
    made: list = []
    counter = {"n": 0}

    def factory(provider=None, **kwargs) -> Rig:
        counter["n"] += 1
        kwargs.setdefault("name", f"app{counter['n']}.db")
        rig = make_app(tmp_path, provider, **kwargs)
        made.append(rig)
        return rig

    yield factory
    for rig in made:
        rig.conn.close()
