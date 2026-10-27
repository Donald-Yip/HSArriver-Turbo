from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


APPLICATION_ROOT = Path(__file__).resolve().parents[2] / "src" / "hslegend" / "application"
FORBIDDEN_IO_MODULES = {
    "_thread",
    "asyncio",
    "concurrent",
    "http",
    "io",
    "multiprocessing",
    "os",
    "pathlib",
    "queue",
    "requests",
    "select",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
    "urllib",
    "win32api",
    "win32file",
    "win32gui",
}
FORBIDDEN_BUILTINS = {"open", "input", "exec", "eval", "compile", "__import__"}
FORBIDDEN_THREAD_FACTORIES = {
    "Thread",
    "Timer",
    "_start_new_thread",
    "ThreadPoolExecutor",
    "ProcessPoolExecutor",
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(APPLICATION_ROOT.parent.parent)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_name(path: Path) -> str:
    module_name = _module_name(path)
    if path.name == "__init__.py":
        return module_name
    return module_name.rpartition(".")[0]


def _resolve_relative(path: Path, level: int, imported: str | None) -> str:
    parts = _package_name(path).split(".")
    ascend = level - 1
    if ascend:
        parts = parts[:-ascend]
    if imported:
        parts.extend(imported.split("."))
    return ".".join(parts)


class ApplicationPurityTests(unittest.TestCase):
    def test_application_imports_only_stdlib_domain_or_application(self) -> None:
        for path in sorted(APPLICATION_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                targets: list[str] = []
                if isinstance(node, ast.Import):
                    targets.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        targets.append(_resolve_relative(path, node.level, node.module))
                    elif node.module:
                        targets.append(node.module)
                for target in targets:
                    with self.subTest(file=path.name, imported=target):
                        root = target.split(".", 1)[0]
                        self.assertTrue(
                            root in sys.stdlib_module_names
                            or target == "hslegend.domain"
                            or target.startswith("hslegend.domain.")
                            or target == "hslegend.application"
                            or target.startswith("hslegend.application."),
                            target,
                        )

    def test_application_has_no_io_or_thread_creation(self) -> None:
        for path in sorted(APPLICATION_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported_roots: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported_roots.add(node.module.split(".", 1)[0])
                    if node.module == "threading":
                        for alias in node.names:
                            with self.subTest(file=path.name, imported=alias.name):
                                self.assertNotIn(alias.name, FORBIDDEN_THREAD_FACTORIES)
            with self.subTest(file=path.name):
                self.assertFalse(imported_roots & FORBIDDEN_IO_MODULES)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    with self.subTest(file=path.name, call=node.func.id):
                        self.assertNotIn(node.func.id, FORBIDDEN_BUILTINS)
                        self.assertNotIn(node.func.id, FORBIDDEN_THREAD_FACTORIES)
                elif isinstance(node.func, ast.Attribute):
                    with self.subTest(file=path.name, call=node.func.attr):
                        self.assertNotIn(node.func.attr, FORBIDDEN_THREAD_FACTORIES)


if __name__ == "__main__":
    unittest.main()
