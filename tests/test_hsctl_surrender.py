"""hsctl 的卡组识别投降 / 查卡命令：参数解析与渲染（不连控制台、不联网）。

思路：用假的 Ctx 顶掉 HTTP，只验「命令 → 页面文字 / 退出码」这一段。
"""

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import hsctl


class FakeCtx:
    """只实现命令用到的那几个方法，记录 POST 内容。"""

    def __init__(self, status=None, post_result=None):
        self.status = status or {}
        self.posted = []
        self.base = "http://127.0.0.1:8765"
        self.json = False
        self.post_result = post_result or {"ok": True, "message": "已保存"}

    def get(self, path, params=None):
        return self.status

    def post(self, path, body=None):
        self.posted.append((path, dict(body or {})))
        return self.post_result

    def emit(self, payload, text):
        print(text)


def _run(func, ns, ctx):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = func(ns, ctx)
    return code, buf.getvalue()


def _ns(**kwargs):
    base = {"action": None, "file": None, "rules": None, "yes": False,
            "words": [], "limit": 8, "show": False, "json": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


CFG = {
    "enabled": True,
    "aliases": {},
    "decks": [{
        "id": "priest_alizana",
        "name": "牧师 · 阿扎莉娜",
        "class": "PRIEST",
        "when": {"all": [{"type": "start_of_game",
                          "card": ["裂魂者阿扎莉娜"]},
                         {"type": "played", "card": ["神秘访客"],
                          "within": {"max": 4}}]},
    }],
}


class ShowTests(unittest.TestCase):
    def test_show_renders_condition_in_chinese(self):
        ctx = FakeCtx(status={"auto_surrender": CFG})
        code, out = _run(hsctl.cmd_surrender, _ns(action="show"), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("共 1 套卡组", out)
        self.assertIn("开局生效卡 裂魂者阿扎莉娜", out)
        self.assertIn("对方打出过 神秘访客", out)
        self.assertIn("回合 ≤4", out)
        self.assertNotIn("PRIEST", out)          # 职业要显示中文

    def test_show_without_decks_hints_next_step(self):
        ctx = FakeCtx(status={"auto_surrender": {"enabled": False, "decks": []}})
        code, out = _run(hsctl.cmd_surrender, _ns(action="show"), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("关", out)
        self.assertIn("还没有卡组", out)

    def test_show_json_prints_config(self):
        ctx = FakeCtx(status={"auto_surrender": CFG})
        ns = _ns(action="show")
        ctx.json = True
        code, out = _run(hsctl.cmd_surrender, ns, ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(json.loads(out)["decks"][0]["id"], "priest_alizana")

    def test_broken_card_name_is_reported_not_crashed(self):
        cfg = {"enabled": True, "decks": [{
            "id": "x", "name": "坏的", "class": "PRIEST",
            "when": {"type": "played", "card": ["根本没有这张卡"]}}]}
        ctx = FakeCtx(status={"auto_surrender": cfg})
        code, out = _run(hsctl.cmd_surrender, _ns(action="show"), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("根本没有这张卡", out)


class ToggleTests(unittest.TestCase):
    def test_on_posts_enabled_true(self):
        ctx = FakeCtx(status={"auto_surrender": CFG},
                      post_result={"ok": True, "message": "已开启",
                                   "config": CFG})
        code, out = _run(hsctl.cmd_surrender, _ns(action="on"), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(ctx.posted[0], ("/api/surrender_rules", {"enabled": True}))
        self.assertIn("已开启", out)

    def test_off_posts_enabled_false(self):
        ctx = FakeCtx(post_result={"ok": True, "message": "已关闭"})
        _run(hsctl.cmd_surrender, _ns(action="off"), ctx)
        self.assertEqual(ctx.posted[0][1], {"enabled": False})

    def test_on_without_decks_warns(self):
        ctx = FakeCtx(post_result={"ok": True, "message": "已开启",
                                   "config": {"enabled": True, "decks": []}})
        _, out = _run(hsctl.cmd_surrender, _ns(action="on"), ctx)
        self.assertIn("还没有配置卡组", out)

    def test_clear_sends_empty_deck_list(self):
        ctx = FakeCtx()
        _run(hsctl.cmd_surrender, _ns(action="clear"), ctx)
        self.assertEqual(ctx.posted[0][1],
                         {"config": {"enabled": False, "decks": []}})


class SetTests(unittest.TestCase):
    def test_set_from_inline_rules(self):
        ctx = FakeCtx()
        ns = _ns(action="set", rules=json.dumps(CFG))
        code, out = _run(hsctl.cmd_surrender, ns, ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        body = ctx.posted[0][1]
        self.assertEqual(body["config"]["decks"][0]["id"], "priest_alizana")
        self.assertNotIn("confirm", body)
        self.assertIn("已保存", out)

    def test_set_single_deck_is_wrapped(self):
        ctx = FakeCtx()
        ns = _ns(action="set", rules=json.dumps(CFG["decks"][0]))
        code, _ = _run(hsctl.cmd_surrender, ns, ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        body = ctx.posted[0][1]["config"]
        self.assertTrue(body["enabled"])
        self.assertEqual(len(body["decks"]), 1)

    def test_set_needs_confirm_returns_failure(self):
        ctx = FakeCtx(post_result={"ok": False, "needs_confirm": True,
                                   "error": "当前这一局会立刻投降",
                                   "hit": {"name": "牧师 · 阿扎莉娜"}})
        ns = _ns(action="set", rules=json.dumps(CFG))
        code, out = _run(hsctl.cmd_surrender, ns, ctx)
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("--yes", out)
        self.assertIn("牧师 · 阿扎莉娜", out)

    def test_set_with_yes_passes_confirm(self):
        ctx = FakeCtx()
        ns = _ns(action="set", rules=json.dumps(CFG), yes=True)
        _run(hsctl.cmd_surrender, ns, ctx)
        self.assertTrue(ctx.posted[0][1]["confirm"])

    def test_set_reports_validation_errors(self):
        ctx = FakeCtx(post_result={"ok": False, "error": "卡名有误，未保存",
                                   "errors": [{"path": "decks[0].when.card[0]",
                                               "message": "找不到卡牌「x」"}]})
        ns = _ns(action="set", rules=json.dumps(CFG))
        code, out = _run(hsctl.cmd_surrender, ns, ctx)
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("卡名有误", out)
        self.assertIn("decks[0].when.card[0]", out)

    def test_set_reports_corrections(self):
        ctx = FakeCtx(post_result={"ok": True, "message": "已保存",
                                   "corrected": [{"from": "阿莉扎娜",
                                                  "to": "阿扎莉娜"}]})
        ns = _ns(action="set", rules=json.dumps(CFG))
        _, out = _run(hsctl.cmd_surrender, ns, ctx)
        self.assertIn("已自动纠正", out)

    def test_set_without_payload_is_usage_error(self):
        with self.assertRaises(hsctl.ApiFailure):
            _run(hsctl.cmd_surrender, _ns(action="set"), FakeCtx())

    def test_set_from_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps(CFG), encoding="utf8")
            ctx = FakeCtx()
            code, _ = _run(hsctl.cmd_surrender,
                           _ns(action="set", file=str(path)), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(len(ctx.posted), 1)


class CheckTests(unittest.TestCase):
    def test_check_shows_progress_and_opponent_class(self):
        state = {
            "opponent_class": "PRIEST", "round": 3, "my_turn": False,
            "opponent_played": [{"card_id": "REV_246", "round": 2}],
            "opponent_revealed": [],
            "start_of_game": [{"card_id": "JAIL_430", "round": 0}],
            "facts": {"played": [{"card_id": "REV_246", "round": 2}],
                      "start_of_game": [{"card_id": "JAIL_430"}]},
            "decks": [{"id": "priest_alizana", "name": "牧师 · 阿扎莉娜",
                       "class": "PRIEST", "matched": True, "done": 2, "total": 2,
                       "checks": [{"text": "对方开局生效卡 裂魂者阿扎莉娜",
                                   "ok": True},
                                  {"text": "对方打出过 神秘访客（回合 ≤4）",
                                   "ok": True}]}],
            "triggered": False,
        }
        ctx = FakeCtx(status={"auto_surrender": CFG, "surrenders": state})
        code, out = _run(hsctl.cmd_surrender, _ns(action="check"), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("牧师", out)
        self.assertIn("回合：3", out)
        self.assertIn("✓ 命中", out)
        self.assertIn("对方打出过 神秘访客", out)

    def test_check_renders_tree_when_present(self):
        state = {
            "opponent_class": "MAGE", "round": 4, "my_turn": True,
            "decks": [{"id": "nested", "name": "嵌套卡组", "class": "",
                       "matched": False, "done": 2, "total": 3,
                       "checks": [{"text": "对方打出过 神秘访客", "ok": True},
                                  {"text": "对方打出过 火球术", "ok": True},
                                  {"text": "对方打出过 寒冰箭", "ok": False}],
                       "tree": {"op": "all", "ok": False, "children": [
                           {"op": "leaf", "ok": True, "text": "对方打出过 神秘访客"},
                           {"op": "any", "ok": True, "children": [
                               {"op": "leaf", "ok": True, "text": "对方打出过 火球术"},
                               {"op": "leaf", "ok": False, "text": "对方打出过 寒冰箭"},
                           ]},
                       ]}}],
        }
        ctx = FakeCtx(status={"auto_surrender": CFG, "surrenders": state})
        code, out = _run(hsctl.cmd_surrender, _ns(action="check"), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("且", out)
        self.assertIn("或", out)
        self.assertIn("对方打出过 寒冰箭", out)

    def test_check_when_automation_idle(self):
        ctx = FakeCtx(status={"auto_surrender": CFG,
                              "surrenders": {"decks": [],
                                             "message": "自动化未启动"}})
        code, out = _run(hsctl.cmd_surrender, _ns(action="check"), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("自动化未启动", out)


class CardTests(unittest.TestCase):
    def test_card_search_prints_candidates(self):
        ctx = FakeCtx()
        code, out = _run(hsctl.cmd_card, _ns(words=["神秘访客"]), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("神秘访客", out)

    def test_card_show_describes_card(self):
        ctx = FakeCtx()
        code, out = _run(hsctl.cmd_card,
                         _ns(words=["裂魂者阿莉扎娜"], show=True), ctx)
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("JAIL_430", out)
        self.assertIn("按「裂魂者阿扎莉娜」处理", out)

    def test_card_unknown_prints_hints_and_fails(self):
        ctx = FakeCtx()
        code, out = _run(hsctl.cmd_card, _ns(words=["完全不存在卡"]), ctx)
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("没有匹配", out)

    def test_card_requires_a_name(self):
        with self.assertRaises(hsctl.ApiFailure):
            _run(hsctl.cmd_card, _ns(words=[]), FakeCtx())


class ParserTests(unittest.TestCase):
    def test_parser_routes_surrender_and_card(self):
        parser = hsctl.build_parser()
        ns = parser.parse_args(["surrender", "on"])
        self.assertEqual(ns.action, "on")
        ns = parser.parse_args(["surrender"])
        self.assertIs(ns.handler, hsctl.cmd_surrender)
        ns = parser.parse_args(["surrender", "set", "--file", "x.json", "--yes"])
        self.assertEqual((ns.action, ns.file, ns.yes), ("set", "x.json", True))
        ns = parser.parse_args(["card", "神秘访客", "--limit", "3"])
        self.assertEqual((ns.words, ns.limit, ns.handler),
                         (["神秘访客"], 3, hsctl.cmd_card))
        ns = parser.parse_args(["card", "神秘访客", "--show"])
        self.assertTrue(ns.show)


if __name__ == "__main__":
    unittest.main()
