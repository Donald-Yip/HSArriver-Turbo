"""卡牌库查询：中文名 → cardId 集合、模糊纠正、多版本合并。

这些用例全部跑合成小卡库（毫秒级），只有最后一个冒烟用例读真实
`cards.json`——它验证的是"真库里确实有阿扎莉娜 / 神秘访客"这类事实，
不联网、不依赖炉石。
"""

import unittest

import card_catalog
from card_catalog import CardCatalog, class_label, normalize, parse_class


def _card(card_id, name, card_class="NEUTRAL", card_type="MINION",
          cost=3, card_set="TEST"):
    return {"id": card_id, "name": name, "cardClass": card_class,
            "type": card_type, "cost": cost, "set": card_set}


def _mini_catalog():
    return CardCatalog([
        _card("JAIL_430", "裂魂者阿扎莉娜", "PRIEST", "MINION", 7,
              "ESCAPEFROM_VIOLET_HOLD"),
        _card("GIL_198", "窃魂者阿扎莉娜", "NEUTRAL", "MINION", 7, "GILNEAS"),
        _card("REV_246", "神秘访客", "PRIEST", "MINION", 2, "REVENDRETH"),
        _card("CORE_REV_246", "神秘访客", "PRIEST", "MINION", 2, "CORE"),
        # 同名但只在冒险模式出现：不该混进"神秘访客"的 id 集合。
        _card("Story_11_UnknownEnemy_012hb", "神秘访客", "PRIEST", "MINION",
              2, "STORY"),
        # 纯酒馆卡：补全时不该出现。
        _card("BG24_Reward_129", "神秘罪碑", "NEUTRAL",
              "BATTLEGROUND_QUEST_REWARD", 0, "BATTLEGROUNDS"),
    ])


class NormalizeTests(unittest.TestCase):
    def test_strips_spaces_and_punctuation_and_width(self):
        self.assertEqual(normalize(" 神秘　访客！ "), "神秘访客")
        self.assertEqual(normalize("Ｍｙｓｔｅｒｙ Visitor"), "mysteryvisitor")
        self.assertEqual(normalize(None), "")


class ParseClassTests(unittest.TestCase):
    def test_chinese_and_enum_and_alias(self):
        self.assertEqual(parse_class("牧师"), "PRIEST")
        self.assertEqual(parse_class("priest"), "PRIEST")
        self.assertEqual(parse_class(" 法师 "), "MAGE")
        self.assertEqual(parse_class("瞎子"), "DEMONHUNTER")

    def test_unknown_returns_none_instead_of_silently_neutral(self):
        self.assertIsNone(parse_class("牧师家"))
        self.assertIsNone(parse_class(""))

    def test_class_label(self):
        self.assertEqual(class_label("PRIEST"), "牧师")
        self.assertEqual(class_label(None), "中立")


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.catalog = _mini_catalog()

    def test_lookup_by_chinese_name(self):
        result = self.catalog.lookup("裂魂者阿扎莉娜")
        self.assertTrue(result.ok)
        self.assertEqual(result.name, "裂魂者阿扎莉娜")
        self.assertEqual(result.card_ids, {"JAIL_430"})

    def test_lookup_by_card_id(self):
        result = self.catalog.lookup("JAIL_430")
        self.assertTrue(result.ok)
        self.assertEqual(result.card_ids, {"JAIL_430"})

    def test_same_name_multiple_versions_merged(self):
        result = self.catalog.lookup("神秘访客")
        self.assertTrue(result.ok)
        # CORE_REV_246 与 REV_246 都要在，冒险同名卡要被剔掉。
        self.assertEqual(result.card_ids, {"REV_246", "CORE_REV_246"})

    def test_wrong_character_is_auto_corrected_with_warning(self):
        # 用户把「阿扎莉娜」打成「阿莉扎娜」：唯一最像，自动纠正并给出提示。
        result = self.catalog.lookup("裂魂者阿莉扎娜")
        self.assertTrue(result.ok)
        self.assertEqual(result.name, "裂魂者阿扎莉娜")
        self.assertIn("已自动纠正", result.warning())

    def test_partial_name_unique_hits(self):
        result = self.catalog.lookup("裂魂者")
        self.assertTrue(result.ok)
        self.assertEqual(result.name, "裂魂者阿扎莉娜")

    def test_ambiguous_partial_name_reports_suggestions(self):
        result = self.catalog.lookup("阿扎莉娜")
        self.assertFalse(result.ok)
        self.assertIn("请写全名", result.error)
        self.assertTrue(result.suggestions)

    def test_missing_card_reports_error_and_suggestions(self):
        result = self.catalog.lookup("完全不存在的卡牌名")
        self.assertFalse(result.ok)
        self.assertIn("找不到卡牌", result.error)

    def test_empty_query(self):
        self.assertFalse(self.catalog.lookup("  ").ok)

    def test_alias_table_is_honoured(self):
        result = self.catalog.lookup("阿莉扎娜", aliases={"阿莉扎娜": "裂魂者阿扎莉娜"})
        self.assertTrue(result.ok)
        self.assertEqual(result.card_ids, {"JAIL_430"})

    def test_card_type_filter(self):
        self.assertTrue(self.catalog.lookup("裂魂者", card_type="MINION").ok)
        self.assertFalse(self.catalog.lookup("裂魂者", card_type="SPELL").ok)

    def test_class_of(self):
        self.assertEqual(self.catalog.class_of("JAIL_430"), "PRIEST")
        self.assertEqual(self.catalog.class_of("NOPE"), "")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.catalog = _mini_catalog()

    def test_prefix_match_returns_card_with_ids(self):
        hits = self.catalog.search("神秘")
        names = [hit.name for hit in hits]
        self.assertIn("神秘访客", names)
        guest = next(hit for hit in hits if hit.name == "神秘访客")
        self.assertEqual(set(guest.ids), {"REV_246", "CORE_REV_246"})
        self.assertEqual(guest.primary_id, "REV_246")

    def test_battleground_only_cards_are_hidden(self):
        names = [hit.name for hit in self.catalog.search("神秘")]
        self.assertNotIn("神秘罪碑", names)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.catalog.search(""), [])
        self.assertEqual(self.catalog.search("   "), [])

    def test_limit(self):
        self.assertLessEqual(len(self.catalog.search("神秘", limit=1)), 1)

    def test_describe_shows_name_class_and_id(self):
        hit = self.catalog.search("裂魂")[0]
        text = hit.describe()
        self.assertIn("裂魂者阿扎莉娜", text)
        self.assertIn("牧师随从", text)
        self.assertIn("JAIL_430", text)


class RealCardsJsonSmokeTests(unittest.TestCase):
    """真库冒烟：确认两个例子卡在真实 cards.json 里能查对（不联网）。"""

    @classmethod
    def setUpClass(cls):
        cls.catalog = card_catalog.default_catalog()

    def setUp(self):
        if self.catalog is None:
            self.skipTest(f"cards.json 不可用：{card_catalog.catalog_error()}")

    def test_alizana_and_guest(self):
        alizana = self.catalog.lookup("裂魂者阿扎莉娜")
        self.assertTrue(alizana.ok)
        self.assertIn("JAIL_430", alizana.card_ids)

        guest = self.catalog.lookup("神秘访客")
        self.assertTrue(guest.ok)
        # 至少要有 REV_246（对战版本）；核心版存在时也要一起进集合。
        self.assertIn("REV_246", guest.card_ids)

    def test_misspelled_alizana_is_corrected(self):
        result = self.catalog.lookup("裂魂者阿莉扎娜")
        self.assertTrue(result.ok)
        self.assertEqual(result.name, "裂魂者阿扎莉娜")


if __name__ == "__main__":
    unittest.main()
