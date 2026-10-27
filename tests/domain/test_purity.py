from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


DOMAIN_ROOT = Path(__file__).resolve().parents[2] / "src" / "hslegend" / "domain"
FORBIDDEN_IO_MODULES = {
    "io",
    "os",
    "pathlib",
    "shutil",
    "socket",
    "subprocess",
    "tempfile",
    "threading",
    "multiprocessing",
    "http",
    "urllib",
    "requests",
    "httpx",
    "cv2",
    "PIL",
    "win32api",
    "win32file",
    "win32gui",
}
FORBIDDEN_BUILTINS = {"open", "input", "exec", "eval", "compile", "__import__"}


class DomainPurityTests(unittest.TestCase):
    def test_domain_imports_only_stdlib_or_relative_domain_modules(self) -> None:
        for path in sorted(DOMAIN_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        with self.subTest(file=path.name, imported=alias.name):
                            self.assertIn(root, sys.stdlib_module_names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    assert node.module is not None
                    with self.subTest(file=path.name, imported=node.module):
                        self.assertTrue(
                            node.module == "hslegend.domain"
                            or node.module.startswith("hslegend.domain.")
                            or node.module.split(".", 1)[0] in sys.stdlib_module_names
                        )

    def test_domain_has_no_io_or_dynamic_import_calls(self) -> None:
        for path in sorted(DOMAIN_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported_roots = {
                alias.name.split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported_roots.update(
                (node.module or "").split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            )
            with self.subTest(file=path.name):
                self.assertFalse(imported_roots & FORBIDDEN_IO_MODULES)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    with self.subTest(file=path.name, call=node.func.id):
                        self.assertNotIn(node.func.id, FORBIDDEN_BUILTINS)


if __name__ == "__main__":
    unittest.main()
