import unittest
from types import SimpleNamespace

from src.parser.recommendation_parser import (
    RecommendationParseError,
    RecommendationParser,
)
from src.recommendation_models import ActionKind, SlotRef


class HandDeckActionParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = RecommendationParser()

    def _parse(self, instruction):
        ocr = SimpleNamespace(
            frame_id="frame-1",
            normalized_text=instruction,
            confidence=0.99,
        )
        try:
            return self.parser.parse(ocr, turn_number=3, log_revision=7)
        except RecommendationParseError as exc:
            self.fail(f"{instruction!r} should be accepted: {exc}")

    def test_forge_and_prepare_both_map_to_trade_card(self):
        for instruction, slot in (("锻造3号位卡牌", 3),
                                  ("预备2号位卡牌", 2)):
            with self.subTest(instruction=instruction):
                proposed = self._parse(instruction)

                self.assertEqual(ActionKind.TRADE_CARD, proposed.action)
                self.assertEqual(SlotRef("hand_slot", "friendly", slot),
                                 proposed.source)
                self.assertEqual(instruction, proposed.normalized_instruction)


if __name__ == "__main__":
    unittest.main()
