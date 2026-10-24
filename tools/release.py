#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发版辅助：校验版本号/CHANGELOG、按日期改版本号、打 ``vYYYY.MM.DD`` 标签。

GitHub 本身没有“版本号”对象：本项目约定

* ``version.py`` 的 ``VERSION`` 是唯一版本源，值 = 发布日期 ``YYYY.MM.DD``；
* 发版提交要同步在 ``CHANGELOG.md`` 表格顶部加一行同日期记录；
* 版本提交后打**注释标签** ``vYYYY.MM.DD``（与 VERSION 逐字一致），
  再在 GitHub 上建同名 Release（``gh release create`` 或网页操作）。

用法（在仓库根目录）::

    python tools/release.py check
    python tools/release.py bump 2026.10.24 "四选一选目标 / 浮窗自查 / 发版流程"
    python tools/release.py tag
    python tools/release.py tag --push --remote mine

``bump`` 默认只改文件、不提交；``tag`` 默认不 push（AGENTS/GIT.md 要求先问用户）。
``check`` 会跑 ``tests.test_version`` 并在版本/CHANGELOG 不一致时退出码 1。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "version.py"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"

_VERSION_RE = re.compile(r'^VERSION\s*=\s*"[^"]*"', re.MULTILINE)
_VERSION_VALUE_RE = re.compile(r'^VERSION\s*=\s*"([^"]+)"', re.MULTILINE)
_CHANGELOG_HEAD_RE = re.compile(r"^\|\s*(\d{4})-(\d{2})-(\d{2})\s*\|", re.MULTILINE)
_DATE_INPUT_RE = re.compile(r"^(\d{4})[.\-]?(\d{2})[.\-]?(\d{2})$")
_TABLE_RULE = "| --- | --- | --- |"


def parse_release_date(value) -> str:
    """把 ``2026.10.24`` / ``2026-10-24`` / ``2026-1024`` 变成 ``2026.10.24``。"""
    match = _DATE_INPUT_RE.fullmatch(str(value or "").strip())
    if match is None:
        raise SystemExit(f"日期格式不对：{value!r}（应为 2026.10.24 / 2026-10-24）")
    try:
        datetime(*(int(part) for part in match.groups()))
    except ValueError as exc:
        raise SystemExit(f"不是真实日期：{value!r}（{exc}）") from exc
    year, month, day = match.groups()
    return f"{year}.{month}.{day}"


def read_version(text: str = None) -> str:
    if text is None:
        text = VERSION_PATH.read_text(encoding="utf-8")
    match = _VERSION_VALUE_RE.search(text)
    if match is None:
        raise RuntimeError("version.py 里找不到 VERSION = \"...\"")
    return parse_release_date(match.group(1))


def replace_version(text: str, version: str) -> str:
    """把 version.py 里唯一的 ``VERSION = "..."`` 换成新日期。"""
    if len(_VERSION_RE.findall(text)) != 1:
        raise RuntimeError("version.py 里必须恰好有一行 VERSION = \"...\"")
    return _VERSION_RE.sub(f'VERSION = "{version}"', text, count=1)


def changelog_head_date(text: str = None) -> str:
    if text is None:
        text = CHANGELOG_PATH.read_text(encoding="utf-8")
    match = _CHANGELOG_HEAD_RE.search(text)
    if match is None:
        raise RuntimeError("CHANGELOG.md 里找不到第一行日期")
    return ".".join(match.groups())


def changelog_row(version: str, summary: str) -> str:
    """CHANGELOG 表格的一行（日期用 ``YYYY-MM-DD``）。"""
    summary = " ".join(str(summary or "").split())
    if not summary:
        raise ValueError("CHANGELOG 摘要不能为空")
    return f"| {version.replace('.', '-')} | 本次 | {summary} |"


def insert_changelog_row(text: str, row: str) -> str:
    """把新行插到表格分隔线下面（最新在最上），同日期已存在则拒绝。"""
    lines = text.splitlines()
    try:
        rule_index = lines.index(_TABLE_RULE)
    except ValueError as exc:
        raise RuntimeError(
            f"CHANGELOG 表格结构变了：找不到 {_TABLE_RULE!r}") from exc
    date = row.split("|")[1].strip() if "|" in row else ""
    if any(line.startswith(f"| {date} ") for line in lines):
        raise RuntimeError(f"CHANGELOG 已经有 {date} 这天的记录了")
    lines.insert(rule_index + 1, row)
    return "\n".join(lines) + "\n"


def _run(command: list[str]) -> int:
    return subprocess.run(command, cwd=str(ROOT)).returncode


def _tag_name(version: str) -> str:
    return f"v{version}"


def command_check(_args) -> int:
    version = read_version()
    head = changelog_head_date()
    problems = []
    if version != head:
        problems.append(
            f"version.py VERSION={version} 与 CHANGELOG 首行 {head} 不一致")
    try:
        datetime.strptime(version, "%Y.%m.%d")
    except ValueError:
        problems.append(f"VERSION 不是真实日期：{version}")
    if problems:
        for problem in problems:
            print(f"[release] {problem}")
        return 1
    print(f"[release] 版本号 {_tag_name(version)}，与 CHANGELOG 首行一致。")
    print("[release] 跑版本号测试：python -m unittest -q tests.test_version")
    code = _run([sys.executable, "-m", "unittest", "-q", "tests.test_version"])
    if code != 0:
        print("[release] 版本测试未通过，先修再发版。")
        return code
    print("[release] 通过。发版步骤见 docs/GIT.md「发布版本号」。")
    return 0


def _write_like(path: Path, text: str, original_raw: bytes) -> None:
    """按文件原来的换行符写回（CHANGELOG 是 LF、version.py 是 CRLF）。

    `Path.write_text` 在 Windows 上会把 ``\n`` 统一转成 CRLF；对 LF 文件会把
    整个文件“改一遍行尾”，diff 变成几百行。这里保留原文件的 EOL。
    """
    newline = b"\r\n" if b"\r\n" in original_raw else b"\n"
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    data = normalized.replace("\n", newline.decode("ascii")).encode("utf-8")
    path.write_bytes(data)


def command_bump(args) -> int:
    version = parse_release_date(args.version)
    summary = args.summary
    current = read_version()
    version_raw = VERSION_PATH.read_bytes()
    changelog_raw = CHANGELOG_PATH.read_bytes()
    text = version_raw.decode("utf-8")
    changelog = changelog_raw.decode("utf-8")
    try:
        new_changelog = insert_changelog_row(
            changelog, changelog_row(version, summary))
    except RuntimeError as exc:
        print(f"[release] {exc}")
        return 1
    print(f"[release] version.py: {current} → {version}")
    print(f"[release] CHANGELOG 顶部新增 {version.replace('.', '-')} 一行")
    if args.dry_run:
        print("[release] --dry-run：未写盘。")
        return 0
    _write_like(VERSION_PATH, replace_version(text, version), version_raw)
    _write_like(CHANGELOG_PATH, new_changelog, changelog_raw)
    print("[release] 已写入。下一步：")
    print(f"  git add version.py CHANGELOG.md")
    print(f'  git commit -m "chore(release): {_tag_name(version)}"')
    print(f"  python tools/release.py tag")
    print(f"  git push <remote> main && git push <remote> {_tag_name(version)}")
    print(f'  gh release create {_tag_name(version)} --title "{_tag_name(version)}" '
          "--generate-notes")
    return 0


def command_tag(args) -> int:
    version = read_version()
    tag = _tag_name(version)
    exists = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        cwd=str(ROOT), capture_output=True, text=True)
    if exists.returncode == 0:
        print(f"[release] 标签 {tag} 已存在，不重复创建。")
        return 1
    code = _run(["git", "tag", "-a", tag, "-m", tag])
    if code != 0:
        return code
    print(f"[release] 已创建注释标签 {tag}")
    if args.push:
        remote = args.remote
        for command in (["git", "push", remote, "main"],
                        ["git", "push", remote, tag]):
            code = _run(command)
            if code != 0:
                return code
        print(f"[release] 已推送到 {remote}：main + {tag}")
    else:
        print("[release] 未 push（需要时加 --push --remote <远端>，先确认用户同意）。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="版本号/CHANGELOG/标签 发版辅助")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="校验版本号与 CHANGELOG，跑 tests.test_version")
    check.set_defaults(handler=command_check)

    bump = sub.add_parser("bump", help="改 version.py 并在 CHANGELOG 顶部加一行")
    bump.add_argument("version", help="新版本号，例如 2026.10.24")
    bump.add_argument("summary", help="CHANGELOG 一句话摘要")
    bump.set_defaults(handler=command_bump)

    tag = sub.add_parser("tag", help="按 version.py 打 vYYYY.MM.DD 注释标签")
    tag.add_argument("--push", action="store_true", help="同时 push main 和标签")
    tag.add_argument("--remote", default="mine", help="push 的远端（默认 mine）")
    tag.set_defaults(handler=command_tag)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
