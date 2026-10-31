import unittest
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import click as clicks
from log_op import parse_line
from log_state import LogState, update_state
from manual_controller import ClickExecutor, ManualController, DiscoverChoiceAction
from src.game_state.recommendation_adapter import adapt_action, RecommendationStateError
from src.ocr.stable_reader import StableRecommendationReader
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import OcrEvidence, OcrLine


class ChooseOneTests(unittest.TestCase):
    def feed(self, log, text):
        parsed = parse_line('D 23:06:06.3682005 GameState.DebugPrintOptions() - ' + text)
        self.assertIsNotNone(parsed, text)
        update_state(log, parsed)

    def options(self, source='AV_205p', player='1'):
        log = LogState()
        log.my_player_id = '1'
        self.feed(log, 'id=23')
        self.feed(log, f'  option 5 type=POWER mainEntity=[entityName=培育 id=152 zone=PLAY zonePos=0 cardId={source} player={player}] error=NONE errorParam=')
        choices = {
            'EX1_154': ((0, '阳炎之怒', 'EX1_154a'), (1, '自然之怒', 'EX1_154b')),
            'EX1_165': ((0, '猎豹形态', 'EX1_165a'), (1, '熊形态', 'EX1_165b')),
        }.get(source, ((0, '山谷植根', 'AV_205pb'), (1, '冰雪绽放', 'AV_205a')))
        for i, name, card in choices:
            self.feed(log, f'    subOption {i} entity=[entityName={name} id={153+i} zone=SETASIDE zonePos=0 cardId={card} player={player}] error=NONE errorParam=')
        return log

    def state(self, log, source='AV_205p'):
        card = SimpleNamespace(entity_id='152', card_id=source, cardtype='SPELL', name='抉择牌')
        return SimpleNamespace(game_num_turns_in_play=3, is_my_turn=True,
            my_player_id='1', power_options=log.power_options,
            my_hero_power=card, my_hand_cards=[card], my_minions=[], my_locations=[],
            oppo_minions=[], oppo_locations=[], my_hero=SimpleNamespace(entity_id='hero'),
            oppo_hero=SimpleNamespace(entity_id='enemy'), discover_choice_count=None)

    def proposed(self, primary='使用英雄技能', name='冰雪绽放', target=''):
        text = f'打法参考A\n{primary}\n培育\n选择卡牌\n{name}\n{target}\n打法参考B\n结束回合'
        return RecommendationParser().parse(SimpleNamespace(frame_id='frame',
            normalized_text=text, confidence=.99), 3, 7)

    def test_choose_one_option_is_clicked_before_the_card_target(self):
        def hero_power(events):
            state = self.state(self.options())
            adapted = adapt_action(self.proposed(target='目标是己方英雄'), state)
            return state, adapted, {
                'cancel_click': lambda: events.append('cancel'),
                'click_skill': lambda: events.append('power'),
                'choose_discover_card': lambda i, n: events.append((i, n)),
                'choose_my_hero': lambda: events.append('target'),
            }

        def spell(events):
            state = self.state(self.options('EX1_154'), 'EX1_154')
            adapted = adapt_action(self.proposed(
                '打出1号位法术', name='自然之怒', target='目标是对方英雄'), state)
            return state, adapted, {
                'cancel_click': lambda: events.append('cancel'),
                'choose_card': lambda *_: events.append('hand'),
                'click_middle': lambda: events.append('play'),
                'choose_discover_card': lambda i, n: events.append((i, n)),
                'choose_oppo_hero': lambda: events.append('target'),
            }

        def minion(events):
            state = self.state(self.options('EX1_165'), 'EX1_165')
            state.my_hand_cards[0].cardtype = 'MINION'
            adapted = adapt_action(self.proposed(
                '打出1号位随从', name='熊形态'), state)
            return state, adapted, {
                'cancel_click': lambda: events.append('cancel'),
                'choose_card': lambda *_: events.append('hand'),
                'put_minion': lambda i, n: events.append(('place', i, n)),
                'choose_discover_card':
                    lambda i, n: events.append(('choice', i, n)),
            }

        for case, scenario, expected in (
                ('hero_power', hero_power,
                 ['cancel', 'power', (1, 2), 'target', 'cancel']),
                ('spell', spell,
                 ['cancel', 'hand', 'play', (1, 2), 'target', 'cancel']),
                ('minion', minion,
                 ['cancel', 'hand', ('place', 0, 0), ('choice', 1, 2),
                  'cancel'])):
            with self.subTest(case=case):
                events = []
                state, adapted, patches = scenario(events)
                executor = ClickExecutor(
                    click_module=clicks, action_context=nullcontext,
                    sleep_func=lambda _: None)
                with ExitStack() as stack:
                    for attribute, side_effect in patches.items():
                        stack.enter_context(patch.object(
                            clicks, attribute, side_effect=side_effect))
                    result = ManualController(
                        executor=executor, output_func=lambda _: None,
                    ).execute(adapted.manual_action, state)
                self.assertTrue(result.executed, result.message)
                self.assertEqual(expected, events)

    def test_disabled_choice_keeps_its_layout_slot_but_cannot_be_selected(self):
        def disabled_layout_slot():
            log = self.options()
            self.feed(log, '    subOption 0 entity=[entityName=山谷植根 id=153 zone=SETASIDE zonePos=0 cardId=AV_205pb player=1] error=REQ_ENOUGH_MANA errorParam=')
            manual = adapt_action(self.proposed(), self.state(log)).manual_action
            # 号位按可用布局算（1 号位仍对应 2 号选项），并绑上选项/父实体 id 供执行前复核。
            self.assertEqual(DiscoverChoiceAction(1, 2, choice_entity_id='154',
                                                 choice_card_id='AV_205a',
                                                 choice_parent_entity_id='152'),
                             manual.choose_one)
            with self.assertRaisesRegex(RecommendationStateError,
                                        'choose_one_option_unavailable'):
                adapt_action(self.proposed(name='山谷植根'), self.state(log))

        def named_option_unavailable():
            log = self.troubles((('噩梦爆发', 'EDR_570A', 'NONE'),
                                 ('动荡能量', 'EDR_570B', 'REQ_ENOUGH_MANA')))
            with self.assertRaisesRegex(RecommendationStateError,
                                        'choose_one_option_unavailable'):
                adapt_action(self.trouble_proposed('动荡能量'),
                             self.trouble_state(log))

        for case, check in (('disabled_layout_slot', disabled_layout_slot),
                            ('named_option_unavailable', named_option_unavailable)):
            with self.subTest(case=case):
                check()

    def test_choose_one_is_not_attached_when_not_needed(self):
        """非抉择技能牌照旧；只有一个选项时游戏自动结算，多点一下反而会误选。"""
        non_choose_one = adapt_action(
            self.proposed(), self.state(self.options('CS2_017'), 'CS2_017'))
        single_option = adapt_action(
            self.trouble_proposed('噩梦爆发'),
            self.trouble_state(self.troubles(
                (('噩梦爆发', 'EDR_570A', 'NONE'),))))
        for case, adapted in (('non_choose_one_hero_power', non_choose_one),
                              ('single_option', single_option)):
            with self.subTest(case=case):
                self.assertIsNone(
                    getattr(adapted.manual_action, 'choose_one', None))

    def test_unknown_or_ambiguous_name_is_rejected_before_clicking(self):
        """判不出唯一选项时零点击拒绝，不能先出牌再停在半途。"""
        duplicate = self.options()
        self.feed(duplicate, '    subOption 2 entity=[entityName=冰雪绽放 id=155 zone=SETASIDE zonePos=0 cardId=AV_205a player=1] error=NONE errorParam=')
        near_names = self.troubles((('噩梦爆发', 'EDR_570A', 'NONE'),
                                    ('噩梦暴发', 'EDR_570B', 'NONE')))
        cases = (
            ('unknown_name', self.proposed(name='错误名称'),
             self.state(self.options())),
            ('duplicate_name', self.proposed(), self.state(duplicate)),
            ('near_names', self.trouble_proposed('动荡能量'),
             self.trouble_state(near_names)),
        )
        for case, proposed, current_state in cases:
            with self.subTest(case=case):
                with self.assertRaisesRegex(RecommendationStateError,
                                            'choose_one_name_not_unique'):
                    adapt_action(proposed, current_state)

    def test_missing_or_enemy_power_options_do_not_block_metadata_resolution(self):
        missing = self.options()
        self.feed(missing, 'id=24')
        empty = self.trouble_state(LogState())
        empty.power_options = {}
        cases = (
            ('missing_power_options', self.proposed(), self.state(missing),
             'AV_205a'),
            ('enemy_power_options', self.proposed(),
             self.state(self.options(player='2')), 'AV_205a'),
            ('empty_power_options', self.trouble_proposed('动荡能量'), empty,
             'EDR_570B'),
        )
        for case, proposed, current_state, card_id in cases:
            with self.subTest(case=case):
                choice = adapt_action(
                    proposed, current_state).manual_action.choose_one
                self.assertEqual(card_id, choice.choice_card_id)
                if case == 'empty_power_options':
                    self.assertEqual(1, choice.choice_index)
                    self.assertIsNone(choice.choice_entity_id)

    # ------------------------------------------------ 选项定位的四档（线上死循环修复）
    def troubles(self, choices, source='EDR_570', name='凶险梦魇'):
        """凶险梦魇（EDR_570 / EDR_570A / EDR_570B）的日志夹具。"""
        log = LogState()
        log.my_player_id = '1'
        self.feed(log, 'id=23')
        self.feed(log, f'  option 5 type=POWER mainEntity=[entityName={name} '
                       f'id=152 zone=PLAY zonePos=0 cardId={source} player=1] '
                       f'error=NONE errorParam= ')
        for index, (option_name, card_id, error) in enumerate(choices):
            self.feed(log, f'    subOption {index} entity=[entityName={option_name} '
                           f'id={153 + index} zone=SETASIDE zonePos=0 '
                           f'cardId={card_id} player=1] error={error} errorParam= ')
        return log

    TROUBLE_CHOICES = (('噩梦爆发', 'EDR_570A', 'NONE'),
                       ('动荡能量', 'EDR_570B', 'NONE'))

    def trouble_state(self, log, target=None, source='EDR_570', name='凶险梦魇'):
        card = SimpleNamespace(entity_id='152', card_id=source,
                               cardtype='SPELL', name=name)
        my_minions = [SimpleNamespace(entity_id='301', zone_pos=1,
                                      cardtype='MINION', name='随从')]
        return SimpleNamespace(
            game_num_turns_in_play=3, is_my_turn=True, my_player_id='1',
            power_options=log.power_options, my_hero_power=card,
            my_hand_cards=[card], my_minions=my_minions, my_locations=[],
            oppo_minions=[], oppo_locations=[],
            my_hero=SimpleNamespace(entity_id='hero'),
            oppo_hero=SimpleNamespace(entity_id='enemy'),
            discover_choice_count=None,
            target=target, general_choice_cards={},
            general_choice_entity_ids={})

    def trouble_proposed(self, name, target=''):
        text = (f'打法参考A\n打出1号位法术\n{target}\n'
                f'选择卡牌\n{name}')
        return RecommendationParser().parse(SimpleNamespace(
            frame_id='frame', normalized_text=text, confidence=.99), 3, 7)

    def test_single_character_ocr_error_still_matches_the_option(self):
        """OCR 差一个字（动荡能量 → 动荡能星）也要认得出，不再死循环。"""
        log = self.troubles((('噩梦爆发', 'EDR_570A', 'NONE'),
                             ('动荡能星', 'EDR_570B', 'NONE')))
        adapted = adapt_action(self.trouble_proposed('动荡能量'),
                               self.trouble_state(log))
        self.assertEqual(1, adapted.manual_action.choose_one.choice_index)

    def test_delayed_option_is_awaited_before_the_choice_click(self):
        state = self.trouble_state(LogState())
        state.power_options = {}
        adapted = adapt_action(self.trouble_proposed('动荡能量'), state)
        events = []
        waits = []

        class Clicks:
            def cancel_click(self):
                events.append('cancel')

            def choose_card(self, index, count):
                events.append(('hand', index, count))

            def click_middle(self):
                events.append('play')

            def choose_discover_card(self, index, count):
                events.append(('choice', index, count))

        def wait_for_choice(parent_id, card_id):
            waits.append((parent_id, card_id))
            events.append('choice_ready')
            return True

        executor = ClickExecutor(
            click_module=Clicks(), action_context=nullcontext,
            sleep_func=lambda _seconds: None,
            choice_waiter=wait_for_choice,
        )
        result = ManualController(
            executor=executor, output_func=lambda _message: None,
        ).execute(adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual([('152', 'EDR_570B')], waits)
        self.assertEqual([
            'cancel', ('hand', 0, 1), 'play', 'choice_ready',
            ('choice', 1, 2), 'cancel',
        ], events)

    def test_panel_card_name_falls_back_to_box_index(self):
        """盒子写母卡名（凶险梦魇）时，允许用盒子号位兜底：
        有目标行 → 选要目标的那个选项；没有目标行 → 选不要目标的那个。"""
        log = self.troubles(self.TROUBLE_CHOICES)
        with_target = adapt_action(
            self.trouble_proposed('凶险梦魇', '目标是己方1号位'),
            self.trouble_state(log))
        self.assertEqual(1, with_target.manual_action.choose_one.choice_index)
        without_target = adapt_action(
            self.trouble_proposed('凶险梦魇'),
            self.trouble_state(log))
        self.assertEqual(0, without_target.manual_action.choose_one.choice_index)

    def test_panel_card_name_without_discriminator_is_not_guessed(self):
        """两个选项的"要不要目标"一样时，母卡名兜底也分不出 → 零点击拒绝。"""
        log = self.troubles(
            (('梦中入侵', 'AV_205pb', 'NONE'), ('梦中奇袭', 'AV_205a', 'NONE')),
            source='AV_205p', name='培育')
        with self.assertRaisesRegex(RecommendationStateError,
                                    'choose_one_name_not_unique'):
            adapt_action(self.trouble_proposed('培育'),
                         self.trouble_state(log, source='AV_205p', name='培育'))

    def test_numbered_discover_still_uses_original_layout(self):
        state = self.state(self.options())
        state.discover_choice_count = 3
        proposed = RecommendationParser().parse(SimpleNamespace(frame_id='frame',
            normalized_text='选择我方2号位卡牌', confidence=.99), 3, 7)
        manual = adapt_action(proposed, state).manual_action
        self.assertEqual(DiscoverChoiceAction(1, 3, choice_entity_id='154',
                                             choice_parent_entity_id='152'), manual)

    def test_choice_name_participates_in_ocr_confidence_and_stability(self):
        reader = StableRecommendationReader(SimpleNamespace(min_ocr_confidence=.9), None,
            text_normalizer=RecommendationParser.normalize_action_text)
        evidence = OcrEvidence('frame', 0, tuple(OcrLine(t, c) for t, c in (
            ('使用英雄技能', .99), ('培育', .99), ('选择卡牌', .99), ('冰雪绽放', .5))),
            '使用英雄技能\n培育\n选择卡牌\n冰雪绽放', .99, 'test', 'test')
        result = reader._action_evidence(evidence)
        self.assertEqual('使用英雄技能\n选择卡牌\n冰雪绽放', result.normalized_text)
        self.assertEqual(.5, result.confidence)


if __name__ == '__main__':
    unittest.main()
