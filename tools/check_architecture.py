#!/usr/bin/env python3
"""Static architecture checks for the new ``src/hslegend`` package.

The checker intentionally uses only the Python standard library so it can run in
the same minimal environment as the repository's compile gate.  It reports
dependency-direction violations and import cycles without importing project
code.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

STANDARD_LIBRARY = frozenset(sys.stdlib_module_names) | {"__future__"}

APPLICATION_FORBIDDEN = (
    "hslegend.adapters",
    "hslegend.bootstrap",
    "hslegend.entrypoints",
    "hslegend.legacy",
    "hslegend.plugins",
)
ADAPTER_FORBIDDEN = (
    "hslegend.use_cases",
    "hslegend.application.use_cases",
    "hslegend.bootstrap",
    "hslegend.entrypoints",
)
ENTRYPOINT_ALLOWED = (
    "hslegend.application",
    "hslegend.bootstrap",
    "hslegend.entrypoints",
)
PLUGIN_FORBIDDEN = (
    "hslegend.adapters",
    "hslegend.bootstrap",
    "hslegend.entrypoints",
    "hslegend.runtime",
    "hslegend.io",
    "hslegend.input",
)
PLUGIN_IO_MODULES = frozenset(
    {
        "io",
        "os",
        "pathlib",
        "shutil",
        "tempfile",
        "socket",
        "subprocess",
        "threading",
        "multiprocessing",
        "http",
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
        "win32file",
        "win32gui",
        "win32api",
        "win32clipboard",
        "win32con",
        "win32process",
        "win32ui",
        "win32com",
        "pythoncom",
        "cv2",
        "PIL",
        "mss",
        "pyscreeze",
        "pywinauto",
        "uiautomation",
        "keyboard",
        "pynput",
        "pyautogui",
    }
)
PLUGIN_IO_SEGMENTS = frozenset({"io", "input", "inputs", "io_backend", "input_backend"})
PLUGIN_IO_CALLS = frozenset({"open", "input", "exec", "eval", "compile", "__import__"})
LEGACY_ACL_PACKAGE = "hslegend.adapters.legacy"
LEGACY_GOD_MODULES = frozenset({"FSM_action", "manual_controller", "web_ui"})


@dataclass(frozen=True)
class SourceModule:
    name: str
    path: Path
    relative_path: str
    layer: str | None
    tree: ast.AST


def module_name_for(path: Path, src_root: Path) -> str:
    relative = path.relative_to(src_root)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def layer_for(module_name: str) -> str | None:
    parts = module_name.split(".")
    if len(parts) >= 2 and parts[0] == "hslegend":
        return parts[1]
    return None


def is_module_or_prefix(target: str, prefix: str) -> bool:
    return target == prefix or target.startswith(prefix + ".")


def package_for(module: SourceModule) -> str:
    if module.path.name == "__init__.py":
        return module.name
    return module.name.rpartition(".")[0]


def resolve_relative(package: str, level: int, imported: str | None) -> str | None:
    if level <= 0:
        return imported
    parts = package.split(".") if package else []
    ascend = level - 1
    if ascend > len(parts):
        return None
    if ascend:
        parts = parts[:-ascend]
    if imported:
        parts.extend(imported.split("."))
    return ".".join(part for part in parts if part)


def import_targets(module: SourceModule, node: ast.AST) -> list[tuple[str, int, str]]:
    """Return (target module, line, raw import) tuples for one import node."""
    targets: list[tuple[str, int, str]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            targets.append((alias.name, node.lineno, alias.name))
        return targets

    if not isinstance(node, ast.ImportFrom):
        return targets

    package = package_for(module)
    base = resolve_relative(package, node.level, node.module) if node.level else node.module
    raw = "." * node.level + (node.module or "")
    if base:
        targets.append((base, node.lineno, raw))
    if node.module is None and node.level:
        base = base or package
        targets = []
    for alias in node.names:
        if alias.name == "*":
            continue
        if base:
            targets.append((f"{base}.{alias.name}", node.lineno, raw))
    return targets


def is_legacy_root_module(target: str, root: Path) -> bool:
    root_name = target.split(".", 1)[0]
    return (root / f"{root_name}.py").is_file()


def is_legacy_src_package(target: str) -> bool:
    return target == "src" or target.startswith("src.")


def in_legacy_acl(module: SourceModule) -> bool:
    return module.name == LEGACY_ACL_PACKAGE or module.name.startswith(
        LEGACY_ACL_PACKAGE + "."
    )


def is_forbidden_legacy_god_module(target: str, root: Path) -> bool:
    del root
    root_name = target.split(".", 1)[0]
    return root_name in LEGACY_GOD_MODULES


def domain_violation(target: str) -> str | None:
    if target == "hslegend" or target.startswith("hslegend."):
        if target == "hslegend" or not is_module_or_prefix(target, "hslegend.domain"):
            return "domain_imports_outside_domain"
        return None
    root_name = target.split(".", 1)[0]
    if root_name not in STANDARD_LIBRARY:
        return "domain_external_import"
    return None


def plugin_io_violation(target: str) -> str | None:
    root_name = target.split(".", 1)[0]
    if root_name in PLUGIN_IO_MODULES:
        return "plugin_imports_io_backend"
    if target.startswith("hslegend."):
        segments = set(target.split("."))
        if segments & PLUGIN_IO_SEGMENTS:
            return "plugin_imports_io_backend"
    return None


def entrypoint_violation(target: str) -> str | None:
    if target == "hslegend" or target.startswith("hslegend."):
        if any(is_module_or_prefix(target, prefix) for prefix in ENTRYPOINT_ALLOWED):
            return None
        return "entrypoint_imports_outside_allowlist"
    root_name = target.split(".", 1)[0]
    if root_name in STANDARD_LIBRARY:
        return None
    return "entrypoint_imports_non_stdlib"


def module_bound_names(tree: ast.AST) -> set[str]:
    """Return names bound by module-level statements.

    This deliberately only performs a cheap top-level shadowing check for plugin
    built-in calls. It removes the obvious ``def open(...)``/``open = ...`` false
    positive without pretending to implement Python's full scoping rules.
    """

    names: set[str] = set()

    def add_target(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                add_target(item)

    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                add_target(target)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
    return names


def call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "builtins"
    ):
        return f"builtins.{node.func.attr}"
    return None


def violation_for_target(
    module: SourceModule, target: str, root: Path
) -> tuple[str, str] | None:
    layer = module.layer
    if is_forbidden_legacy_god_module(target, root):
        return (
            "imports_forbidden_legacy_god_module",
            "FSM_action/manual_controller/web_ui are forbidden in every layer",
        )
    if is_legacy_root_module(target, root):
        if in_legacy_acl(module):
            return None
        return (
            "imports_legacy_root_module",
            "Only hslegend.adapters.legacy may import stable root legacy modules",
        )
    if is_legacy_src_package(target):
        if in_legacy_acl(module):
            return None
        return (
            "imports_legacy_src_package",
            "Only hslegend.adapters.legacy may import legacy src.* packages",
        )

    if layer == "domain":
        kind = domain_violation(target)
        if kind:
            return kind, "Domain may import only the standard library and hslegend.domain"
    elif layer == "application":
        if any(is_module_or_prefix(target, prefix) for prefix in APPLICATION_FORBIDDEN):
            return "application_imports_outer_layer", "Application cannot import adapters/plugins/bootstrap/entrypoints/legacy"
    elif layer == "adapters":
        if any(is_module_or_prefix(target, prefix) for prefix in ADAPTER_FORBIDDEN):
            return "adapter_imports_inner_or_composition", "Adapters cannot import use_cases/bootstrap/entrypoints"
    elif layer == "entrypoints":
        kind = entrypoint_violation(target)
        if kind:
            return kind, "Entrypoints may import only stdlib, application, bootstrap, and sibling entrypoints"
    elif layer == "plugins":
        if any(is_module_or_prefix(target, prefix) for prefix in PLUGIN_FORBIDDEN):
            return "plugin_imports_forbidden_backend", "Plugins cannot import adapters/runtime/IO/input backends"
        kind = plugin_io_violation(target)
        if kind:
            return kind, "Plugins cannot import IO or input backends"
    return None


def scan_sources(root: Path) -> tuple[list[SourceModule], list[dict[str, object]]]:
    src_root = root / "src"
    package_root = src_root / "hslegend"
    if not package_root.is_dir():
        return [], []

    modules: list[SourceModule] = []
    violations: list[dict[str, object]] = []
    for path in sorted(package_root.rglob("*.py")):
        name = module_name_for(path, src_root)
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            violations.append(
                {
                    "kind": "syntax_error",
                    "file": relative,
                    "line": getattr(exc, "lineno", 0) or 0,
                    "module": name,
                    "imported": "",
                    "message": f"Cannot parse Python module: {exc}",
                }
            )
            continue
        modules.append(SourceModule(name, path, relative, layer_for(name), tree))
    return modules, violations


def collect_violations(root: Path, modules: Iterable[SourceModule]) -> list[dict[str, object]]:
    violations: list[dict[str, object]] = []
    for module in modules:
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            seen: set[str] = set()
            for target, line, raw in import_targets(module, node):
                if not target or target in seen:
                    continue
                seen.add(target)
                matched = violation_for_target(module, target, root)
                if not matched:
                    continue
                kind, message = matched
                violations.append(
                    {
                        "kind": kind,
                        "file": module.relative_path,
                        "line": line,
                        "module": module.name,
                        "imported": target,
                        "raw_import": raw,
                        "message": message,
                    }
                )

        if module.layer != "plugins":
            continue
        bound = module_bound_names(module.tree)
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            if not name or name not in PLUGIN_IO_CALLS:
                continue
            short_name = name.rsplit('.', 1)[-1]
            if short_name in bound:
                continue
            violations.append(
                {
                    "kind": "plugin_io_call",
                    "file": module.relative_path,
                    "line": node.lineno,
                    "module": module.name,
                    "imported": name,
                    "raw_import": "",
                    "message": "Plugins cannot call built-in IO/code-loading functions",
                }
            )
    return violations


def import_graph(modules: Iterable[SourceModule]) -> dict[str, set[str]]:
    module_list = list(modules)
    names = {module.name for module in module_list}
    graph: dict[str, set[str]] = {name: set() for name in names}
    for module in module_list:
        for node in ast.walk(module.tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for target, _line, _raw in import_targets(module, node):
                if target in names and target != module.name:
                    graph[module.name].add(target)
    return graph


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    state: dict[str, int] = {node: 0 for node in graph}
    stack: list[str] = []
    cycles: set[tuple[str, ...]] = set()

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for target in sorted(graph[node]):
            if state.get(target, 0) == 0:
                visit(target)
            elif state.get(target) == 1:
                start = stack.index(target)
                cycle = stack[start:] + [target]
                body = cycle[:-1]
                if body:
                    rotations = [tuple(body[i:] + body[:i]) for i in range(len(body))]
                    cycles.add(min(rotations))
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if state[node] == 0:
            visit(node)
    return [list(cycle) + [cycle[0]] for cycle in sorted(cycles)]


def check(root: Path) -> dict[str, object]:
    modules, syntax_violations = scan_sources(root)
    violations = syntax_violations + collect_violations(root, modules)
    cycles = find_cycles(import_graph(modules))
    return {
        "ok": not violations and not cycles,
        "root": str(root.resolve()),
        "package_root": str((root / "src" / "hslegend").resolve()),
        "checked_files": len(modules),
        "violations": violations,
        "cycles": cycles,
    }


def print_report(report: dict[str, object]) -> None:
    if report["ok"]:
        if report["checked_files"]:
            print(f"[architecture] PASS: {report['checked_files']} files checked")
        else:
            print("[architecture] PASS: src/hslegend is not present yet")
        return
    print("[architecture] FAIL", file=sys.stderr)
    for item in report["violations"]:  # type: ignore[index]
        print(
            f"- {item['file']}:{item['line']} [{item['kind']}] "
            f"{item['imported']} ({item['message']})",
            file=sys.stderr,
        )
    for cycle in report["cycles"]:  # type: ignore[index]
        print(f"- import cycle: {' -> '.join(cycle)}", file=sys.stderr)


def write_fixture(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def run_self_test() -> dict[str, object]:
    clean_files = {
        "click.py": "",
        "src/hslegend/__init__.py": "",
        "src/hslegend/domain/__init__.py": "",
        "src/hslegend/domain/model.py": "from dataclasses import dataclass\n",
        "src/hslegend/application/__init__.py": "",
        "src/hslegend/application/use_case.py": "from hslegend.domain.model import dataclass\n",
        "src/hslegend/adapters/__init__.py": "",
        "src/hslegend/adapters/io.py": "from hslegend.application.use_case import dataclass\n",
        "src/hslegend/adapters/legacy/__init__.py": "import click\nfrom src.ocr.stable_reader import StableRecommendationReader\n",
        "src/hslegend/plugins/__init__.py": "",
        "src/hslegend/plugins/plan.py": "from hslegend.domain.model import dataclass\n",
        "src/hslegend/bootstrap/__init__.py": "",
        "src/hslegend/entrypoints/__init__.py": "",
    }
    cases: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="hslegend-architecture-") as temp:
        base = Path(temp)
        clean = base / "clean"
        write_fixture(clean, clean_files)
        report = check(clean)
        cases.append({"name": "compliant tree", "ok": bool(report["ok"]), "expected": "pass"})

        fixtures = {
            "domain external import": {
                "src/hslegend/domain/bad.py": "import requests\n",
            },
            "application imports adapter": {
                "src/hslegend/application/bad.py": "from hslegend.adapters.io import x\n",
            },
            "adapter imports use_cases": {
                "src/hslegend/adapters/bad.py": "from hslegend.application.use_cases.foo import x\n",
                "src/hslegend/application/use_cases/__init__.py": "",
            },
            "plugin imports input backend": {
                "src/hslegend/plugins/bad.py": "from hslegend.adapters.input_backend import x\n",
            },
            "entrypoint imports adapter": {
                "src/hslegend/entrypoints/bad.py": "from hslegend.adapters.io import x\n",
            },
            "entrypoint imports plugin": {
                "src/hslegend/entrypoints/bad.py": "from hslegend.plugins.plan import x\n",
            },
            "entrypoint imports domain": {
                "src/hslegend/entrypoints/bad.py": "from hslegend.domain import GameSnapshot\n",
            },
            "entrypoint imports third party": {
                "src/hslegend/entrypoints/bad.py": "import requests\n",
            },
            "all layers import legacy root": {
                "src/hslegend/domain/bad.py": "import click\n",
                "src/hslegend/application/bad.py": "import click\n",
                "src/hslegend/adapters/bad.py": "import click\n",
                "src/hslegend/plugins/bad.py": "import click\n",
                "src/hslegend/bootstrap/bad.py": "import click\n",
                "src/hslegend/entrypoints/bad.py": "import click\n",
            },
            "application imports plugins": {
                "src/hslegend/application/bad.py": "from hslegend.plugins.plan import x\n",
            },
            "all layers import legacy src": {
                "src/hslegend/domain/bad.py": "from src.parser.recommendation_parser import x\n",
                "src/hslegend/application/bad.py": "from src.parser.recommendation_parser import x\n",
                "src/hslegend/adapters/bad.py": "from src.parser.recommendation_parser import x\n",
                "src/hslegend/plugins/bad.py": "from src.parser.recommendation_parser import x\n",
                "src/hslegend/bootstrap/bad.py": "from src.parser.recommendation_parser import x\n",
                "src/hslegend/entrypoints/bad.py": "from src.parser.recommendation_parser import x\n",
            },
            "legacy ACL imports forbidden god module": {
                "src/hslegend/adapters/legacy/bad.py": "import FSM_action\n",
            },
            "plugin builtin IO calls": {
                "src/hslegend/plugins/bad.py": "open('a')\ninput('x')\nexec('x')\neval('1')\ncompile('x', 'x', 'exec')\n__import__('os')\n",
            },
            "plugin builtin input": {
                "src/hslegend/plugins/bad.py": "input('x')\n",
            },
            "plugin imports tempfile": {
                "src/hslegend/plugins/bad.py": "import tempfile\n",
            },
            "plugin imports win32file": {
                "src/hslegend/plugins/bad.py": "import win32file\n",
            },
            "plugin imports cv2": {
                "src/hslegend/plugins/bad.py": "import cv2\n",
            },
            "plugin IO modules": {
                "src/hslegend/plugins/bad.py": "import os, pathlib, io, shutil, socket, urllib, http, requests, subprocess, threading, multiprocessing\n",
            },
            "import cycle": {
                "src/hslegend/domain/a.py": "from hslegend.domain.b import value\n",
                "src/hslegend/domain/b.py": "from hslegend.domain.a import value\n",
            },
        }
        expected_kinds = {
            "domain external import": "domain_external_import",
            "application imports adapter": "application_imports_outer_layer",
            "adapter imports use_cases": "adapter_imports_inner_or_composition",
            "plugin imports input backend": "plugin_imports_forbidden_backend",
            "entrypoint imports adapter": "entrypoint_imports_outside_allowlist",
            "entrypoint imports plugin": "entrypoint_imports_outside_allowlist",
            "entrypoint imports domain": "entrypoint_imports_outside_allowlist",
            "entrypoint imports third party": "entrypoint_imports_non_stdlib",
            "all layers import legacy root": "imports_legacy_root_module",
            "all layers import legacy src": "imports_legacy_src_package",
            "legacy ACL imports forbidden god module": "imports_forbidden_legacy_god_module",
            "application imports plugins": "application_imports_outer_layer",
            "plugin builtin IO calls": "plugin_io_call",
            "plugin builtin input": "plugin_io_call",
            "plugin imports tempfile": "plugin_imports_io_backend",
            "plugin imports win32file": "plugin_imports_io_backend",
            "plugin imports cv2": "plugin_imports_io_backend",
            "plugin IO modules": "plugin_imports_io_backend",
        }
        expected_files = {
            "all layers import legacy root": {
                "src/hslegend/domain/bad.py",
                "src/hslegend/application/bad.py",
                "src/hslegend/adapters/bad.py",
                "src/hslegend/plugins/bad.py",
                "src/hslegend/bootstrap/bad.py",
                "src/hslegend/entrypoints/bad.py",
            },
            "all layers import legacy src": {
                "src/hslegend/domain/bad.py",
                "src/hslegend/application/bad.py",
                "src/hslegend/adapters/bad.py",
                "src/hslegend/plugins/bad.py",
                "src/hslegend/bootstrap/bad.py",
                "src/hslegend/entrypoints/bad.py",
            },
            "legacy ACL imports forbidden god module": {
                "src/hslegend/adapters/legacy/bad.py",
            },
        }
        expected_imports = {
            "plugin builtin IO calls": {"open", "input", "exec", "eval", "compile", "__import__"},
            "plugin builtin input": {"input"},
            "plugin imports tempfile": {"tempfile"},
            "plugin imports win32file": {"win32file"},
            "plugin imports cv2": {"cv2"},
            "plugin IO modules": {
                "os", "pathlib", "io", "shutil", "socket", "urllib", "http",
                "requests", "subprocess", "threading", "multiprocessing",
            },
        }
        for index, (name, extra) in enumerate(fixtures.items()):
            fixture_root = base / f"violation-{index}"
            files = dict(clean_files)
            files.update(extra)
            write_fixture(fixture_root, files)
            report = check(fixture_root)
            violation_items = report["violations"]  # type: ignore[index]
            kinds = {item["kind"] for item in violation_items}
            files = {item["file"] for item in violation_items}
            imported = {item["imported"] for item in violation_items}
            if name == "import cycle":
                detected = bool(report["cycles"])
            else:
                detected = (
                    expected_kinds[name] in kinds
                    and expected_files.get(name, set()) <= files
                    and expected_imports.get(name, set()) <= imported
                )
            cases.append(
                {
                    "name": name,
                    "ok": detected,
                    "expected": "fail with expected violation",
                    "detected": sorted(kinds) if kinds else report["cycles"],
                }
            )

        acl_allowed = base / "legacy-acl-allowed"
        acl_files = dict(clean_files)
        acl_files["src/hslegend/adapters/legacy/allowed.py"] = (
            "import click\n"
            "from src.ocr.stable_reader import StableRecommendationReader\n"
        )
        write_fixture(acl_allowed, acl_files)
        report = check(acl_allowed)
        cases.append(
            {
                "name": "legacy imports allowed only in anti-corruption package",
                "ok": bool(report["ok"]),
                "expected": "pass",
                "detected": report["violations"],
            }
        )

        false_positive = base / "false-positive-shadowed-open"
        false_positive_files = dict(clean_files)
        false_positive_files["src/hslegend/plugins/local_open.py"] = (
            "def open(path):\n"
            "    return path\n"
            "open('fixture')\n"
        )
        write_fixture(false_positive, false_positive_files)
        report = check(false_positive)
        cases.append(
            {
                "name": "module-bound open avoids false positive",
                "ok": bool(report["ok"]),
                "expected": "pass",
                "detected": report["violations"],
            }
        )

        computation = base / "plugin-computation-allowed"
        computation_files = dict(clean_files)
        computation_files["src/hslegend/plugins/compute.py"] = "import math\nimport numpy as np\n"
        write_fixture(computation, computation_files)
        report = check(computation)
        cases.append(
            {
                "name": "plugin computation modules remain allowed",
                "ok": bool(report["ok"]),
                "expected": "pass",
                "detected": report["violations"],
            }
        )
    return {"ok": all(bool(case["ok"]) for case in cases), "mode": "self-test", "cases": cases}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="Repository root (default: repository containing this tool)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--self-test", action="store_true", help="Run built-in compliant and violation fixtures")
    args = parser.parse_args(argv)

    if args.self_test:
        report = run_self_test()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            status = "PASS" if report["ok"] else "FAIL"
            print(f"[architecture:self-test] {status}: {sum(1 for item in report['cases'] if item['ok'])}/{len(report['cases'])} cases")
            for item in report["cases"]:
                print(f"- {item['name']}: {'PASS' if item['ok'] else 'FAIL'}")
        return 0 if report["ok"] else 1

    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    report = check(root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
