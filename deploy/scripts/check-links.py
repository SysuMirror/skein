#!/usr/bin/env python3
"""Check that relative links in the repository's Markdown files point at files that exist.

External links (http, https, mailto) and pure anchors are not fetched — CI has no reliable
network and an external site being down should not fail a pull request. Run it locally
with ``make check-docs``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", "node_modules", "dist", ".venv", "__pycache__", "data"}
# [text](target) and [text](<target>) — images included
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")


def markdown_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.md"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return sorted(files)


def targets(text: str) -> list[str]:
    """链接目标，去掉代码块与行内代码（那里面的括号不是链接）。"""
    stripped = INLINE_CODE.sub("", FENCE.sub("", text))
    return [match.group(1) for match in LINK.finditer(stripped)]


def check(path: Path) -> list[str]:
    problems: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for target in targets(text):
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        location = target.split("#", 1)[0]
        if not location:
            continue
        location = unquote(location)
        if location.startswith("/"):
            problems.append(f"{path.relative_to(ROOT)}: absolute path {target!r} (use a relative link)")
            continue
        if not (path.parent / location).exists():
            problems.append(f"{path.relative_to(ROOT)}: missing {target!r}")
    return problems


def main() -> int:
    problems = [problem for path in markdown_files() for problem in check(path)]
    for problem in problems:
        print(problem)
    count = len(markdown_files())
    if problems:
        print(f"\n{len(problems)} broken link(s) in {count} file(s)")
        return 1
    print(f"all relative links resolve ({count} markdown files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
