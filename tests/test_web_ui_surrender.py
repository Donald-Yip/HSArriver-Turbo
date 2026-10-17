"""卡组识别投降的网页接口：保存校验、热保存、二次确认、查卡接口。

不写真实 ui_config.json（load_config / save_config 全是内存桩），
也不依赖炉石在跑。
"""

import unittest
from unittest.mock import patch

import web_ui

VALID_DECK = {
    "id": "priest_alizana",
    "name": "牧师 · 阿扎莉娜 + 神秘访客",
    "class": "牧师",
    "when": {"all": [
        {"type": "seen", "card": "裂魂者阿扎莉娜"},
        {"type": "played", "card": "神秘访客", "within": {"max": 4}},
    ]},
}


class _Base(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.cfg = {"name": "", "log_root": "", "auto_surrender": {}}
        for name, kwargs in (
            ("load_config", {"side_effect": lambda: dict(self.cfg)}),
            ("save_config", {"side_effect": self._save}),
            ("_log", {}),
            ("_current_log_state", {"return_value": None}),
        ):
            patcher = patch.object(web_ui, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        # preview 默认"不会立刻触发"，需要时由用例覆盖
        patcher = patch.object(web_ui.surrender_rules.engine, "preview",
                               return_value={"ok": True, "would_fire_now": False})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _save(self, cfg):
        self.cfg = dict(cfg)
        self.saved.append(dict(cfg))


class SaveTests(_Base):
    def test_valid_config_is_saved_and_normalized(self):
        result = web_ui.api_save_surrender_rules(
            {"config": {"enabled": True, "decks": [dict(VALID_DECK)]}})
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.cfg["auto_surrender"]["enabled"])
        deck = self.cfg["auto_surrender"]["decks"][0]
        # 中文职业被规范化成日志枚举；side 默认 opponent；空的 min 不落盘
        self.assertEqual(deck["class"], "PRIEST")
        self.assertEqual(deck["when"]["all"][0]["side"], "opponent")
        self.assertEqual(deck["when"]["all"][1]["within"], {"max": 4})

    def test_unknown_leaf_type_is_rejected_and_not_saved(self):
        bad = {"enabled": True, "decks": [{"id": "x", "name": "x",
                                           "when": {"type": "magic"}}]}
        result = web_ui.api_save_surrender_rules({"config": bad})
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["path"], "decks[0].when.type")
        self.assertEqual(self.saved, [])

    def test_unknown_card_is_rejected_and_not_saved(self):
        bad = {"enabled": True, "decks": [{
            "id": "x", "name": "x",
            "when": {"type": "played", "card": "根本没有这张卡"}}]}
        result = web_ui.api_save_surrender_rules({"config": bad})
        self.assertFalse(result["ok"])
        self.assertIn("card", result["errors"][0]["path"])
        self.assertEqual(self.saved, [])

    def test_misspelled_card_is_corrected_with_notice(self):
        ok = {"enabled": True, "decks": [{
            "id": "x", "name": "x",
            "when": {"type": "revealed", "card": "裂魂者阿莉扎娜"}}]}
        result = web_ui.api_save_surrender_rules({"config": ok})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["corrected"][0]["to"], "裂魂者阿扎莉娜")
        # 落盘保留用户原文（card 统一是列表），纠错只在编译期生效
        self.assertEqual(self.cfg["auto_surrender"]["decks"][0]["when"]["card"],
                         ["裂魂者阿莉扎娜"])

    def test_toggle_only_keeps_existing_decks(self):
        self.cfg["auto_surrender"] = {"enabled": False, "aliases": {},
                                      "decks": [dict(VALID_DECK)]}
        result = web_ui.api_save_surrender_rules({"enabled": True})
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(self.cfg["auto_surrender"]["decks"]), 1)

    def test_running_automation_can_still_hot_save(self):
        class _Thread:
            def is_alive(self):
                return True

        saved = web_ui.CTRL.automation_thread
        web_ui.CTRL.automation_thread = _Thread()
        self.addCleanup(setattr, web_ui.CTRL, "automation_thread", saved)
        result = web_ui.api_save_surrender_rules(
            {"config": {"enabled": True, "decks": [dict(VALID_DECK)]}})
        self.assertTrue(result["ok"], result)
        self.assertTrue(self.cfg["auto_surrender"]["enabled"])


class ConfirmTests(_Base):
    def test_would_fire_now_requires_confirmation(self):
        with patch.object(web_ui.surrender_rules.engine, "preview",
                          return_value={"ok": True, "would_fire_now": True,
                                        "hit": {"deck_id": "priest_alizana",
                                                "name": "牧师"}}):
            result = web_ui.api_save_surrender_rules(
                {"config": {"enabled": True, "decks": [dict(VALID_DECK)]}})
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_confirm"])
        self.assertEqual(self.saved, [])

    def test_confirmed_save_goes_through(self):
        with patch.object(web_ui.surrender_rules.engine, "preview",
                          return_value={"ok": True, "would_fire_now": True,
                                        "hit": {"deck_id": "x", "name": "x"}}):
            result = web_ui.api_save_surrender_rules(
                {"config": {"enabled": True, "decks": [dict(VALID_DECK)]},
                 "confirm": True})
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["would_fire_now"])
        self.assertEqual(len(self.saved), 1)


class EmptyConfigTests(_Base):
    def test_empty_config_saves_without_error(self):
        result = web_ui.api_save_surrender_rules({"enabled": False})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.cfg["auto_surrender"]["decks"], [])

    def test_surrender_state_without_automation(self):
        # 别的用例可能留下 CTRL.fsm 桩，这里显式置空才测得到「未启动」分支
        with patch.object(web_ui.CTRL, "fsm", None):
            state = web_ui._surrender_state()
        self.assertEqual(state["decks"], [])
        self.assertIn("未启动", state["message"])


NESTED_DECK = {
    "id": "nested",
    "name": "嵌套 · 法师",
    "class": "法师",
    "when": {"all": [
        {"type": "played", "card": "神秘访客"},
        {"any": [{"type": "played", "card": "火球术"},
                 {"type": "played", "card": "寒冰箭"}]},
    ]},
}


class NestedSaveTests(_Base):
    def test_nested_any_is_not_flattened(self):
        result = web_ui.api_save_surrender_rules(
            {"config": {"enabled": True, "decks": [dict(NESTED_DECK)]}})
        self.assertTrue(result["ok"], result)
        when = self.cfg["auto_surrender"]["decks"][0]["when"]
        self.assertEqual(len(when["all"]), 2)
        self.assertEqual(len(when["all"][1]["any"]), 2)

    def test_saving_normalized_config_again_is_idempotent(self):
        first = web_ui.api_save_surrender_rules(
            {"config": {"enabled": True, "decks": [dict(NESTED_DECK)]}})
        self.assertTrue(first["ok"], first)
        saved = self.cfg["auto_surrender"]
        second = web_ui.api_save_surrender_rules({"config": saved})
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["config"]["decks"][0]["when"],
                         saved["decks"][0]["when"])

    def test_not_wrapping_a_group_round_trips(self):
        deck = {"id": "negate", "name": "非", "class": "",
                "when": {"not": {"any": [
                    {"type": "played", "card": "火球术"},
                    {"type": "played", "card": "寒冰箭"}]}}}
        result = web_ui.api_save_surrender_rules(
            {"config": {"enabled": True, "decks": [deck]}})
        self.assertTrue(result["ok"], result)
        when = self.cfg["auto_surrender"]["decks"][0]["when"]
        self.assertIn("not", when)
        self.assertEqual(len(when["not"]["any"]), 2)


class WebPageTests(unittest.TestCase):
    """前端源码的静态断言：嵌套编辑器与可收起卡组的钩子必须存在。"""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "web" / "index.html"
        cls.html = path.read_text(encoding="utf8")

    def test_page_has_nested_editor_hooks(self):
        for token in ('data-act="add-group"', "data-path", 'data-f="op"',
                      "surrenderCollectNode", "SURRENDER_OPS",
                      "surrenderLeafSupported"):
            self.assertIn(token, self.html, token)

    def test_page_has_collapsible_deck(self):
        for token in ('data-act="toggle-deck"', "hsSurrenderCollapsed",
                      ".sdeck.collapsed", "sdeck-body", "sdeck-summary"):
            self.assertIn(token, self.html, token)

    def test_old_single_level_wording_is_gone(self):
        self.assertNotIn("表单只能表达", self.html)


if __name__ == "__main__":
    unittest.main()
