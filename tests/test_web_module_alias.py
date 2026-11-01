# -*- coding: utf-8 -*-
"""``python web_ui.py`` 启动时的模块别名：全进程只能有一份 web_ui。

背景（R5 复审发现的生产级问题）：控制台是以 ``python web_ui.py`` 启动的，模块在
``sys.modules["__main__"]`` 而不是 ``"web_ui"``。后果：FSM 里
``sys.modules.get("web_ui")`` 恒为 None → 「守护」的进程消失让位/存活检测交接在
生产环境永远失效；其它模块按名字 ``import web_ui`` 也会**再执行一遍** web_ui.py，
造出第二份 CTRL/RESIDENT（各自读到不同的自动化线程/守护状态）。

修法：web_ui 模块体最前面调用 ``_ensure_module_alias()``，``python web_ui.py``
启动时把 ``web_ui`` 指向同一个模块对象；正常 import 时是 no-op。
"""

from __future__ import annotations

import sys
import unittest

import web_ui


class ModuleAliasTests(unittest.TestCase):
    def test_regular_import_is_noop(self):
        # 被 import 时 __name__ == "web_ui"，函数不应改动现有映射。
        self.assertIs(sys.modules.get("web_ui"), web_ui)
        web_ui._ensure_module_alias()
        self.assertIs(sys.modules.get("web_ui"), web_ui)

    def test_main_entry_registers_web_ui_alias(self):
        saved_main = sys.modules.get("__main__")
        saved_web = sys.modules.get("web_ui")
        saved_name = web_ui.__name__
        try:
            # 模拟 `python web_ui.py`：模块住在 __main__，且 "web_ui" 还没登记。
            web_ui.__name__ = "__main__"
            sys.modules["__main__"] = web_ui
            sys.modules.pop("web_ui", None)

            web_ui._ensure_module_alias()

            self.assertIs(sys.modules.get("web_ui"), web_ui,
                          "别名必须指向同一个模块对象，否则会造出第二份状态")
        finally:
            web_ui.__name__ = saved_name
            if saved_web is not None:
                sys.modules["web_ui"] = saved_web
            else:
                sys.modules.pop("web_ui", None)
            if saved_main is not None:
                sys.modules["__main__"] = saved_main
            else:
                sys.modules.pop("__main__", None)

    def test_alias_never_overrides_an_existing_module(self):
        # 已经有人（例如测试 harness）登记过 web_ui 时，保持原对象，不偷换。
        saved_main = sys.modules.get("__main__")
        saved_web = sys.modules.get("web_ui")
        saved_name = web_ui.__name__
        try:
            web_ui.__name__ = "__main__"
            sys.modules["__main__"] = web_ui
            existing = object()
            sys.modules["web_ui"] = existing

            web_ui._ensure_module_alias()

            self.assertIs(sys.modules.get("web_ui"), existing)
        finally:
            web_ui.__name__ = saved_name
            if saved_web is not None:
                sys.modules["web_ui"] = saved_web
            else:
                sys.modules.pop("web_ui", None)
            if saved_main is not None:
                sys.modules["__main__"] = saved_main
            else:
                sys.modules.pop("__main__", None)


if __name__ == "__main__":
    unittest.main()
