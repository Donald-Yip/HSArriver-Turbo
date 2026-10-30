#!/usr/bin/env python3
"""Validate a task's changed-file scope against its task book.

By default the tool compares the index with HEAD using
``git diff --cached --name-only``. CI may pass ``--base-ref`` to compare a pull
request branch against its base commit without rewriting the checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

SECTION_NAMES = {"allowed files": "allowed", "forbidden files": "forbidden"}


def normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./")


def resolve_task_book(root: Path, task: str) -> Path:
    candidate = Path(task)
    if candidate.suffix.lower() == ".md":
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            return candidate.resolve()

    task_dir = root / "docs" / "tasks"
    exact = task_dir / f"{task}.md"
    if exact.is_file():
        return exact
    matches = sorted(task_dir.glob(f"{task}-*.md"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(item.name for item in matches)
        raise ValueError(f"Task id {task!r} is ambiguous: {names}")
    raise FileNotFoundError(f"No task book found for {task!r} under {task_dir}")


def parse_task_book(path: Path) -> tuple[list[str], list[str]]:
    patterns: dict[str, list[str]] = {"allowed": [], "forbidden": []}
    section: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$", raw_line)
        bold_heading = re.match(r"^\s*\*\*(Allowed Files|Forbidden Files)\*\*\s*:?\s*$", raw_line, re.I)
        if heading:
            name = heading.group(2).strip().lower()
            section = SECTION_NAMES.get(name)
            continue
        if bold_heading:
            section = SECTION_NAMES[bold_heading.group(1).lower()]
            continue
        if section is None:
            continue
        bullet = re.match(r"^\s*[-*]\s+(.+?)\s*$", raw_line)
        if not bullet:
            continue
        values = re.findall(r"`([^`]+)`", bullet.group(1))
        if not values:
            candidate = bullet.group(1).strip().strip("`")
            if " " in candidate:
                continue
            values = [candidate.rstrip(".,;")]
        for value in values:
            normalized = normalize_path(value)
            if normalized:
                patterns[section].append(normalized)
    if not patterns["allowed"]:
        raise ValueError(f"Task book has no Allowed Files entries: {path}")
    return patterns["allowed"], patterns["forbidden"]


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    pattern = normalize_path(pattern)
    if pattern.endswith("/"):
        pattern += "**"
    pieces: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                if index + 2 < len(pattern) and pattern[index + 2] == "/":
                    # ``**/`` matches zero or more directories, including none.
                    pieces.append("(?:.*/)?")
                    index += 3
                else:
                    pieces.append(".*")
                    index += 2
                continue
            pieces.append("[^/]*")
        elif char == "?":
            pieces.append("[^/]")
        else:
            pieces.append(re.escape(char))
        index += 1
    pieces.append("$")
    return re.compile("".join(pieces))


def matching_pattern(path: str, patterns: Iterable[str]) -> str | None:
    normalized = normalize_path(path)
    for pattern in patterns:
        if glob_to_regex(pattern).match(normalized):
            return pattern
    return None


def validate_scope(
    changed_files: Iterable[str], allowed: Iterable[str], forbidden: Iterable[str]
) -> list[dict[str, str]]:
    allowed_list = list(allowed)
    forbidden_list = list(forbidden)
    violations: list[dict[str, str]] = []
    for raw_path in changed_files:
        path = normalize_path(raw_path)
        if not path:
            continue
        forbidden_match = matching_pattern(path, forbidden_list)
        if forbidden_match:
            violations.append(
                {
                    "kind": "forbidden",
                    "path": path,
                    "pattern": forbidden_match,
                    "message": "Changed file matches a Forbidden Files entry",
                }
            )
            continue
        allowed_match = matching_pattern(path, allowed_list)
        if not allowed_match:
            violations.append(
                {
                    "kind": "not_allowed",
                    "path": path,
                    "pattern": "",
                    "message": "Changed file is not covered by Allowed Files",
                }
            )
    return violations


def git_changed_files(root: Path, base_ref: str | None = None) -> list[str]:
    # core.quotepath=false：仓库里有中文目录（ai/任务牧 等）。git 默认把非 ASCII
    # 路径输出成 "ai/\344\273\273..." 的八进制转义，glob 匹配会全部落空；这里
    # 显式关掉转义，CI 与本机行为一致（不改用户全局 git 配置）。
    command = ["git", "-c", "core.quotepath=false", "diff",
               "--name-only", "--diff-filter=ACMRTD"]
    if base_ref is not None:
        command.append(f"{base_ref}...HEAD")
    else:
        command.append("--cached")
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git diff failed ({result.returncode}): {detail}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def check(root: Path, task: str, base_ref: str | None = None) -> dict[str, object]:
    task_book = resolve_task_book(root, task)
    allowed, forbidden = parse_task_book(task_book)
    changed = git_changed_files(root, base_ref)
    violations = validate_scope(changed, allowed, forbidden)
    return {
        "ok": not violations,
        "task": task,
        "task_book": task_book.relative_to(root).as_posix() if task_book.is_relative_to(root) else str(task_book),
        "diff_source": f"{base_ref}...HEAD" if base_ref is not None else "git diff --cached",
        "allowed": allowed,
        "forbidden": forbidden,
        "changed_files": changed,
        "violations": violations,
    }


def run_self_test() -> dict[str, object]:
    task_text = """# Fixture Task\n\n## Allowed Files\n\n- `tools/**`\n- `docs/testing/*.md`\n- `README.md`\n- `**/*.py`\n\n## Forbidden Files\n\n- `tests/**`\n- `src/**`\n"""
    allowed = ["tools/**", "docs/testing/*.md", "README.md", "**/*.py"]
    forbidden = ["tests/**", "src/**"]
    cases = [
        ("allowed nested tool", ["tools/new_gate.py"], True),
        ("allowed markdown glob", ["docs/testing/guide.md"], True),
        ("double star matches root python", ["a.py"], True),
        ("double star matches nested python", ["nested/a.py"], True),
        ("forbidden tests", ["tests/test_gate.py"], False),
        ("out of scope", ["assets/data.json"], False),
        ("exact file", ["README.md"], True),
    ]
    case_results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="hslegend-scope-") as temp:
        root = Path(temp)
        task_file = root / "docs" / "tasks" / "R99-fixture.md"
        task_file.parent.mkdir(parents=True)
        task_file.write_text(task_text, encoding="utf-8")
        parsed_allowed, parsed_forbidden = parse_task_book(task_file)
        parser_ok = parsed_allowed == allowed and parsed_forbidden == forbidden
        case_results.append({"name": "parse task book", "ok": parser_ok, "expected": "pass"})
        for name, changed, expected_ok in cases:
            violations = validate_scope(changed, allowed, forbidden)
            actual_ok = not violations
            case_results.append(
                {
                    "name": name,
                    "ok": actual_ok == expected_ok,
                    "expected": "pass" if expected_ok else "fail",
                    "actual": "pass" if actual_ok else violations,
                }
            )
    return {"ok": all(item["ok"] for item in case_results), "mode": "self-test", "cases": case_results}


def print_report(report: dict[str, object]) -> None:
    if report["ok"]:
        print(
            f"[scope] PASS: {report['task']} ({len(report['changed_files'])} changed files, "
            f"source: {report['diff_source']})"
        )
        return
    print(f"[scope] FAIL: {report['task']}", file=sys.stderr)
    for item in report["violations"]:  # type: ignore[index]
        suffix = f" via {item['pattern']}" if item.get("pattern") else ""
        print(f"- {item['path']}: {item['message']}{suffix}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default=os.environ.get("TASK_ID"), help="Task id or task-book path (default: TASK_ID)")
    parser.add_argument("--root", type=Path, default=None, help="Repository root (default: repository containing this tool)")
    parser.add_argument("--base-ref", help="Compare BASE_REF...HEAD instead of the staged index (CI mode)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--self-test", action="store_true", help="Run built-in parser and matcher fixtures")
    args = parser.parse_args(argv)

    if args.self_test:
        report = run_self_test()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            status = "PASS" if report["ok"] else "FAIL"
            print(f"[scope:self-test] {status}: {sum(1 for item in report['cases'] if item['ok'])}/{len(report['cases'])} cases")
            for item in report["cases"]:
                print(f"- {item['name']}: {'PASS' if item['ok'] else 'FAIL'}")
        return 0 if report["ok"] else 1

    if not args.task:
        message = "Missing --task (or TASK_ID). Scope check was not run."
        if args.json:
            print(json.dumps({"ok": False, "error": "task_required", "message": message}, ensure_ascii=False, indent=2))
        else:
            print(f"[scope] SKIP: {message}", file=sys.stderr)
        return 2

    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    try:
        report = check(root, args.task, args.base_ref)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"[scope] ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
