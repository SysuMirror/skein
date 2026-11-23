"""版本对齐：控制面、控制台、节点代理与 CHANGELOG 写的是同一个版本。"""

from __future__ import annotations

import json
from pathlib import Path

import skein

ROOT = Path(__file__).resolve().parents[3]


def test_all_packages_share_one_version():
    version = skein.__version__
    assert version.count(".") == 2, version
    console = json.loads((ROOT / "services/console/package.json").read_text(encoding="utf-8"))
    agent = json.loads((ROOT / "services/node-agent/package.json").read_text(encoding="utf-8"))
    assert console["version"] == agent["version"] == version
    pyproject = (ROOT / "services/control/pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{version}"' in pyproject


def test_changelog_has_an_entry_for_the_version():
    version = skein.__version__
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog
    # Keep a Changelog 的链接区
    assert f"[{version}]:" in changelog
