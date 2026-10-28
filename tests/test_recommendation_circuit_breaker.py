# -*- coding: utf-8 -*-
"""同一条推荐连续失败 → 熔断（不再重复点）+ 一条带 OCR 原文的 WARN。

线上反馈的“卡住”：盒子给一条读不懂/点不动的推荐（典型是带目标的法术，
如「打出3号位法术 / 漂浮教程 / 目标是己方1号位」），解析或执行一直失败，
流程就无限重试到烧绳。这里锁住：同一条推荐连续失败到上限后只观察、不再
执行；熔断那一刻打一条 WARN（只打一次）；推荐或回合变化后自动恢复计数。

解析失败没有 ProposedAction，计数必须按「回合 + 失败码」而不是 OCR 原文：
2026-10-27 反馈里目标行被 OCR 读成「已方」，原文一抖计数就清零，
`unsupported_spell_target` 连试 82 次、43 秒，熔断形同虚设。
"""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

import battle_record
from manual_controller import ActionExecutionResult, EndTurnAction
from src.flow.recommendation_flow import (
    FlowStepStatus, RecommendationFlow,
)
from src.game_state.recommendation_adapter import AdaptedAction
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import FrameEvidence, OcrEvidence


PANEL = ["打法参考A", "打出3号位法术", "漂浮教程", "目标是己方1号位", "克拉克斯西宾"]
PANEL_TEXT = "\n".join(PANEL)

# 解析一定失败的两种写法（同一个失败码：unsupported_spell_target）。
# 两者只差最后一行，用来模拟 OCR 抖动：原文变了、失败码没变。
PARSE_FAIL_TEXT = "\n".join(["打法参考A", "打出2号位法术", "目标是已方随从"])
PARSE_FAIL_TEXT_JITTER = PARSE_FAIL_TEXT + "\n推荐打法Beta"


def frame(frame_id):
    return FrameEvidence(
        frame_id=frame_id, captured_at=0.0, desktop_size=(1920, 1080), dpi=96,
        window_handle=1, foreground=True,
        recommendation_roi=(7, 32, 278, 970), exact_hash=frame_id,
        perceptual_hash="0" * 64, panel_visible=True)


def evidence(frame_id, lines):
    text = "\n".join(lines) if isinstance(lines, (list, tuple)) else str(lines)
    return OcrEvidence(
        frame_id=frame_id, created_at=0.0, lines=(),
        normalized_text=text, confidence=0.95,
        backend="test", preprocessing="test")


class CyclingCapture:
    """每次抓图给不同的 exact_hash（等于面板一直在刷新）。"""

    def __init__(self):
        self.count = 0

    def capture(self, ocr_panel_ok=False):
        self.count += 1
        return frame(f"f{self.count}")

    @staticmethod
    def crop_recommendation(current_frame):
        return current_frame


class ScriptedReader:
    """按顺序吐出面板文字；用完后一直重复最后一条。"""

    def __init__(self, texts):
        self.texts = list(texts)

    def _next(self):
        if len(self.texts) > 1:
            return self.texts.pop(0)
        return self.texts[0]

    def read(self, frame_supplier, _roi):
        current = frame_supplier()
        return evidence(current.frame_id, self._next())

    def read_frame(self, current_frame, _roi):
        return evidence(current_frame.frame_id, self._next())


class ExplodingAdapter:
    def __call__(self, proposed, state):
        raise ValueError("boom: adapter rejected")


class AcceptingAdapter:
    def __call__(self, proposed, state):
        return AdaptedAction(
            EndTurnAction(), None, None, "turn_changed")


class AcceptingValidator:
    def validate(self, proposed, adapted, *args, **kwargs):
        return SimpleNamespace(accepted=True, value=(adapted, "k"))


class RecordingController:
    def __init__(self):
        self.executed = []

    @staticmethod
    def output(_message):
        pass

    def execute(self, action, _state):
        self.executed.append(action)
        return ActionExecutionResult(True, "executed")


def active_state(turn=5):
    return SimpleNamespace(
        is_my_turn=True, game_num_turns_in_play=turn, hand_entry_count=0,
        my_hand_cards=[], my_minions=[], oppo_minions=[],
        my_locations=[], oppo_locations=[])


def build_flow(reader, adapter=None, limit=3, turn=5, controller=None):
    flow = RecommendationFlow(
        capture=CyclingCapture(), reader=reader,
        parser=RecommendationParser(),
        state_supplier=lambda: (active_state(turn), 10),
        adapter=adapter or ExplodingAdapter(),
        validator=AcceptingValidator(),
        controller=controller or RecordingController(),
        sleep=lambda _s: None,
        same_recommendation_retry_limit=limit)
    return flow


def run_step(flow):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = flow.run_player_turn_step()
    return result, buffer.getvalue()


class CircuitBreakerTests(unittest.TestCase):
    def test_same_recommendation_breaks_the_loop_and_warns_once(self):
        flow = build_flow(ScriptedReader([PANEL_TEXT]), limit=3)

        first, first_out = run_step(flow)
        second, _ = run_step(flow)
        third, third_out = run_step(flow)

        self.assertEqual(FlowStepStatus.RETRY, first.status)
        self.assertEqual(FlowStepStatus.RETRY, second.status)
        # 第三次到上限：改成 OBSERVE（不再执行同一条推荐），并且熔断告警带原文
        self.assertEqual(FlowStepStatus.OBSERVE, third.status)
        self.assertEqual("recommendation_stuck", third.diagnostics)
        self.assertIn("同一条推荐连续失败 3 次", third_out)
        self.assertIn("漂浮教程", third_out)
        self.assertIn("目标是己方1号位", third_out)
        self.assertNotIn("同一条推荐连续失败", first_out)

        # 熔断后继续读同一条：还是观察，但不再重复打警告
        fourth, fourth_out = run_step(flow)
        self.assertEqual(FlowStepStatus.OBSERVE, fourth.status)
        self.assertNotIn("同一条推荐连续失败", fourth_out)

    def test_changed_recommendation_restarts_the_counter(self):
        reader = ScriptedReader([PANEL_TEXT])
        flow = build_flow(reader, limit=3)
        for _ in range(3):
            run_step(flow)                       # 先熔断
        reader.texts = ["结束回合"]
        result, output = run_step(flow)
        # 换了推荐 → 计数从头开始，先回到 RETRY，不是立刻观察
        self.assertEqual(FlowStepStatus.RETRY, result.status)
        self.assertNotIn("同一条推荐连续失败", output)

    def test_new_turn_restarts_the_counter(self):
        reader = ScriptedReader([PANEL_TEXT])
        flow = build_flow(reader, limit=3, turn=5)
        for _ in range(3):
            run_step(flow)
        flow.state_supplier = lambda: (active_state(turn=6), 11)
        result, _ = run_step(flow)
        self.assertEqual(FlowStepStatus.RETRY, result.status)

    def test_stuck_recommendation_is_no_longer_executed(self):
        # 执行了但结果一直确认不了（result_not_confirmed）也会熔断：熔断之后
        # 同一条推荐连点击都不该再点，只观察。
        reader = ScriptedReader(["结束回合"])
        controller = RecordingController()
        flow = RecommendationFlow(
            capture=CyclingCapture(), reader=reader,
            parser=RecommendationParser(),
            state_supplier=lambda: (active_state(), 10),
            adapter=AcceptingAdapter(),
            validator=AcceptingValidator(),
            controller=controller,
            sleep=lambda _s: None,
            same_recommendation_retry_limit=3)

        statuses = [run_step(flow)[0].status for _ in range(4)]

        self.assertEqual(
            [FlowStepStatus.RETRY, FlowStepStatus.RETRY,
             FlowStepStatus.OBSERVE, FlowStepStatus.OBSERVE], statuses)
        # 前两次各点一次，第三次点到上限后熔断，第四次不再执行
        self.assertEqual(3, len(controller.executed))

    def test_success_clears_the_counter(self):
        reader = ScriptedReader(["结束回合", "结束回合", "等待对手操作"])
        flow = build_flow(reader, adapter=AcceptingAdapter(), limit=3)
        result, _ = run_step(flow)
        self.assertEqual(FlowStepStatus.EXECUTED, result.status)
        self.assertEqual(0, flow._retry_count)

        # 成功之后再失败，从 1 重新计数（不会一上来就熔断）
        flow.reader = ScriptedReader([PANEL_TEXT])
        flow.adapter = ExplodingAdapter()
        result, _ = run_step(flow)
        self.assertEqual(FlowStepStatus.RETRY, result.status)


class ParseFailureCircuitBreakerTests(unittest.TestCase):
    """解析失败（拿不到 ProposedAction）时的熔断、原文留痕与恢复。

    2026-10-27 反馈：目标行被 OCR 读成「已方」，旧包正则只认「己方|我方」
    → `unsupported_spell_target` 连试 82 次、43 秒。这里锁住三件事：
    原文抖动不能把计数清零、熔断后不再点、原文要以 recommendation_raw 的
    身份进日志/反馈包（而不是被当成一条新推荐指令）。
    """

    def test_ocr_jitter_does_not_reset_the_counter(self):
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        flow = build_flow(reader, limit=3)

        statuses = []
        for text in (PARSE_FAIL_TEXT, PARSE_FAIL_TEXT_JITTER, PARSE_FAIL_TEXT):
            reader.texts = [text]
            statuses.append(run_step(flow)[0].status)

        self.assertEqual(
            [FlowStepStatus.RETRY, FlowStepStatus.RETRY,
             FlowStepStatus.OBSERVE], statuses)

    def test_flickering_failure_codes_do_not_reset_the_counter(self):
        # 面板一闪一闪时失败码也会在 unsupported_spell_target /
        # empty_recommendation 之间跳：读不懂就是读不懂，照样累计。
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        flow = build_flow(reader, limit=3)

        statuses = []
        for text in (PARSE_FAIL_TEXT, "打法参考A", PARSE_FAIL_TEXT):
            reader.texts = [text]
            statuses.append(run_step(flow)[0].status)

        self.assertEqual(
            [FlowStepStatus.RETRY, FlowStepStatus.RETRY,
             FlowStepStatus.OBSERVE], statuses)

    def test_parse_failure_raw_is_logged_once_with_the_raw_prefix(self):
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        flow = build_flow(reader, limit=5)

        first = run_step(flow)[1]
        second = run_step(flow)[1]

        # 前缀必须是「原文：」：只有这种行会被归到 recommendation_raw
        self.assertIn("[推荐] 原文：目标是已方随从", first)
        self.assertNotIn("OCR原文", first)
        self.assertEqual(1, first.count("[推荐] 原文：目标是已方随从"))
        # 同一段原文重试时不重复刷屏
        self.assertNotIn("[推荐] 原文：目标是已方随从", second)
        self.assertEqual(
            "recommendation_raw",
            battle_record.classify("[推荐] 原文：目标是已方随从")["kind"])

    def test_raw_is_logged_again_in_a_later_episode(self):
        # 去重只针对“同一段插曲”：中间面板读通过一次，之后再读不懂时
        # 还要再留一份原文，不然下一次反馈的 raw 又会是空的。
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        controller = RecordingController()
        flow = build_flow(reader, limit=5, adapter=AcceptingAdapter(),
                          controller=controller)

        self.assertIn("[推荐] 原文：目标是已方随从", run_step(flow)[1])

        reader.texts = ["结束回合"]          # 面板恢复可读 → 插曲结束
        run_step(flow)

        reader.texts = [PARSE_FAIL_TEXT]     # 新的一段“读不懂”
        self.assertIn("[推荐] 原文：目标是已方随从", run_step(flow)[1])

    def test_stuck_warning_is_one_warn_and_no_more_clicks(self):
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        controller = RecordingController()
        flow = build_flow(reader, limit=3, controller=controller)

        outputs = [run_step(flow)[1] for _ in range(4)]

        warn_lines = [line for line in outputs[2].splitlines()
                      if "WARN" in line]
        self.assertEqual(1, len(warn_lines))
        self.assertIn("unsupported_spell_target", warn_lines[0])
        self.assertEqual("warn", battle_record.classify(warn_lines[0])["kind"])
        # 熔断后不再重复告警，也不再点
        self.assertNotIn("WARN", outputs[3])
        self.assertEqual([], controller.executed)

    def test_readable_recommendation_resumes_after_parse_stuck(self):
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        controller = RecordingController()
        flow = build_flow(reader, limit=3, adapter=AcceptingAdapter(),
                          controller=controller)

        for _ in range(3):
            run_step(flow)
        self.assertEqual([], controller.executed)

        # 面板恢复可读 → 不因为“这一回合熔断过”就永久停手
        reader.texts = ["结束回合"]
        result, _ = run_step(flow)
        self.assertNotEqual(FlowStepStatus.OBSERVE, result.status)
        self.assertEqual(1, len(controller.executed))


if __name__ == "__main__":
    unittest.main()
