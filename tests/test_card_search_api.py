"""网页查卡接口 ``GET /api/card_search``：中文补全、多版本合并、不联网。

只用本地 ``cards.json``（真实文件），不打网络请求。
"""

import unittest
from unittest.mock import patch

import web_ui


def _ids(cards):
    return [card_id for card in cards for card_id in card["ids"]]


class SearchTests(unittest.TestCase):
    def test_substring_hits_and_merges_versions(self):
        result = web_ui.api_card_search("神秘访客")
        self.assertTrue(result["ok"], result)
        names = [card["name"] for card in result["cards"]]
        self.assertIn("神秘访客", names)
        hit = next(card for card in result["cards"] if card["name"] == "神秘访客")
        self.assertIn("REV_246", hit["ids"])
        self.assertIn("CORE_REV_246", hit["ids"])
        # 同名多版本只占一条，不重复展示
        self.assertEqual(len(result["cards"]), len({c["name"] for c in result["cards"]}))

    def test_typo_typing_mid_word_still_suggests(self):
        result = web_ui.api_card_search("裂魂者阿莉")
        self.assertTrue(result["ok"], result)
        self.assertIn("裂魂者阿扎莉娜", [card["name"] for card in result["cards"]])

    def test_adventure_only_duplicate_is_filtered(self):
        result = web_ui.api_card_search("神秘访客")
        self.assertNotIn("Story_11_UnknownEnemy_012hb", _ids(result["cards"]))

    def test_battlegrounds_cards_are_not_suggested(self):
        result = web_ui.api_card_search("神秘")
        self.assertTrue(result["ok"], result)
        for card_id in _ids(result["cards"]):
            self.assertFalse(card_id.startswith("BG"), card_id)
        for card in result["cards"]:
            self.assertNotIn("罪碑", card["name"])

    def test_empty_query_returns_empty_list(self):
        for text in ("", "   ", None):
            result = web_ui.api_card_search(text)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["cards"], [])

    def test_limit_is_applied_and_clamped(self):
        wide = web_ui.api_card_search("龙", limit=5)
        self.assertLessEqual(len(wide["cards"]), 5)
        huge = web_ui.api_card_search("龙", limit=999)
        self.assertLessEqual(len(huge["cards"]), 20)
        broken = web_ui.api_card_search("龙", limit="abc")
        self.assertTrue(broken["ok"], broken)
        self.assertLessEqual(len(broken["cards"]), 8)

    def test_no_match_returns_empty_not_error(self):
        result = web_ui.api_card_search("根本没有这张卡")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["cards"], [])

    def test_lookup_by_card_id_works(self):
        result = web_ui.api_card_search("JAIL_430")
        self.assertTrue(result["ok"], result)
        self.assertIn("裂魂者阿扎莉娜", [card["name"] for card in result["cards"]])

    def test_fields_are_ready_for_ui(self):
        result = web_ui.api_card_search("裂魂者阿扎莉娜")
        hit = result["cards"][0]
        for key in ("id", "ids", "name", "class", "class_zh", "type",
                    "type_zh", "cost"):
            self.assertIn(key, hit)
        self.assertEqual(hit["class_zh"], "牧师")
        self.assertEqual(hit["type_zh"], "随从")

    def test_broken_catalog_reports_error_without_crash(self):
        with patch.object(web_ui.card_catalog, "default_catalog", return_value=None), \
                patch.object(web_ui.card_catalog, "catalog_error",
                             return_value="找不到卡牌库 cards.json"):
            result = web_ui.api_card_search("神秘访客")
        self.assertFalse(result["ok"])
        self.assertIn("cards.json", result["error"])

    def test_never_touches_network(self):
        import json_op
        with patch.object(json_op, "read_json",
                          side_effect=AssertionError("查卡不该联网")):
            result = web_ui.api_card_search("神秘访客")
        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
