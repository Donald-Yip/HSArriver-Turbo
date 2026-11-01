# -*- coding: utf-8 -*-
"""浮窗「常驻」页签：默认页不变、惰性创建、新布局、按钮语义、回调接线（fake Tk）。"""

from __future__ import annotations

import unittest
from unittest import mock

import log_overlay
from tests.helpers.fake_tk import _CALLBACK_NAMES, overlay_harness

# 默认页应有的按钮（契约；常驻页按钮只在点开页签后才创建）。
DEFAULT_BUTTONS = {
    "开始对战", "中止", "本局结束后停止", "校准", "保存日志", "打开日志",
    "清理日志", "导出本次出错截屏与日志", "重启炉石", "退出浮窗",
    "退出脚本", "装好没反应？",
}

RESIDENT_STATE = {
    "enabled": True,
    "state": "running",
    "mode": "wild",
    "deck_index": 3,
    "stale_in_game_seconds": 180.0,
    "stale_other_seconds": 300.0,
    "max_restarts": 3,
    "window_seconds": 600.0,
    "restarts_in_window": 1,
    "restarts_left": 2,
    "restarts_total": 4,
    "log_age": 12.0,
    "in_game": True,
    "paused_reason": None,
    "message": "日志停滞 181s，正在重启",
}


def _state(**overrides):
    return dict(RESIDENT_STATE, **overrides)


class _FakeThread:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.started = False

    def start(self):
        self.started = True


def _descendants(widget):
    found = []
    for child in widget.children:
        found.append(child)
        found.extend(_descendants(child))
    return found


def _body(ui):
    # 分区标题「对战设置」在 section 里，section 在 body 里。
    return ui.label("对战设置").parent.parent


def _resident_page(ui):
    return _body(ui).parent


def _resident_labels(ui):
    return [w for w in _descendants(_resident_page(ui)) if w.kind == "label"]


def _resident_label_texts(ui):
    return [w.options.get("text") for w in _resident_labels(ui)]


def _row(ui, title):
    """常驻页里标题为 title 的那一行（状态页也有「日志」等同名标签）。"""
    matches = [w for w in _resident_labels(ui)
               if w.options.get("text") == title]
    return matches[0].parent


def _cluster(ui, title):
    row = _row(ui, title)
    return [child for child in row.children
            if child.kind == "frame"
            and child.pack_calls
            and child.pack_calls[-1].get("side") == "right"][0]


def _value_box(ui, title):
    return [child for child in _cluster(ui, title).children
            if child.kind == "frame"][0]


def _value_widget(ui, title):
    return [child for child in _value_box(ui, title).children
            if child.kind == "label"][0]


def _row_label(ui, title):
    return [child for child in _row(ui, title).children
            if child.kind == "label"][0]


def _left_inset(widget):
    """沿 pack 链累加 padx，估算控件"左边缘离内容区多远"（fake Tk 无坐标）。"""
    total = 0
    node = widget
    while node is not None and node.kind != "root":
        calls = node.pack_calls
        if calls:
            padx = calls[-1].get("padx")
            if isinstance(padx, (tuple, list)):
                total += int(padx[0])
            elif isinstance(padx, (int, float)):
                total += int(padx)
        node = node.parent
    return total


def _rule_labels(ui):
    return [w for w in _resident_labels(ui)
            if str(w.options.get("text", "")).startswith("· ")]


def _rule_card(ui):
    return _rule_labels(ui)[0].parent


def _header_dot(ui):
    head = ui.label(log_overlay.RESIDENT_HEADER_TEXT).parent
    dots = [child for child in head.children
            if child.kind == "label" and child.options.get("text") == "●"]
    return dots[0]


def _label_texts(ui):
    return [w.options.get("text") for w in ui.widgets_of("label")]


class ResidentPageTests(unittest.TestCase):
    def _harness(self, running=False, resident_state=None, **callbacks):
        """走 harness 正常注入口（不手工 set/restore 模块全局）。"""
        callbacks.setdefault(
            "resident_callback",
            lambda: dict(resident_state if resident_state is not None
                         else RESIDENT_STATE),
        )
        return overlay_harness(_IS_RUNNING=lambda: running, **callbacks)

    # ------------------------------------------------------ 默认页 / 页签
    def test_default_page_is_unchanged(self):
        with self._harness(running=True) as ui:
            labels = _label_texts(ui)
            self.assertIn(log_overlay.STATUS_PAGE_TITLE, labels)
            self.assertIn(log_overlay.RESIDENT_PAGE_TITLE, labels)
            buttons = {w.options.get("text") for w in ui.widgets_of("button")}
            self.assertEqual(DEFAULT_BUTTONS, buttons)
            self.assertNotIn("对战设置", labels)
            self.assertNotIn("保存设置", buttons)
            for name in ("活人感", "投降检测", "炉石", "账号", "日志"):
                ui.label(name)

    def test_switching_pages_updates_the_shared_title_only(self):
        with self._harness() as ui:
            title = ui.label(log_overlay.STATUS_HEADER_TEXT)
            content = title.parent.parent
            self.assertEqual("自动化日志", title.options["text"])

            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            resident_title = ui.label(log_overlay.RESIDENT_HEADER_TEXT)
            self.assertEqual("常驻设置", resident_title.options["text"])
            # 层级不变：标题仍在 head 里、head 仍在 content 里（既有测试钉死）。
            self.assertIs(content, resident_title.parent.parent)

            ui.label(log_overlay.STATUS_PAGE_TITLE).fire("<Button-1>")
            self.assertEqual(
                "自动化日志",
                ui.label(log_overlay.STATUS_HEADER_TEXT).options["text"],
            )

    def test_clicking_the_tab_builds_the_page_lazily(self):
        with self._harness() as ui:
            page_status = ui.label("活人感").parent.parent.parent
            self.assertTrue(page_status.packed)
            self.assertNotIn("对战设置", _label_texts(ui))

            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")

            self.assertFalse(page_status.packed)
            texts = _resident_label_texts(ui)
            for title in ("目标", "日志", "最近事件", "对战设置", "自动恢复"):
                self.assertIn(title, texts)
            self.assertEqual(6, len(_rule_labels(ui)))
            self.assertEqual("暂停常驻", ui.button("暂停常驻").options["text"])
            ui.button("保存设置")
            self.assertTrue(_resident_page(ui).packed)

    def test_switching_back_keeps_the_status_page(self):
        with self._harness() as ui:
            page_status = ui.label("活人感").parent.parent.parent
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            ui.label(log_overlay.STATUS_PAGE_TITLE).fire("<Button-1>")
            self.assertTrue(page_status.packed)
            ui.button("开始对战")

    # ------------------------------------------------------ 状态卡
    def test_status_card_is_single_line_and_uses_the_pure_views(self):
        with self._harness() as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            self.assertEqual("运行中", ui.label("运行中").options["text"])
            self.assertEqual("1 / 3", ui.label("1 / 3").options["text"])
            self.assertIn("狂野", ui.label("狂野 · 卡组 3").options["text"])
            self.assertIn("12s 前", ui.label("12s 前 · 对局中").options["text"])
            self.assertIn("日志停滞 181s",
                          ui.label("日志停滞 181s，正在重启").options["text"])
            for title in ("目标", "日志", "最近事件"):
                value = _row(ui, title).children[-1]
                self.assertNotIn("\n", str(value.options.get("text")))
                self.assertNotIn("wraplength", value.options)

    def test_event_is_truncated_to_about_18_chars_in_the_card(self):
        message = "常驻自动恢复失败，已停止自愈并告警；请处理后重新开启"
        with self._harness(resident_state=_state(message=message)) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            value = _row(ui, "最近事件").children[-1]
            text = str(value.options.get("text"))
            self.assertNotIn("\n", text)
            self.assertLessEqual(len(text), 18)
            self.assertTrue(text.endswith("…"))

    def test_header_dot_follows_the_state(self):
        with self._harness() as ui:
            # 状态页的圆点保持绿色。
            head = ui.label(log_overlay.STATUS_HEADER_TEXT).parent
            status_dot = [c for c in head.children
                          if c.kind == "label" and c.options.get("text") == "●"][0]
            self.assertEqual(log_overlay.GREEN, status_dot.options["fg"])
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            self.assertEqual(log_overlay.GREEN, _header_dot(ui).options["fg"])
            # 切回状态页：恢复绿色。
            ui.label(log_overlay.STATUS_PAGE_TITLE).fire("<Button-1>")
            self.assertEqual(log_overlay.GREEN, status_dot.options["fg"])
        # 暂停态：圆点变 DIM。
        with self._harness(
            resident_state=_state(state="paused", paused_reason="user_stop")
        ) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            self.assertEqual(log_overlay.DIM, _header_dot(ui).options["fg"])
        # 出错态：DANGER。
        with self._harness(resident_state=_state(state="error")) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            self.assertEqual(log_overlay.DANGER, _header_dot(ui).options["fg"])

    def test_missing_callback_data_is_shown_not_crashed(self):
        with self._harness(resident_callback=lambda: {}) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            labels = _label_texts(ui)
            self.assertTrue(any("未提供常驻状态" in str(t) for t in labels))
            # 没有状态时说明卡仍显示默认 6 行。
            self.assertEqual(6, len(_rule_labels(ui)))

    # ------------------------------------------------------ 等待定时窗口
    def test_waiting_schedule_shows_reason_and_offers_pause(self):
        toggles = []
        applied = []
        with self._harness(
            resident_state=_state(
                state="waiting_schedule",
                waiting_reason="等待定时 23:00 开始",
            ),
            on_resident_toggle=lambda: toggles.append("toggle") or "常驻已关闭",
            on_resident_apply=lambda body: applied.append(dict(body)) or {"ok": True},
        ) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            # 状态行：第一行「等待定时窗口」，第二行是控制台给的等待原因。
            state = ui.label("等待定时窗口\n等待定时 23:00 开始")
            self.assertEqual(
                "等待定时窗口\n等待定时 23:00 开始", state.options["text"]
            )
            self.assertEqual(log_overlay.ACCENT, _header_dot(ui).options["fg"])
            # waiting_schedule 仍在值守：按钮是「暂停常驻」，点击走 toggle 而不是恢复。
            ui.button("暂停常驻").invoke()
        self.assertEqual(["toggle"], toggles)
        self.assertEqual([], applied)

    def test_waiting_schedule_without_reason_is_single_line(self):
        # 老控制台只给 state、没有 waiting_reason：只显示状态行第一行。
        with self._harness(
            resident_state=_state(state="waiting_schedule", waiting_reason=None)
        ) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            self.assertEqual(
                "等待定时窗口", ui.label("等待定时窗口").options["text"]
            )
            self.assertEqual("暂停常驻", ui.button("暂停常驻").options["text"])

    # ------------------------------------------------------ 规则说明（紧凑列表）
    def test_rules_list_sits_above_buttons_without_a_big_card(self):
        with self._harness() as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            body = _body(ui)
            children = list(body.children)
            rules = _rule_labels(ui)[0].parent
            btn_row = ui.button("保存设置").parent
            hint = [w for w in _resident_labels(ui)
                    if w.options.get("text") == "保存后立即生效"][0]
            # 规则是紧凑列表（不是撑满中段的大卡）：背景回归页面底色、不 expand。
            self.assertEqual(log_overlay.TITLE_BG, rules.options["bg"])
            self.assertEqual(6, len(_rule_labels(ui)))
            for line in _rule_labels(ui):
                self.assertIs(rules, line.parent)
                self.assertEqual("x", line.pack_calls[-1]["fill"])
                self.assertFalse(line.pack_calls[-1].get("expand"))
                self.assertEqual(log_overlay.px(2), line.pack_calls[-1]["pady"])
            # 与设置区同一左基线（body px(12) + 列表 px(12)）。
            self.assertEqual(log_overlay.px(12), rules.pack_calls[-1]["padx"])
            self.assertLess(children.index(rules), children.index(btn_row))
            self.assertLess(children.index(btn_row), children.index(hint))
            # 规则与按钮之间只有一个可伸缩占位（窗口高度随内容收放）。
            expanding = [child for child in children
                         if child.kind == "frame"
                         and any(call.get("expand") for call in child.pack_calls)]
            self.assertEqual(1, len(expanding))
            self.assertLess(children.index(rules), children.index(expanding[0]))
            # 分区标题与说明同左基线。
            section = ui.label("自动恢复").parent
            self.assertEqual(log_overlay.px(12), section.pack_calls[-1]["padx"])

    def test_rules_card_six_lines_follow_the_settings(self):
        with self._harness(
            resident_state=_state(
                stale_in_game_seconds=120.0,
                stale_other_seconds=240.0,
                window_seconds=900.0,
                max_restarts=5,
            )
        ) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            lines = [str(w.options.get("text")) for w in _rule_labels(ui)]
            self.assertEqual(6, len(lines))
            self.assertIn("120s", lines[0])
            self.assertIn("240s", lines[1])
            self.assertIn("重连", lines[2])
            self.assertIn("不盲开局", lines[3])
            self.assertIn("暂停常驻", lines[4])
            self.assertIn("15 分钟", lines[5])
            self.assertIn("5 次", lines[5])
            for line in lines:
                self.assertNotIn("\n", line)

    # ------------------------------------------------------ 分段模式控件
    def test_mode_segment_highlights_the_selected_mode(self):
        with self._harness() as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            self.assertEqual(log_overlay.ACCENT, ui.label("狂野").options["bg"])
            self.assertEqual(log_overlay.BG, ui.label("狂野").options["fg"])
            self.assertEqual(log_overlay.PANEL, ui.label("标准").options["bg"])
            self.assertEqual(log_overlay.DIM, ui.label("标准").options["fg"])
            # 与状态卡同一左基线（body px(12)）。
            mode_box = ui.label("标准").parent
            self.assertEqual("x", mode_box.pack_calls[-1]["fill"])
            self.assertEqual(log_overlay.px(12),
                             _body(ui).pack_calls[-1]["padx"])

            ui.label("休闲").fire("<Button-1>")
            self.assertEqual(log_overlay.ACCENT, ui.label("休闲").options["bg"])
            self.assertEqual(log_overlay.PANEL, ui.label("狂野").options["bg"])

    # ------------------------------------------------------ stepper
    def test_steppers_are_visible_clusters_with_continuous_values(self):
        with self._harness() as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            for title, expected in (("卡组", "第 3 套"),
                                    ("对局中", "180s"),
                                    ("其他界面", "300s")):
                cluster = _cluster(ui, title)
                self.assertEqual("right", cluster.pack_calls[-1]["side"])
                self.assertEqual(log_overlay.px(66),
                                 _value_box(ui, title).options["width"])
                # 数值连续：不含空格拆开的单位。
                self.assertEqual(expected, _value_widget(ui, title).options["text"])
                labels = [c for c in cluster.children if c.kind == "label"]
                self.assertEqual(["−", "+"],
                                 [c.options["text"] for c in labels])
                for button in labels:
                    self.assertEqual(log_overlay.PANEL2, button.options["bg"])
                    self.assertEqual(log_overlay.DIM, button.options["fg"])
                # 整行缩进由 steppers 容器统一给 px(12)，与状态卡内容同左基线。
                self.assertEqual(log_overlay.px(12),
                                 _row(ui, title).parent.pack_calls[-1]["padx"])
                # 值区里不再有单独的「s」小字。
                self.assertEqual(
                    [], [c for c in _value_box(ui, title).children
                         if c.kind == "label"
                         and c.options.get("text") == "s"]
                )
            # 标签列加宽到能完整显示 4 个汉字（「其他界面」不再被截）。
            target_label = _row_label(ui, "目标")
            for title in ("卡组", "对局中", "其他界面"):
                label = _row_label(ui, title)
                self.assertEqual(title, label.options["text"])
                self.assertGreaterEqual(int(label.options.get("width") or 0), 6)
                # 与状态卡「目标/日志/最近事件」同一左基线。
                self.assertEqual(
                    _left_inset(target_label), _left_inset(label), title
                )

    def test_steppers_clamp_and_deck_zero_shows_unspecified(self):
        with self._harness(resident_state=_state(deck_index=None)) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            cluster = _cluster(ui, "卡组")
            minus = [c for c in cluster.children
                     if c.kind == "label" and c.options.get("text") == "−"][0]
            minus.fire("<Button-1>")
            self.assertEqual("未指定", _value_widget(ui, "卡组").options["text"])
            plus = [c for c in cluster.children
                    if c.kind == "label" and c.options.get("text") == "+"][0]
            for _ in range(15):
                plus.fire("<Button-1>")
            self.assertEqual("第 9 套", _value_widget(ui, "卡组").options["text"])

    # ------------------------------------------------------ 按钮语义
    def test_running_offers_pause_and_calls_the_toggle(self):
        toggles = []
        applied = []
        with self._harness(
            resident_state=_state(state="running"),
            on_resident_toggle=lambda: toggles.append("toggle") or "常驻已关闭",
            on_resident_apply=lambda body: applied.append(dict(body)) or {"ok": True},
        ) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            ui.button("暂停常驻").invoke()
        self.assertEqual(["toggle"], toggles)
        self.assertEqual([], applied)

    def test_paused_states_offer_resume_and_resume_in_one_click(self):
        for paused_state in ("paused", "paused_exhausted", "error"):
            with self.subTest(state=paused_state):
                current = {"value": _state(state=paused_state,
                                           paused_reason="user_stop")}
                applied = []
                toggles = []

                def _resident_cb():
                    return dict(current["value"])

                def _apply(body):
                    applied.append(dict(body))
                    current["value"] = _state(state="countdown")
                    return {"ok": True, "message": "已恢复常驻"}

                with self._harness(
                    resident_callback=_resident_cb,
                    on_resident_apply=_apply,
                    on_resident_toggle=lambda: toggles.append("toggle") or "x",
                ) as ui:
                    ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
                    ui.button("开启常驻").invoke()
                    self.assertEqual([{"enabled": True}], applied)
                    self.assertEqual([], toggles)
                    ui.button("暂停常驻")
                    self.assertIn("已恢复常驻", _label_texts(ui))

    def test_disabled_offers_enable_and_calls_the_toggle(self):
        toggles = []
        with self._harness(
            resident_state=_state(enabled=False, state="disabled"),
            on_resident_toggle=lambda: toggles.append("toggle") or "常驻已开启",
            on_resident_apply=lambda body: {"ok": True},
        ) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            ui.button("开启常驻").invoke()
        self.assertEqual(["toggle"], toggles)

    # ------------------------------------------------------ 保存设置
    def test_apply_button_sends_mode_deck_and_thresholds(self):
        received = []

        with self._harness(
            resident_state=_state(deck_index=None),
            on_resident_apply=(
                lambda body: received.append(dict(body))
                or {"ok": True, "message": "已保存"}
            ),
        ) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            ui.label("狂野").fire("<Button-1>")
            # 卡组 stepper：+ 两次 → 2；阈值用默认 180/300。
            cluster = _cluster(ui, "卡组")
            plus = [c for c in cluster.children
                    if c.kind == "label" and c.options.get("text") == "+"][0]
            plus.fire("<Button-1>")
            plus.fire("<Button-1>")
            self.assertEqual("第 2 套", _value_widget(ui, "卡组").options["text"])
            ui.button("保存设置").invoke()
        self.assertEqual(
            [{
                "mode": "wild",
                "deck_index": 2,
                "stale_in_game_seconds": 180,
                "stale_other_seconds": 300,
            }],
            received,
        )

    def test_apply_failure_keeps_the_error_message_visible(self):
        with self._harness(
            on_resident_apply=lambda body: {
                "ok": False, "message": "先停止自动化再开启常驻",
            },
        ) as ui:
            ui.label(log_overlay.RESIDENT_PAGE_TITLE).fire("<Button-1>")
            ui.button("保存设置").invoke()
            self.assertIn("先停止自动化再开启常驻", _label_texts(ui))

    def test_window_height_is_unchanged(self):
        self.assertEqual(940, log_overlay.WINDOW_HEIGHT)


class FakeHarnessResidentCallbackTests(unittest.TestCase):
    """fake_tk 的回调清单要认识三个常驻回调（R5 nit 的回归钉子）。"""

    def test_callback_names_include_the_resident_callbacks(self):
        for name in ("_RESIDENT", "_ON_RESIDENT_TOGGLE", "_ON_RESIDENT_APPLY"):
            self.assertIn(name, _CALLBACK_NAMES)

    def test_harness_injects_and_restores_the_callbacks(self):
        before = log_overlay._RESIDENT
        before_toggle = log_overlay._ON_RESIDENT_TOGGLE
        resident = lambda: {}
        toggle = lambda: None
        apply_ = lambda body: {"ok": True}
        with overlay_harness(
            resident_callback=resident,
            on_resident_toggle=toggle,
            on_resident_apply=apply_,
        ):
            self.assertIs(resident, log_overlay._RESIDENT)
            self.assertIs(toggle, log_overlay._ON_RESIDENT_TOGGLE)
            self.assertIs(apply_, log_overlay._ON_RESIDENT_APPLY)
        self.assertIs(before, log_overlay._RESIDENT)
        self.assertIs(before_toggle, log_overlay._ON_RESIDENT_TOGGLE)


class ResidentWiringTests(unittest.TestCase):
    def test_start_installs_the_three_callbacks(self):
        resident = lambda: {"enabled": True}
        toggle = lambda: "ok"
        apply_ = lambda body: {"ok": True}
        saved = (
            log_overlay._RESIDENT,
            log_overlay._ON_RESIDENT_TOGGLE,
            log_overlay._ON_RESIDENT_APPLY,
            log_overlay._STARTED[0],
            log_overlay._THREAD,
        )
        # 前面的浮窗测试可能把 _STARTED 留在 True（进程内共享模块状态）：
        # start() 见到 True 会直接返回 OPEN_ALREADY，这里显式从“没开着”开始。
        log_overlay._STARTED[0] = False
        try:
            with (
                mock.patch.object(log_overlay, "_join_previous_thread"),
                mock.patch.object(log_overlay.threading, "Thread", _FakeThread),
            ):
                status = log_overlay.start(
                    resident_callback=resident,
                    on_resident_toggle=toggle,
                    on_resident_apply=apply_,
                )
            self.assertEqual(log_overlay.OPEN_STARTED, status)
            self.assertIs(resident, log_overlay._RESIDENT)
            self.assertIs(toggle, log_overlay._ON_RESIDENT_TOGGLE)
            self.assertIs(apply_, log_overlay._ON_RESIDENT_APPLY)
        finally:
            (
                log_overlay._RESIDENT,
                log_overlay._ON_RESIDENT_TOGGLE,
                log_overlay._ON_RESIDENT_APPLY,
                log_overlay._STARTED[0],
                log_overlay._THREAD,
            ) = saved
            log_overlay._STOP.clear()
            log_overlay._CLOSING.clear()

    def test_defaults_are_safe_when_web_ui_is_old(self):
        saved = (
            log_overlay._RESIDENT,
            log_overlay._ON_RESIDENT_TOGGLE,
            log_overlay._ON_RESIDENT_APPLY,
            log_overlay._STARTED[0],
            log_overlay._THREAD,
        )
        # 同上：别的浮窗测试可能留下 _STARTED=True，先归零再验证默认回调。
        log_overlay._STARTED[0] = False
        try:
            with (
                mock.patch.object(log_overlay, "_join_previous_thread"),
                mock.patch.object(log_overlay.threading, "Thread", _FakeThread),
            ):
                log_overlay.start()
            self.assertEqual({}, log_overlay._RESIDENT())
            self.assertIsNone(log_overlay._ON_RESIDENT_TOGGLE())
            result = log_overlay._ON_RESIDENT_APPLY({})
            self.assertFalse(result["ok"])
        finally:
            (
                log_overlay._RESIDENT,
                log_overlay._ON_RESIDENT_TOGGLE,
                log_overlay._ON_RESIDENT_APPLY,
                log_overlay._STARTED[0],
                log_overlay._THREAD,
            ) = saved
            log_overlay._STOP.clear()
            log_overlay._CLOSING.clear()


class ResidentWindowHeightTests(unittest.TestCase):
    """切到常驻页时按内容收放窗口高度（避免固定 940 下半屏空黑）。"""

    def test_clamps_between_min_and_max(self):
        self.assertEqual(940, log_overlay.resident_window_height(1200, 940))
        self.assertEqual(600, log_overlay.resident_window_height(600, 940))
        self.assertEqual(
            log_overlay.RESIDENT_PAGE_MIN_HEIGHT,
            log_overlay.resident_window_height(100, 940),
        )

    def test_bad_values_fall_back_to_the_full_height(self):
        self.assertEqual(940, log_overlay.resident_window_height("x", 940))
        self.assertEqual(940, log_overlay.resident_window_height(None, 940))


if __name__ == "__main__":
    unittest.main()
