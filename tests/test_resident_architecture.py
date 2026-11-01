# -*- coding: utf-8 -*-
"""常驻监督器的架构边界（AST 静态断言，**不 import 被测模块**）。

约定（R1 验收契约 + R5 nit10）：
1. ``src/safety/resident_supervisor.py`` 模块级 import 只允许标准库；
2. 全文件不得 import web_ui / FSM_action / manual_controller / click / config
   （配置、状态、动作全部由构造注入，判定层不能反向依赖装配层）；
3. 判定层里除 import 语句外不得出现 ``__import__`` / ``importlib``
   （拦"动态 import 绕过静态检查"这条后门）；
4. 只有 ``web_ui.py`` 可以依赖 ``resident_supervisor``；其他根模块
   （FSM_action.py / log_overlay.py / hsctl.py / main.py …）与
   ``src/hslegend/**`` 的**代码**（注释/文档字符串除外）不得出现该名字；
5. ``src/hslegend/**`` 不得反向 import resident_supervisor / web_ui / FSM_action；
6. 模块级不允许任何函数调用（``open()`` / ``Thread()`` 等副作用），
   线程必须在 ``start()`` 里惰性创建。
"""

from __future__ import annotations

import ast
import io
import sys
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "src" / "safety" / "resident_supervisor.py"
HSLEGEND_ROOT = ROOT / "src" / "hslegend"

STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}

FORBIDDEN_IMPORTS = ("web_ui", "FSM_action", "manual_controller", "click", "config")
FORBIDDEN_FOR_HSLEGEND = ("resident_supervisor", "web_ui", "FSM_action")
DYNAMIC_IMPORT_NAMES = frozenset({"__import__", "importlib"})
# 模块顶层只允许这些语句（其余如 if/try/with/for/call 都算"模块级副作用"）。
ALLOWED_MODULE_STATEMENTS = (
    ast.Import,
    ast.ImportFrom,
    ast.Assign,
    ast.AnnAssign,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Expr,       # 仅模块 docstring，见 _assert_docstring_expr
    ast.Pass,
)

EXPECTED_METHODS = (
    "start", "stop", "resume", "pause", "request_pause", "disable",
    "note_recovered", "poll_once", "state",
)

EXPECTED_INIT_KEYWORDS = (
    "settings", "log_age", "in_game", "process_running", "automation_running",
    "stopped_by", "start_automation", "recover", "alert", "clock", "sleep",
    "hold_reason", "warn",
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_targets(tree: ast.AST) -> list:
    """返回全部 import 目标模块名（相对导入记为 '<relative>'）。"""
    targets: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                targets.append("<relative>")
            if node.module:
                targets.append(node.module)
                targets.extend(f"{node.module}.{alias.name}" for alias in node.names)
            elif node.level:
                targets.extend(alias.name for alias in node.names)
    return targets


def _root(target: str) -> str:
    return target.split(".", 1)[0]


def _docstring_lines(tree: ast.AST) -> set:
    """模块/类/函数 docstring 占用的行号（用于把"文档提到"与"代码依赖"区分开）。"""
    lines: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if not (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            continue
        start = first.lineno
        end = getattr(first, "end_lineno", start) or start
        lines.update(range(start, end + 1))
    return lines


def _code_without_comments_and_docstrings(path: Path) -> str:
    """去掉注释与文档字符串后的源码文本。

    "依赖"要看代码：hsctl.py 的函数 docstring 里写「判定逻辑在
    src/safety/resident_supervisor.py」是文档说明，不是依赖；真正的
    import / 动态引用必须被这个断言拦住。
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            lines[row - 1] = lines[row - 1][:column]
    docstrings = _docstring_lines(_parse(path))
    return "\n".join(line for number, line in enumerate(lines, 1)
                     if number not in docstrings)


class ResidentSupervisorBoundaryTests(unittest.TestCase):

    def setUp(self):
        self.assertTrue(MODULE_PATH.is_file(), f"缺少模块：{MODULE_PATH}")
        self.tree = _parse(MODULE_PATH)

    def _class(self) -> ast.ClassDef:
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "ResidentSupervisor":
                return node
        self.fail("resident_supervisor.py 里没有 class ResidentSupervisor")

    # ------------------------------------------------------------- import 边界
    def test_module_level_imports_are_stdlib_only(self):
        for node in self.tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(_root(alias.name), STDLIB,
                                  f"模块级非标准库 import：{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(0, node.level,
                                 f"模块级不允许相对导入：{node.module}")
                self.assertIn(_root(node.module or ""), STDLIB,
                              f"模块级非标准库 import：{node.module}")

    def test_no_forbidden_imports_anywhere(self):
        for target in _import_targets(self.tree):
            self.assertNotEqual("<relative>", target, "不允许相对导入")
            self.assertNotIn(_root(target), FORBIDDEN_IMPORTS,
                             f"常驻判定层不得 import {target}")

    def test_all_imports_are_stdlib(self):
        # 更强约束：连函数内的延迟导入也不能是项目模块（依赖必须全部注入）。
        for target in _import_targets(self.tree):
            self.assertIn(_root(target), STDLIB,
                          f"常驻判定层出现非标准库依赖：{target}")

    def test_no_dynamic_import_escapes(self):
        # 拦住 `__import__("web_ui")` / `importlib.import_module(...)` 这类
        # 绕过上面静态 import 断言的动态导入。
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name):
                self.assertNotIn(
                    node.id, DYNAMIC_IMPORT_NAMES,
                    f"第 {node.lineno} 行出现动态导入符号 {node.id!r}")
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(
                    node.attr, DYNAMIC_IMPORT_NAMES,
                    f"第 {node.lineno} 行出现动态导入属性 .{node.attr}")
                if isinstance(node.value, ast.Name):
                    self.assertNotIn(
                        node.value.id, DYNAMIC_IMPORT_NAMES,
                        f"第 {node.lineno} 行通过 {node.value.id} 动态导入")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                for name in DYNAMIC_IMPORT_NAMES:
                    self.assertNotIn(
                        name, node.value,
                        f"第 {node.lineno} 行字符串里藏了动态导入名字 {name!r}")

    def test_only_web_ui_may_depend_on_resident_supervisor(self):
        # 装配只允许在 web_ui.py；其余根模块（含 FSM_action/log_overlay/
        # hsctl/main…）与 src/hslegend/** 的代码里不能出现这个依赖名。
        candidates = [path for path in sorted(ROOT.glob("*.py"))
                      if path.name != "web_ui.py"]
        if HSLEGEND_ROOT.is_dir():
            candidates.extend(sorted(HSLEGEND_ROOT.rglob("*.py")))
        self.assertGreater(len(candidates), 5)   # 防止路径写错导致空跑
        for path in candidates:
            code = _code_without_comments_and_docstrings(path)
            self.assertNotIn(
                "resident_supervisor", code,
                f"{path.relative_to(ROOT)} 的代码依赖了 resident_supervisor；"
                f"只允许 web_ui.py 装配它（注释/文档字符串不算）")

    # ------------------------------------------------------------- 类接口契约
    def test_class_declares_the_contract_methods(self):
        klass = self._class()
        methods = {node.name for node in klass.body
                   if isinstance(node, ast.FunctionDef)}
        self.assertTrue(set(EXPECTED_METHODS) <= methods,
                        f"缺少方法：{set(EXPECTED_METHODS) - methods}")

    def test_init_is_keyword_only_with_injected_dependencies(self):
        klass = self._class()
        init = next(node for node in klass.body
                    if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        positional = [arg.arg for arg in init.args.args]
        self.assertEqual(["self"], positional, "__init__ 依赖必须全部关键字注入")
        self.assertEqual([], [arg.arg for arg in init.args.posonlyargs])
        self.assertIsNone(init.args.vararg)
        self.assertIsNone(init.args.kwarg)
        self.assertEqual(list(EXPECTED_INIT_KEYWORDS),
                         [arg.arg for arg in init.args.kwonlyargs])
        # 前 10 个是必须注入的依赖；clock/sleep/hold_reason/warn 允许有默认值。
        defaults = init.args.kw_defaults
        for name, default in zip(EXPECTED_INIT_KEYWORDS, defaults):
            if name in ("clock", "sleep", "hold_reason", "warn"):
                self.assertIsNotNone(default, f"{name} 应该有默认值")
            else:
                self.assertIsNone(default, f"{name} 不允许有默认值（必须注入）")

    # ------------------------------------------------------------- 无副作用
    def test_module_level_has_no_open_or_thread_calls(self):
        for stmt in self.tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Call):
                    continue
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif (isinstance(node.func, ast.Attribute)
                      and isinstance(node.func.value, ast.Name)):
                    name = f"{node.func.value.id}.{node.func.attr}"
                self.assertNotIn(
                    name, {"open", "Thread", "threading.Thread"},
                    f"模块级不允许 {name}()（线程应在 start() 里惰性创建）")

    def test_module_level_has_no_side_effects(self):
        # 比"没有 open/Thread"更强：模块顶层只允许 import / 常量赋值 /
        # 定义，不允许任何函数调用或控制流（import 时零副作用）。
        for stmt in self.tree.body:
            self.assertIsInstance(
                stmt, ALLOWED_MODULE_STATEMENTS,
                f"模块级出现不允许的语句 {type(stmt).__name__}（line "
                f"{getattr(stmt, 'lineno', '?')}）")
            if isinstance(stmt, ast.Expr):
                # Expr 只允许是模块 docstring。
                self.assertTrue(
                    isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str),
                    "模块级 Expr 只能是 docstring")
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                continue
            for node in ast.walk(stmt):
                self.assertNotIsInstance(
                    node, ast.Call,
                    f"模块级不允许函数调用（line {getattr(node, 'lineno', '?')}）")


class HslegendDirectionTests(unittest.TestCase):
    """src/hslegend/** 不能反向依赖常驻/装配层。"""

    def test_hslegend_does_not_import_resident_or_god_modules(self):
        files = sorted(HSLEGEND_ROOT.rglob("*.py")) if HSLEGEND_ROOT.is_dir() else []
        checked = 0
        for path in files:
            checked += 1
            tree = _parse(path)
            for target in _import_targets(tree):
                self.assertNotIn(
                    _root(target), FORBIDDEN_FOR_HSLEGEND,
                    f"{path.relative_to(ROOT)} 反向 import 了 {target}")
        # 包存在时必须真的扫到文件（避免路径写错导致空跑）。
        if HSLEGEND_ROOT.is_dir():
            self.assertGreater(checked, 0)

    def test_hslegend_source_text_has_no_supervisor_dependency(self):
        files = sorted(HSLEGEND_ROOT.rglob("*.py")) if HSLEGEND_ROOT.is_dir() else []
        for path in files:
            code = _code_without_comments_and_docstrings(path)
            self.assertNotIn(
                "resident_supervisor", code,
                f"{path.relative_to(ROOT)} 代码里出现了 resident_supervisor")


if __name__ == "__main__":
    unittest.main()
