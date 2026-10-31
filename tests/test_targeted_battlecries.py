import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from manual_controller import ClickExecutor, ManualController
from src.game_state.recommendation_adapter import adapt_action, RecommendationStateError
from src.parser.recommendation_parser import RecommendationParser, RecommendationParseError


class RecordingClicks:
    def __init__(self):
        self.events = []

    def choose_card(self, index, count):
        self.events.append(("hand", index, count))

    def put_minion(self, index, count):
        self.events.append(("place", index, count))

    def choose_my_minion(self, index, count):
        self.events.append(("friendly", index, count))

    def choose_opponent_minion(self, index, count):
        self.events.append(("enemy", index, count))

    def choose_my_hero(self):
        self.events.append(("friendly_hero",))

    def choose_oppo_hero(self):
        self.events.append(("enemy_hero",))

    def cancel_click(self):
        self.events.append(("cancel",))


class TargetedBattlecryTests(unittest.TestCase):
    def state(self):
        return SimpleNamespace(
            game_num_turns_in_play=3, is_my_turn=True,
            my_hand_cards=[SimpleNamespace(card_id="ORDINARY_BATTLECRY",
                cardtype="MINION", entity_id="source", name="战吼随从")],
            my_minions=[SimpleNamespace(entity_id="friendly", zone_pos=2)],
            my_locations=[SimpleNamespace(entity_id="location", zone_pos=1)],
            oppo_minions=[SimpleNamespace(entity_id="enemy", zone_pos=1)],
            oppo_locations=[], my_board_slot_num=2, oppo_board_slot_num=1,
            my_hero=SimpleNamespace(entity_id="friendly-hero"),
            oppo_hero=SimpleNamespace(entity_id="enemy-hero"))

    def proposed(self, target=None, destination=1):
        lines = ["打法参考A", "打出1号位随从", "战吼随从"]
        if target:
            lines.extend([target, "镀银魔像"])
        lines.append(f"放置于我方{destination}号位")
        return RecommendationParser().parse(SimpleNamespace(
            frame_id="frame", normalized_text="\n".join(lines), confidence=.99),
            turn_number=3, log_revision=7)

    def execute(self, adapted, state):
        clicks = RecordingClicks()
        sleeps = []
        controller = ManualController(output_func=lambda _: None,
            executor=ClickExecutor(click_module=clicks, sleep_func=sleeps.append,
                                   action_context=nullcontext))
        result = controller.execute(adapted.manual_action, state)
        return result, clicks.events, sleeps

    def test_all_battlecry_targets_are_bound_and_clicked_after_placement(self):
        cases = [
            ("目标是己方2号位", "friendly", ("friendly", 2, 3)),
            ("目标是我方2号位随从", "friendly", ("friendly", 2, 3)),
            ("目标是敌方1号位随从", "enemy", ("enemy", 0, 1)),
            ("目标是对方1号位", "enemy", ("enemy", 0, 1)),
            ("目标是己方英雄", "friendly-hero", ("friendly_hero",)),
            ("目标是对方英雄", "enemy-hero", ("enemy_hero",)),
        ]
        for line, entity_id, click in cases:
            with self.subTest(line=line):
                state = self.state()
                try:
                    adapted = adapt_action(self.proposed(line), state)
                except (RecommendationParseError, RecommendationStateError) as exc:
                    self.fail(f"battlecry target rejected: {exc}")
                result, events, sleeps = self.execute(adapted, state)
                self.assertTrue(result.executed, result.message)
                self.assertEqual(entity_id, adapted.target_entity_id)
                self.assertEqual([("hand", 0, 1), ("place", 0, 2),
                                  click, ("cancel",)], events)
                self.assertEqual([.4], sleeps)

    def test_friendly_target_before_placement_keeps_index(self):
        state = self.state()
        adapted = adapt_action(self.proposed("目标是己方2号位", 3), state)
        result, events, _ = self.execute(adapted, state)
        self.assertTrue(result.executed, result.message)
        self.assertEqual(("friendly", 1, 3), events[2])

    def test_replaced_target_is_rejected_before_clicking(self):
        state = self.state()
        adapted = adapt_action(self.proposed("目标是己方2号位"), state)
        state.my_minions[0] = SimpleNamespace(entity_id="replacement", zone_pos=2)
        result, events, _ = self.execute(adapted, state)
        self.assertFalse(result.executed)
        self.assertEqual([], events)

    def test_untargeted_battlecry_keeps_original_clicks(self):
        state = self.state()
        result, events, sleeps = self.execute(adapt_action(self.proposed(), state), state)
        self.assertTrue(result.executed, result.message)
        self.assertEqual([("hand", 0, 1), ("place", 0, 2), ("cancel",)], events)
        self.assertEqual([], sleeps)

    def test_multiple_targets_are_rejected(self):
        with self.assertRaisesRegex(RecommendationParseError, "ambiguous_target"):
            self.proposed("目标是己方英雄\n目标是对方英雄")
