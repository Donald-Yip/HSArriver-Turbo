import unittest
from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import patch

import click as hearthstone_click
from log_op import parse_line
from log_state import LogState, update_state
from manual_controller import ManualController, ClickExecutor
from src.parser.recommendation_parser import RecommendationParser
from src.game_state.recommendation_adapter import adapt_action
from strategy import StrategyState


class RestoredTimelineTests(unittest.TestCase):
    def snapshot(self, ids):
        log = LogState()
        log.my_player_id = '1'
        def feed(text):
            parsed = parse_line('D 21:29:00.0000000 ' + text)
            self.assertIsNotNone(parsed)
            update_state(log, parsed)
        feed('GameState.DebugPrintEntityChoices() - id=8 Player=1 ChoiceType=GENERAL')
        for index, card in enumerate(ids):
            feed('GameState.DebugPrintEntityChoices() - '
                 f'Entities[{index}]=[entityName=选项 id={100+index} '
                 f'zone=SETASIDE zonePos=0 cardId={card} player=1]')
        feed('ChoiceCardMgr.WaitThenShowChoices() - id=8 BEGIN')
        state = StrategyState(NS(is_end=False, is_my_turn=True, game_num_turns_in_play=3,
            my_entity=NS(query_tag=lambda _: 0), entity_dict={}, my_player_id='1',
            power_options={}, hand_entry_count=0,
            discover_choice_count=log.discover_choice_count,
            general_choice_cards=log.general_choice_cards))
        feed('GameState.SendChoices() - id=8 ChoiceType=GENERAL')
        self.assertEqual({}, log.general_choice_cards)
        self.assertEqual(2, len(state.general_choice_cards))
        return state

    def proposed(self, text):
        return RecommendationParser().parse(NS(frame_id='f', normalized_text=text,
                                              confidence=.99), 3, 1)

    def test_number_only_and_named_recommendations_use_timeline_buttons(self):
        for ids in [('TIME_000ta', 'TIME_000tb'), ('TIME_000tb', 'TIME_000ta')]:
            state = self.snapshot(ids)
            for card, name in [('TIME_000ta', '维持时间线'), ('TIME_000tb', '回溯时间线')]:
                slot = ids.index(card) + 1
                for text in [f'选择我方{slot}号位卡牌', name,
                             f'选择我方{slot}号位卡牌\n{name}']:
                    with self.subTest(ids=ids, text=text):
                        adapted = adapt_action(self.proposed(text), state)
                        self.assertEqual(card, adapted.manual_action.timeline_card_id)
                        self.assertEqual('choice_resolved', adapted.postcondition)
                        executor = NS(choose_timeline=lambda chosen: self.assertEqual(card, chosen))
                        result = ManualController(output_func=lambda _: None,
                            executor=executor).execute(adapted.manual_action, state)
                        self.assertTrue(result.executed)

    def test_regular_discover_keeps_regular_click_path(self):
        state = self.snapshot(('OTHER_A', 'OTHER_B'))
        action = adapt_action(self.proposed('选择我方2号位卡牌'), state).manual_action
        self.assertIsNone(action.timeline_card_id)

    def test_changed_choices_are_rejected_before_click(self):
        state = self.snapshot(('TIME_000ta', 'TIME_000tb'))
        action = adapt_action(self.proposed('选择我方2号位卡牌'), state).manual_action
        state.general_choice_cards = {0: 'TIME_000tb', 1: 'TIME_000ta'}
        controller = ManualController(output_func=lambda _: None, executor=NS())
        self.assertFalse(controller.execute(action, state).executed)

    def test_calibrated_buttons_use_client_origin(self):
        for card, x in [('TIME_000ta', 585), ('TIME_000tb', 350)]:
            with patch.object(hearthstone_click, 'get_HS_hwnd', return_value=1), \
                 patch.object(hearthstone_click.win32gui, 'GetClientRect', return_value=(0, 0, 1920, 1080)), \
                 patch.object(hearthstone_click.win32gui, 'ClientToScreen', side_effect=lambda h, p: (p[0]+50, p[1]+31)), \
                 patch.object(hearthstone_click, 'rand_sleep'), \
                 patch.object(hearthstone_click, 'left_click') as click:
                executor = ClickExecutor(click_module=hearthstone_click, action_context=nullcontext)
                executor.choose_timeline(card)
                click.assert_called_once_with(x+50, 836)
