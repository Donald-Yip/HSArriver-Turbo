# -*- coding: utf-8 -*-
"""执行日志要写清楚「执行了什么」，不再只有一句「开始点击」。

用户 2026-10-31：「执行要写清楚执行什么，修改功能要注意架构」。本文件锁住：

1. 适配层 ``describe_adapted``：按 ``actions.py`` 的动作 DTO 生成一句中文
   （号位/卡名/目标），卡名认不出退回 card_id，认不出动作退回推荐原文；
2. ``AdaptedAction.description`` 是纯数据字段，``RecommendationAdapter.__call__``
   在适配完成后填充（``replace`` 分支不会丢）；
3. 应用层只用 ``getattr`` 读 description，缺失时回退推荐原文；点击成功后追加
   ``[SYS] 执行结果：…``（空消息不打印）。
"""

import io
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from types import SimpleNamespace

from hslegend.adapters.legacy.actions import (
    ActionExecutionResult,
    AssembleChoiceAction,
    AttackAction,
    DiscoverChoiceAction,
    EndTurnAction,
    HeroPowerAction,
    LaunchStarshipAction,
    PlayCardAction,
    RefreshAction,
    SelectTargetAction,
    Target,
    TimelineAction,
    TradeCardAction,
    UseAbilityAction,
    UseLocationAction,
)
from hslegend.adapters.legacy.recommendation import (
    AdaptedAction,
    RecommendationAdapter,
    describe_adapted,
)
from hslegend.application.recommendation_flow import (
    FlowStepStatus,
    RecommendationFlow,
)
from hslegend.domain import ActionKind, ProposedAction, SlotRef
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import FrameEvidence, OcrEvidence


def make_proposed(action, instruction="", **overrides):
    values = dict(
        action_id="a1", frame_id="f1", created_at=0.0, turn_number=5,
        log_revision=10, raw_instruction=instruction,
        normalized_instruction=instruction, action=action)
    values.update(overrides)
    return ProposedAction(**values)


def adapted(manual, source_id=None, target_id=None, postcondition=""):
    return AdaptedAction(manual, source_id, target_id, postcondition)


class DescribePlayCardTests(unittest.TestCase):
    def test_play_card_names_the_card_slot_and_target(self):
        text = describe_adapted(
            adapted(PlayCardAction(0, "CS2_029", "SPELL", gap_index=1,
                                   target=Target("enemy", "hero", None, "64")),
                    "11", "64", "hand_card_left"),
            make_proposed(ActionKind.PLAY_CARD, "打出1号位法术"))

        self.assertIn("打出1号位法术", text)
        self.assertIn("「火球术」", text)
        self.assertIn("→ 我方2号位", text)
        self.assertIn("对方英雄", text)

        # 卡名认不出就退回 card_id。
        fallback = describe_adapted(
            adapted(PlayCardAction(0, "XXX_9999", "MINION", gap_index=0)),
            make_proposed(ActionKind.PLAY_CARD, "打出1号位随从"))
        self.assertIn("打出1号位随从「XXX_9999」→ 我方1号位", fallback)


class DescribeAttackTests(unittest.TestCase):
    def test_minion_attack_uses_the_state_entity_name(self):
        state = SimpleNamespace(
            my_minions=[SimpleNamespace(entity_id="301", name="影焰猎豹")])

        text = describe_adapted(
            adapted(AttackAction(
                Target("friendly", "minion", 0, "301"),
                Target("enemy", "hero", None, "64")),
                "301", "64", "combat_state_changed"),
            make_proposed(ActionKind.ATTACK, "操作1号位随从攻击"),
            state)

        self.assertEqual("1号位随从「影焰猎豹」攻击 → 对方英雄", text)

    def test_attack_targets_read_as_slots_and_my_hero(self):
        text = describe_adapted(
            adapted(AttackAction(
                Target("friendly", "minion", 1, "301"),
                Target("enemy", "minion", 2, "402"))),
            make_proposed(ActionKind.ATTACK, "操作2号位随从攻击"))

        self.assertIn("2号位随从攻击", text)
        self.assertIn("对方3号位随从", text)

        self.assertEqual(
            "我方英雄攻击 → 对方英雄",
            describe_adapted(adapted(AttackAction(
                Target("friendly", "hero", None, "1"),
                Target("enemy", "hero", None, "64")))))


class DescribeOtherActionsTests(unittest.TestCase):
    def test_hero_power_uses_the_parsed_source_name_with_card_id_fallback(self):
        text = describe_adapted(
            adapted(HeroPowerAction()),
            make_proposed(ActionKind.USE_HERO_POWER, "使用英雄技能",
                          source_name="生命分流"))

        self.assertEqual("使用英雄技能「生命分流」", text)
        self.assertIn(
            "「HERO_9999」",
            describe_adapted(adapted(HeroPowerAction(card_id="HERO_9999"))))

    def test_end_turn_discover_and_assemble(self):
        self.assertEqual("结束回合（点右侧按钮）",
                         describe_adapted(adapted(EndTurnAction())))
        self.assertEqual(
            "选择第3个选项 → 对方1号位随从",
            describe_adapted(
                adapted(DiscoverChoiceAction(
                    2, 4, target=Target("enemy", "minion", 0, "402")))))
        self.assertEqual("拼装：选第2个部件",
                         describe_adapted(adapted(AssembleChoiceAction(1))))

    def test_use_ability_names_the_skill(self):
        text = describe_adapted(
            adapted(UseAbilityAction(
                source=Target("friendly", "minion", 0, "301"),
                card_id="BE_013", choice_index=3, choice_count=4,
                choice_name="克苏恩的愤怒")))

        self.assertEqual("1号位随从发动技能「克苏恩的愤怒」", text)

    def test_select_target(self):
        self.assertEqual(
            "选择对方2号位随从",
            describe_adapted(adapted(SelectTargetAction(
                Target("enemy", "minion", 1, "402")))))
        self.assertEqual(
            "选择对方英雄",
            describe_adapted(adapted(SelectTargetAction(
                Target("enemy", "hero", None, "64")))))

    def test_location_starship_trade_timeline_and_refresh(self):
        self.assertEqual("使用2号位地标", describe_adapted(
            adapted(UseLocationAction(1, "X"))))
        self.assertEqual("发射星舰", describe_adapted(
            adapted(LaunchStarshipAction(0, "X"))))
        self.assertEqual("交易3号位手牌", describe_adapted(
            adapted(TradeCardAction(2, "X", "MINION"))))
        self.assertEqual("回溯", describe_adapted(
            adapted(TimelineAction("undo"))))
        self.assertEqual("维持", describe_adapted(
            adapted(TimelineAction("keep"))))
        self.assertEqual("刷新酒馆", describe_adapted(adapted(RefreshAction())))

    def test_unknown_action_falls_back_to_the_recommendation_text(self):
        proposed = make_proposed(ActionKind.PLAY_CARD, "打出1号位随从")

        self.assertEqual(
            "打出1号位随从",
            describe_adapted(adapted(SimpleNamespace()), proposed))
        self.assertEqual("", describe_adapted(adapted(SimpleNamespace())))


class AdapterFillsDescriptionTests(unittest.TestCase):
    def test_call_fills_the_description_and_replace_keeps_it(self):
        adapter = RecommendationAdapter()
        state = SimpleNamespace(
            game_num_turns_in_play=5, is_my_turn=True,
            my_hand_cards=[SimpleNamespace(
                entity_id="11", card_id="CS2_029", cardtype="SPELL")])

        result = adapter(
            make_proposed(
                ActionKind.PLAY_CARD, "打出1号位法术",
                source=SlotRef("hand_slot", "friendly", 1),
                card_type="SPELL"),
            state)

        self.assertEqual("打出1号位法术「火球术」", result.description)

        original = AdaptedAction(
            PlayCardAction(0, "CS2_029", "SPELL"), "11", None,
            "hand_card_left", description="打出1号位法术「火球术」")
        updated = replace(
            original,
            manual_action=replace(original.manual_action, gap_index=2))

        self.assertEqual("打出1号位法术「火球术」", updated.description)


# ---------------------------------------------------------------- flow 日志
def frame(frame_id):
    return FrameEvidence(
        frame_id=frame_id, captured_at=0.0, desktop_size=(1920, 1080),
        dpi=96, window_handle=1, foreground=True,
        recommendation_roi=(7, 32, 278, 970), exact_hash=frame_id,
        perceptual_hash="0" * 64, panel_visible=True)


class CyclingCapture:
    def __init__(self):
        self.count = 0

    def capture(self, ocr_panel_ok=False):
        self.count += 1
        return frame(f"f{self.count}")

    @staticmethod
    def crop_recommendation(current_frame):
        return current_frame


class FixedReader:
    def read(self, frame_supplier, _roi):
        return self._evidence(frame_supplier().frame_id)

    def read_frame(self, current_frame, _roi):
        return self._evidence(current_frame.frame_id)

    @staticmethod
    def _evidence(frame_id):
        return OcrEvidence(
            frame_id=frame_id, created_at=0.0, lines=(),
            normalized_text="结束回合", confidence=0.95,
            backend="test", preprocessing="test")


class EndTurnAdapter:
    def __init__(self, description=""):
        self.description = description
        self.states = []

    def __call__(self, _proposed, state):
        self.states.append(state)
        return AdaptedAction(EndTurnAction(), None, None, "turn_changed",
                             description=self.description)


class AcceptingValidator:
    def validate(self, _proposed, adapted, *args):
        return SimpleNamespace(accepted=True, value=(adapted, "k"))


class RecordingController:
    def __init__(self, message="executed"):
        self.message = message
        self.executed = []
        self.outputs = []

    def output(self, message):
        self.outputs.append(message)

    def execute(self, action, _state):
        self.executed.append(action)
        return ActionExecutionResult(True, self.message)


def run_step(flow):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = flow.run_player_turn_step()
    return result, buffer.getvalue()


def active_state(turn=5):
    return SimpleNamespace(
        is_my_turn=True, game_num_turns_in_play=turn, hand_entry_count=0,
        my_hand_cards=[], my_minions=[], oppo_minions=[],
        my_locations=[], oppo_locations=[])


def build_box_flow(adapter, controller):
    return RecommendationFlow(
        capture=CyclingCapture(), reader=FixedReader(),
        parser=RecommendationParser(),
        state_supplier=lambda: (active_state(), 10),
        adapter=adapter, validator=AcceptingValidator(),
        controller=controller, sleep=lambda _seconds: None,
        clock=lambda: 100.0)


def build_local_flow(adapter, controller):
    action = ProposedAction(
        action_id="a1", frame_id="search:5:10", created_at=0.0,
        turn_number=5, log_revision=10, raw_instruction="打出1号位随从",
        normalized_instruction="打出1号位随从", action=ActionKind.PLAY_CARD,
        source=SlotRef("hand_slot", "friendly", 1),
        card_type="MINION", entity_id="c1")

    class Planner:
        def plan(self, state, cost, stopped=None):
            return [action], 1.0

        @staticmethod
        def describe(item):
            return item.normalized_instruction

    return RecommendationFlow(
        capture=None, reader=None, parser=None,
        state_supplier=lambda: (active_state(), 10),
        adapter=adapter, validator=None, controller=controller,
        sleep=lambda _seconds: None, clock=lambda: 100.0,
        box_ai=False, planner=Planner())


class FlowExecutionLogTests(unittest.TestCase):
    def test_box_path_logs_the_adapted_description_or_falls_back(self):
        controller = RecordingController()
        flow = build_box_flow(
            EndTurnAdapter("打出1号位法术「火球术」→ 我方1号位"), controller)

        result, output = run_step(flow)

        self.assertEqual(FlowStepStatus.OBSERVE, result.status)
        self.assertIn("[执行] 打出1号位法术「火球术」→ 我方1号位", output)
        self.assertNotIn("开始点击", output)

        controller = RecordingController()
        _result, output = run_step(build_box_flow(EndTurnAdapter(""), controller))

        self.assertIn("[执行] 结束回合", output)
        self.assertNotIn("开始点击", output)

    def test_successful_execution_logs_the_result_message(self):
        controller = RecordingController(message="已操作随从攻击")
        flow = build_box_flow(EndTurnAdapter("1号位随从攻击 → 对方英雄"),
                              controller)

        _result, output = run_step(flow)

        self.assertIn("[SYS] 执行结果：已操作随从攻击", output)

    def test_empty_result_message_is_not_logged(self):
        controller = RecordingController(message="")
        flow = build_box_flow(EndTurnAdapter("结束回合（点右侧按钮）"),
                              controller)

        _result, output = run_step(flow)

        self.assertNotIn("[SYS] 执行结果：", output)

    def test_local_path_logs_the_description_and_the_result(self):
        controller = RecordingController(message="已打出手牌")
        flow = build_local_flow(
            EndTurnAdapter("打出1号位随从「火球术」→ 我方1号位"), controller)

        result, output = run_step(flow)

        self.assertEqual(FlowStepStatus.OBSERVE, result.status)
        self.assertIn("[执行] 打出1号位随从「火球术」→ 我方1号位", output)
        self.assertIn("[SYS] 执行结果：已打出手牌", output)
        self.assertNotIn("开始点击", output)


if __name__ == "__main__":
    unittest.main()
