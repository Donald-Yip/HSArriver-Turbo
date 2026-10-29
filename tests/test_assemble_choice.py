from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import click as hearthstone_click
from manual_controller import (
    AssembleChoiceAction, ClickExecutor, ManualController,
)
from src.game_state.recommendation_adapter import adapt_action
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import ActionKind


ALIASES = {
    "拼装\n左二\n中一": 2,
    "拼装\n左一\n中二": 1,
    "拼装\n右二\n中一": 4,
    "拼装\n右一\n中二": 3,
}


def parse(text):
    return RecommendationParser().parse(
        SimpleNamespace(frame_id="frame", normalized_text=text, confidence=.99),
        turn_number=3, log_revision=7)


def state():
    return SimpleNamespace(
        game_num_turns_in_play=3, is_my_turn=True,
        discover_choice_count=4, discover_choice_pending=False,
        general_choice_cards={}, general_choice_entity_ids={},
        my_hand_cards=[], my_minions=[], my_locations=[],
        oppo_minions=[], oppo_locations=[])


class AssembleChoiceTests(unittest.TestCase):
    def test_aliases_map_to_existing_four_choice_indices(self):
        for text, expected in ALIASES.items():
            with self.subTest(text=text):
                proposed = parse(text)
                self.assertIs(ActionKind.ASSEMBLE_CHOICE, proposed.action)
                self.assertEqual(expected, proposed.source.index)

    def test_compact_panel_phrase_is_detected(self):
        self.assertEqual(2, parse("拼装出现左二中一").source.index)
        self.assertEqual(3, parse("拼装右一中二").source.index)

    def test_live_panel_phrase_with_arabic_digits_is_detected(self):
        self.assertEqual(2, parse("进行拼装【左2中1】").source.index)

    def test_live_panel_numeric_aliases_map_to_existing_choices(self):
        cases = {
            "进行拼装【左2中1】": 2,
            "进行拼装【左1中2】": 1,
            "进行拼装【右2中1】": 4,
            "进行拼装【右1中2】": 3,
            "进行拼装【左２中１】": 2,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, parse(text).source.index)

    def test_live_panel_countdown_does_not_change_stability_semantics(self):
        first = RecommendationParser.normalize_action_text(
            "选择一项替换\n进行拼装【左2中1】\n土石幼龙\n00:35")
        second = RecommendationParser.normalize_action_text(
            "选择一项替换\n进行拼装【左2中1】\n土石幼龙\n00:34")

        self.assertEqual("拼装\n左二\n中一", first)
        self.assertEqual(first, second)

    def test_adapter_uses_the_dedicated_assembly_gesture(self):
        adapted = adapt_action(parse("拼装\n右一\n中二"), state())
        self.assertIsInstance(adapted.manual_action, AssembleChoiceAction)
        self.assertEqual(2, adapted.manual_action.choice_index)

        class Clicks:
            def __init__(self): self.events = []
            def choose_assemble_card(self, index):
                self.events.append(index)

        clicks = Clicks()
        controller = ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(click_module=clicks,
                                   action_context=nullcontext,
                                   sleep_func=lambda _seconds: None),
        )
        result = controller.execute(adapted.manual_action, state())
        self.assertTrue(result.executed, result.message)
        self.assertEqual([2], clicks.events)

    def test_assembly_gesture_clicks_the_four_green_box_centers(self):
        expected = ((514, 481), (514, 595), (1329, 481), (1329, 595))
        clicked = []
        with (
            patch.object(hearthstone_click, "rand_sleep"),
            patch.object(hearthstone_click, "left_click",
                         side_effect=lambda x, y: clicked.append((x, y))),
        ):
            for index in range(4):
                hearthstone_click.choose_assemble_card(index)

        self.assertEqual(list(expected), clicked)

    def test_invalid_assembly_phrases_fail_before_clicking(self):
        from src.parser.recommendation_parser import RecommendationParseError
        for text in ("拼装\n左一\n中一", "拼装\n左二", "拼装\n中一"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(RecommendationParseError,
                                            "assemble_choice_unknown"):
                    parse(text)


if __name__ == "__main__":
    unittest.main()
