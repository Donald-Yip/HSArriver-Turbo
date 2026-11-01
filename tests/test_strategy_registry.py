# -*- coding: utf-8 -*-
"""R18 卡组策略注册表：ai/ 目录自动发现、网页选项与加载失败隔离。

覆盖 ``ai/deck_strategy.py`` 的对外接口：

* ``strategy_folders()`` 返回 ai/ 下的卡组（按名称排序，不含 outofdate；含「通用」）；
* ``strategy_labels()`` / ``strategy_class()`` / ``deck_strategy()`` 的约定；
* ``config.strategy_choices()`` 内置「盒子AI」+ 本地卡组，默认走盒子；
* 某个卡组文件夹加载失败时只跳过该卡组，不把异常抛给主流程。
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import ai
import ai.deck_strategy as deck_strategy_module
import config


EXPECTED_FOLDERS = ["任务牧", "动物园术", "强化学习动物园术", "通用"]
EXCLUDED_FOLDER = "outofdate"


class StrategyRegistryTests(unittest.TestCase):
    """注册表 + 配置选项（只读真实 ai/ 目录）。"""

    def test_registry_lists_and_resolves_the_decks(self):
        folders = deck_strategy_module.strategy_folders(refresh=True)

        self.assertEqual(EXPECTED_FOLDERS, folders)
        self.assertEqual(sorted(folders), folders)
        self.assertNotIn(EXCLUDED_FOLDER, folders)
        self.assertEqual({name: name for name in EXPECTED_FOLDERS},
                         deck_strategy_module.strategy_labels())

        for key in EXPECTED_FOLDERS:
            with self.subTest(deck=key):
                self.assertIn(key, folders)
                strategy_class = deck_strategy_module.strategy_class(key)
                self.assertIsNotNone(strategy_class)
                self.assertTrue(callable(strategy_class.rankState))

    def test_box_and_unknown_keys_have_no_deck_strategy(self):
        # 盒子AI / 不存在的卡组都不是本地策略，调用方据此退回盒子路径。
        self.assertIsNone(deck_strategy_module.deck_strategy("hsbox"))
        self.assertIsNone(deck_strategy_module.deck_strategy("不存在的卡组"))
        self.assertIsNone(deck_strategy_module.strategy_class("hsbox"))
        self.assertIsNone(deck_strategy_module.strategy_class("不存在的卡组"))
        # 缺省参数读网页配置；仓库默认仍是盒子AI。
        self.assertIsNone(deck_strategy_module.deck_strategy())
        self.assertIsNone(deck_strategy_module.strategy_class())

    def test_config_choices_include_box_and_decks_and_default_to_box(self):
        choices = config.strategy_choices()

        self.assertIn(config.STRATEGY_BOX, choices)
        self.assertEqual("盒子AI", choices[config.STRATEGY_BOX])
        for key in EXPECTED_FOLDERS:
            with self.subTest(deck=key):
                self.assertIn(key, choices)
                self.assertEqual(key, choices[key])
        self.assertNotIn(EXCLUDED_FOLDER, choices)

        # 仓库默认策略 key 是 hsbox（ui_config.json 没写 strategy 时也回退到它），
        # 现有盒子路径 / 既有测试因此完全不受本地策略影响。
        self.assertEqual(config.STRATEGY_BOX, config.strategy_setting())
        self.assertIs(True, config.uses_box_ai())


class _DeckCacheGuard(unittest.TestCase):
    """保存/还原 ai.deck_strategy 的模块级缓存，隔离篡改 AI_DIR 的测试。"""

    def setUp(self):
        self._saved_ai_dir = deck_strategy_module.AI_DIR
        self._saved_classes = dict(deck_strategy_module._CLASSES)
        self._saved_failures = dict(deck_strategy_module._FAILURES)
        self._saved_scan_cache = dict(deck_strategy_module._scan_cache)
        self._saved_instances = dict(deck_strategy_module._INSTANCES)
        self._temp_dir = tempfile.TemporaryDirectory()
        self._fake_ai_dir = Path(self._temp_dir.name)
        self._write_deck("good", _GOOD_DECK_SOURCE)
        self._write_deck("broken", _BROKEN_DECK_SOURCE)

        deck_strategy_module.AI_DIR = self._fake_ai_dir
        self._clear_caches()

    def tearDown(self):
        self._clear_caches()
        self._forget_fake_packages()
        deck_strategy_module.AI_DIR = self._saved_ai_dir
        deck_strategy_module._CLASSES.update(self._saved_classes)
        deck_strategy_module._FAILURES.update(self._saved_failures)
        deck_strategy_module._scan_cache.update(self._saved_scan_cache)
        deck_strategy_module._INSTANCES.update(self._saved_instances)
        self._temp_dir.cleanup()

    def _write_deck(self, name, source):
        folder = self._fake_ai_dir / name
        folder.mkdir()
        (folder / f"{name}.py").write_text(source, encoding="utf-8")

    @staticmethod
    def _clear_caches():
        deck_strategy_module._CLASSES.clear()
        deck_strategy_module._FAILURES.clear()
        deck_strategy_module._scan_cache.clear()
        deck_strategy_module._scan_cache["at"] = 0.0
        deck_strategy_module._scan_cache["folders"] = []
        deck_strategy_module._INSTANCES.clear()

    @staticmethod
    def _forget_fake_packages():
        # _ensure_package 会把 ai.good / ai.broken 注册成包并挂在 ai 上。
        for name in ("ai.good", "ai.broken"):
            sys.modules.pop(name, None)
            for module_name in [key for key in sys.modules
                                if key.startswith(name + ".")]:
                sys.modules.pop(module_name, None)
        for attribute in ("good", "broken"):
            if hasattr(ai, attribute):
                delattr(ai, attribute)


_GOOD_DECK_SOURCE = """\
class Good:
    @staticmethod
    def rankState(state, actions):
        return 1.0
"""

_BROKEN_DECK_SOURCE = """\
raise ImportError("boom: broken deck cannot load")
"""


class StrategyFailureIsolationTests(_DeckCacheGuard):
    """一个坏卡组文件夹不能拖垮整张下拉框/整局自动化。"""

    def test_broken_deck_is_skipped_once_without_raising(self):
        output = io.StringIO()
        with redirect_stdout(output):
            folders = deck_strategy_module.strategy_folders(refresh=True)
            for _ in range(3):
                self.assertIsNone(deck_strategy_module.deck_strategy("broken"))
            self.assertIsNone(deck_strategy_module.strategy_class("broken"))
            good_strategy = deck_strategy_module.deck_strategy("good")

        self.assertEqual(["broken", "good"], folders)
        self.assertIsNotNone(good_strategy)
        self.assertTrue(callable(good_strategy.rankState))
        # 失败只打印一次提示，异常不出模块边界。
        log = output.getvalue()
        self.assertEqual(1, log.count("加载「broken」失败"))
        self.assertIn("boom: broken deck cannot load", log)


if __name__ == "__main__":
    unittest.main()
