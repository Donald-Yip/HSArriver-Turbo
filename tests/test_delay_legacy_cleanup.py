# -*- coding: utf-8 -*-
"""旧版「固定回合开始延时」的清理回归（2026-11-01 用户反馈）。

背景：R18 起「我方回合就绪」改判右侧「结束回合」按钮的 OCR 出现
（`RecommendationConfig.turn_start_probe_interval_seconds`），固定的
「每张开局卡延时 / 每回合前延时」运行期**不再读取**。但网页/CLI/文档里
还留着这两个开关（用户本机 ui_config.json 里是 3.5 / 7.0），用户调了不生效、
还以为是 7s 延时在起作用 —— 这就是要修的逻辑冲突。

这里锁住四件事，防止以后又把死开关加回来：
  * 两个废弃字段不在任何用户可见白名单里（config / web_ui / hsctl），
    且各处的「生效时机」表互相一致；
  * 网页不再渲染这两个输入框；
  * 旧 ui_config.json 里残留的值被**忽略**（仍是代码默认值，不报错）；
  * 保存延时配置时把残留字段从 delays 段清掉。
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import hsctl
import web_ui
from config import (
    _DEPRECATED_DELAY_KEYS,
    _USER_DELAY_KEYS,
    RecommendationConfig,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
# 受限环境里 %TEMP% / 新建子目录可能不可写；沿用仓库里既有的 .tmp-tests 目录
# （直接写文件，不建子目录），与其它测试保持同一口径。
TMP_DIR = REPO_ROOT / ".tmp-tests"
LEGACY_KEYS = (
    "first_turn_per_card_delay_seconds",
    "pre_action_delay_seconds",
)


class _RootStub:
    """让 ``config.ROOT / "ui_config.json"`` 指向本次测试的临时文件。

    config._user_box / _user_delays 按 ``ROOT / "ui_config.json"`` 读盘，生产里
    ROOT 就是仓库根。测试不能碰真实 ui_config.json，所以只替换 "/" 这一下。
    """

    def __init__(self, config_path):
        self._config_path = Path(config_path)

    def __truediv__(self, name):
        if name == "ui_config.json":
            return self._config_path
        return Path(name)


class LegacyDelayKeySurfaceTests(unittest.TestCase):
    """废弃字段不能再出现在用户能改到的地方。"""

    def test_deprecated_keys_are_registered_but_not_exposed(self):
        self.assertEqual(LEGACY_KEYS, _DEPRECATED_DELAY_KEYS)
        for key in LEGACY_KEYS:
            self.assertNotIn(key, _USER_DELAY_KEYS)
            self.assertNotIn(key, web_ui._DELAY_BOUNDS)
            self.assertNotIn(key, hsctl.DELAY_KEYS)
            self.assertNotIn(key, hsctl.DELAY_EFFECT)

    def test_web_and_cli_delay_whitelists_match_the_config(self):
        expected = set(_USER_DELAY_KEYS)
        self.assertEqual(expected, set(web_ui._DELAY_BOUNDS))
        self.assertEqual(expected, set(hsctl.DELAY_KEYS))
        self.assertEqual(expected, set(hsctl.DELAY_EFFECT))

    def test_effect_scope_tables_do_not_drift(self):
        # hsctl 的本地提示与 web_ui 的生效时机必须一致（两边注释都这么要求）。
        for key in _USER_DELAY_KEYS:
            self.assertEqual(
                hsctl.DELAY_EFFECT[key], web_ui._delay_effect_scope(key),
                f"{key} 的生效时机在 hsctl / web_ui 里不一致")

    def test_web_page_no_longer_renders_the_two_fields(self):
        html = (REPO_ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for marker in ("inpFirstTurnPerCard", "inpPreDelay",
                       "每张开局卡延时（秒）", "每回合前延时（秒）",
                       "first_turn_per_card_delay_seconds",
                       "pre_action_delay_seconds"):
            # 用 assertFalse 而不是 assertNotIn：后者失败时会把整页 HTML 打出来。
            self.assertFalse(marker in html, f"网页仍在渲染废弃延时：{marker}")
        # 用「结束回合」OCR 就绪代替固定延时的说明要留在页面上。
        self.assertTrue("结束回合" in html)
        self.assertTrue("回合开始不再有固定延时" in html)


class LegacyConfigCompatibilityTests(unittest.TestCase):
    """旧配置/旧调用不能报错，但值不生效。"""

    def test_legacy_ui_config_values_are_ignored(self):
        TMP_DIR.mkdir(exist_ok=True)
        path = TMP_DIR / f"ui_config_legacy_{os.getpid()}.json"
        path.write_text(
            json.dumps({
                "delays": {
                    "first_turn_per_card_delay_seconds": 3.5,
                    "pre_action_delay_seconds": 7.0,
                    "mulligan_retry_delay_seconds": 2.5,
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        self.addCleanup(path.unlink, missing_ok=True)
        # _user_delays 按 config.ROOT / "ui_config.json" 读盘（与生产同口径）。
        with patch.object(config, "ROOT", _RootStub(path)):
            settings = RecommendationConfig()
        # 废弃字段保持代码默认（0.0），不因旧配置“复活”。
        self.assertEqual(0.0, settings.first_turn_per_card_delay_seconds)
        self.assertEqual(0.0, settings.pre_action_delay_seconds)
        # 同段里有效的延时照常读盘。
        self.assertEqual(2.5, settings.mulligan_retry_delay_seconds)

    def test_legacy_kwargs_are_still_accepted_but_have_no_effect(self):
        # 老调用方（旧测试/旧脚本）传这两个关键字不能抛 TypeError；值只是被
        # 存进字段，运行期没人读 —— 真正的“不生效”由上面的读盘白名单保证。
        settings = RecommendationConfig(
            first_turn_per_card_delay_seconds=3.5,
            pre_action_delay_seconds=7.0)
        self.assertEqual(3.5, settings.first_turn_per_card_delay_seconds)
        self.assertEqual(7.0, settings.pre_action_delay_seconds)


class SaveDelaysPrunesLegacyKeysTests(unittest.TestCase):
    """保存延时配置时顺手清掉旧字段，网页/CLI 不再看到它们。"""

    def setUp(self):
        self.saved = {}
        self.cfg = {
            "delays": {
                "first_turn_per_card_delay_seconds": 3.5,
                "pre_action_delay_seconds": 7.0,
                "mulligan_retry_delay_seconds": 5.0,
            },
        }
        self.thread = web_ui.CTRL.automation_thread
        web_ui.CTRL.automation_thread = None
        self.addCleanup(setattr, web_ui.CTRL, "automation_thread", self.thread)
        patches = (
            patch.object(web_ui, "load_config", side_effect=lambda: dict(self.cfg)),
            patch.object(web_ui, "save_config", side_effect=self._save),
            patch.object(web_ui, "_log"),
            patch.object(web_ui, "_current_delays", return_value={}),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _save(self, cfg):
        self.saved = cfg

    def test_saving_any_delay_prunes_the_legacy_entries(self):
        result = web_ui.api_save_delays({"mulligan_retry_delay_seconds": 4.0})
        self.assertTrue(result["ok"])
        delays = self.saved["delays"]
        for key in LEGACY_KEYS:
            self.assertNotIn(key, delays)
        self.assertEqual(4.0, delays["mulligan_retry_delay_seconds"])

    def test_posting_a_legacy_key_is_ignored_not_stored(self):
        result = web_ui.api_save_delays({"pre_action_delay_seconds": 7.0})
        self.assertTrue(result["ok"])
        self.assertNotIn("effects", result)
        self.assertNotIn("pre_action_delay_seconds", self.saved["delays"])


if __name__ == "__main__":
    unittest.main()
