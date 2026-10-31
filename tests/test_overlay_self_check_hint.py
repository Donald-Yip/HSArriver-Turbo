# -*- coding: utf-8 -*-
"""「装好没反应？」按钮 + 自查弹窗：文案状态、按钮接线与版本信息。

用户 2026-10-24 二次反馈：顶部红色大字太丑，改成一个小按钮，点开弹提示，
按顺序排查：① 群里是否最新（当前版本号 / 线上最新）② 自检是否全部通过
③ 是否校准过。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

import log_overlay
import version
import web_ui
from tests.helpers.fake_tk import overlay_harness


class SelfCheckTextTests(unittest.TestCase):
    def test_default_only_shows_local_and_never_claims_latest(self):
        text = log_overlay.self_check_text(None)

        self.assertIn(version.version_label(), text)
        self.assertIn("①", text)
        self.assertIn("②", text)
        self.assertIn("③", text)
        self.assertIn("自检", text)
        self.assertIn("校准", text)
        self.assertIn("获取失败", text)
        self.assertNotIn("已是最新", text)
        # 标题只放在弹窗顶栏，正文不再重复一遍。
        self.assertNotIn("按顺序自查", text)

    def test_version_state_lines(self):
        cases = (
            ("update_available",
             {"state": "update_available",
              "local_label": "v2026.10.24",
              "latest_label": "v2026.10.25"},
             ("v2026.10.24", "v2026.10.25", "不是最新")),
            ("latest",
             {"state": "latest", "local_label": "v2026.10.24",
              "latest_label": "v2026.10.24"},
             ("已是最新",)),
            ("local_newer",
             {"state": "local_newer", "local_label": "v2026.10.25",
              "latest_label": "v2026.10.24"},
             ("线上还是 v2026.10.24",)),
            ("remote_missing",
             {"state": "remote_missing", "local_label": "v2026.10.24"},
             ("线上还没发布",)),
            ("checking",
             {"state": "unknown", "checking": True,
              "local_label": "v2026.10.24"},
             ("正在获取",)),
            ("failed",
             {"state": "unknown", "local_label": "v2026.10.24"},
             ("获取失败",)),
        )
        for name, payload, expected in cases:
            with self.subTest(state=name):
                text = log_overlay.self_check_text(payload)
                for fragment in expected:
                    self.assertIn(fragment, text)

    def test_garbage_payload_falls_back_to_local(self):
        for payload in (None, {}, "bad", 42):
            with self.subTest(payload=payload):
                self.assertIn(version.version_label(),
                              log_overlay.self_check_text(payload))


class _FakeThread:
    def __init__(self, target=None, **_kwargs):
        self.target = target

    def start(self):
        return None


class SelfCheckButtonTests(unittest.TestCase):
    def test_self_check_button_opens_info_dialog_with_version_payload(self):
        payload = {
            "state": "latest",
            "local_label": "v2026.10.24",
            "latest_label": "v2026.10.24",
        }
        calls = []
        with overlay_harness(
            _IS_RUNNING=lambda: True,
            _VERSION_INFO=lambda: calls.append("version") or payload,
        ) as ui:
            button = ui.button(log_overlay.SELF_CHECK_BUTTON_TEXT)
            self.assertEqual(log_overlay.WARN, button.options["bg"])
            # 自查入口是最后一行、横跨整行的按钮（不再占顶部的红色大字）。
            grid = button.grid_calls[-1]
            self.assertEqual(log_overlay.BTN_LAYOUT["self_check"][0], grid["row"])
            self.assertEqual(log_overlay.BTN_LAYOUT["self_check"][1], grid["column"])
            self.assertEqual(log_overlay.BTN_SPAN["self_check"], grid["columnspan"])
            red_hints = [
                label for label in ui.widgets_of("label")
                if label.options.get("fg") == log_overlay.DANGER
                and "装好没反应" in str(label.options.get("text", ""))
            ]
            self.assertEqual([], red_hints)

            with mock.patch.object(log_overlay, "_info_dialog") as dialog:
                button.invoke()

        self.assertEqual(["version"], calls)
        dialog.assert_called_once()
        args = dialog.call_args.args
        self.assertIn("按顺序自查", args[1])
        self.assertIn("已是最新", args[2])
        self.assertIn("v2026.10.24", args[2])

    def test_start_installs_version_callback_for_self_check(self):
        callback = lambda: {"state": "latest"}
        saved = (
            log_overlay._VERSION_INFO,
            log_overlay._STARTED[0],
            log_overlay._THREAD,
        )
        try:
            log_overlay._VERSION_INFO = None
            log_overlay._STARTED[0] = False
            log_overlay._THREAD = None
            with (
                mock.patch.object(log_overlay, "_join_previous_thread"),
                mock.patch.object(log_overlay.threading, "Thread", _FakeThread),
            ):
                status = log_overlay.start(version_callback=callback)
            self.assertEqual(log_overlay.OPEN_STARTED, status)
            self.assertIs(callback, log_overlay._VERSION_INFO)
        finally:
            log_overlay._VERSION_INFO, log_overlay._STARTED[0], log_overlay._THREAD = saved
            log_overlay._STOP.clear()
            log_overlay._CLOSING.clear()


class OverlayWiringTests(unittest.TestCase):
    def test_bind_overlay_passes_the_version_callback(self):
        bound = {}
        overlay = SimpleNamespace(
            start=lambda **kwargs: bound.update(kwargs),
            is_running=lambda: False)
        with (
            mock.patch.object(web_ui, "log_overlay", overlay),
            mock.patch.object(web_ui, "_verify_overlay_opened_async"),
        ):
            web_ui._bind_overlay()

        self.assertIn("version_callback", bound)
        self.assertIs(bound["version_callback"], web_ui._overlay_version_info)

    def test_version_callback_reads_cache_and_swallows_errors(self):
        with mock.patch.object(
                web_ui.update_check, "status",
                return_value={"state": "latest"}) as status:
            self.assertEqual({"state": "latest"},
                             web_ui._overlay_version_info())
        status.assert_called_once_with()

        with mock.patch.object(web_ui.update_check, "status",
                               side_effect=RuntimeError("boom")):
            self.assertIsNone(web_ui._overlay_version_info())


if __name__ == "__main__":
    unittest.main()
