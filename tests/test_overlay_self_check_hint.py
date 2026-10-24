# -*- coding: utf-8 -*-
"""「装好没反应？」按钮 + 自查弹窗：文案状态、按钮接线与版本信息。

用户 2026-10-24 二次反馈：顶部红色大字太丑，改成一个小按钮，点开弹提示，
按顺序排查：① 群里是否最新（当前版本号 / 线上最新）② 自检是否全部通过
③ 是否校准过。
"""

import inspect
import unittest
from types import SimpleNamespace
from unittest import mock

import log_overlay
import version
import web_ui


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
        self.assertNotIn(log_overlay.SELF_CHECK_TITLE, text)

    def test_update_available_shows_both_versions(self):
        text = log_overlay.self_check_text({
            "state": "update_available",
            "local_label": "v2026.10.24",
            "latest_label": "v2026.10.25",
        })
        self.assertIn("v2026.10.24", text)
        self.assertIn("v2026.10.25", text)
        self.assertIn("不是最新", text)

    def test_latest_state(self):
        text = log_overlay.self_check_text({
            "state": "latest", "local_label": "v2026.10.24",
            "latest_label": "v2026.10.24"})
        self.assertIn("已是最新", text)

    def test_local_newer_and_missing_and_checking(self):
        newer = log_overlay.self_check_text({
            "state": "local_newer", "local_label": "v2026.10.25",
            "latest_label": "v2026.10.24"})
        self.assertIn("线上还是 v2026.10.24", newer)

        missing = log_overlay.self_check_text({
            "state": "remote_missing", "local_label": "v2026.10.24"})
        self.assertIn("线上还没发布", missing)

        checking = log_overlay.self_check_text({
            "state": "unknown", "checking": True,
            "local_label": "v2026.10.24"})
        self.assertIn("正在获取", checking)

        failed = log_overlay.self_check_text({
            "state": "unknown", "local_label": "v2026.10.24"})
        self.assertIn("获取失败", failed)

    def test_garbage_payload_falls_back_to_local(self):
        for payload in (None, {}, "bad", 42):
            with self.subTest(payload=payload):
                self.assertIn(version.version_label(),
                              log_overlay.self_check_text(payload))


class SelfCheckButtonTests(unittest.TestCase):
    def test_button_sits_on_its_own_full_width_row(self):
        self.assertEqual((7, 0), log_overlay.BTN_LAYOUT["self_check"])
        self.assertEqual(2, log_overlay.BTN_SPAN["self_check"])
        self.assertEqual("装好没反应？", log_overlay.SELF_CHECK_BUTTON_TEXT)
        self.assertEqual("装好没反应？按顺序自查", log_overlay.SELF_CHECK_TITLE)

    def test_run_builds_the_button_and_the_info_dialog(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("self_check_btn", source)
        self.assertIn("_place(self_check_btn, \"self_check\")", source)
        self.assertIn("_info_dialog(", source)
        self.assertIn("self_check_text(info)", source)
        # 红色大字已经按用户要求删掉：源码里不应再有红字 Label。
        self.assertNotIn("SELF_CHECK_HINT_COLOR", source)
        self.assertNotIn("hint_label", source)
        self.assertIn("def _info_dialog", inspect.getsource(log_overlay))

    def test_height_only_grew_by_the_button_row(self):
        # 772（去掉红字的 880）→ 802：只多了「装好没反应？」按钮行。
        self.assertEqual(802, log_overlay.WINDOW_HEIGHT)

    def test_start_accepts_the_version_callback(self):
        parameters = inspect.signature(log_overlay.start).parameters
        self.assertIn("version_callback", parameters)
        source = inspect.getsource(log_overlay.start)
        self.assertIn("_VERSION_INFO = version_callback", source)
        self.assertIn('"version_callback": version_callback', source)


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
