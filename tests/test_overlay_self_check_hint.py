# -*- coding: utf-8 -*-
"""浮窗顶部红色自查提示：文案状态、醒目样式、接线与版本信息。

用户要求（2026-10-24）：浮窗上找个位置写红色大字，回应「装好却不动」的人，
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


class SelfCheckHintTextTests(unittest.TestCase):
    def test_default_only_shows_local_and_never_claims_latest(self):
        text = log_overlay.self_check_hint(None)

        self.assertIn("装好没反应？按顺序自查", text)
        self.assertIn(version.version_label(), text)
        self.assertIn("①", text)
        self.assertIn("②", text)
        self.assertIn("③", text)
        self.assertIn("自检", text)
        self.assertIn("校准", text)
        self.assertIn("获取失败", text)
        self.assertNotIn("已是最新", text)

    def test_update_available_shows_both_versions(self):
        text = log_overlay.self_check_hint({
            "state": "update_available",
            "local_label": "v2026.10.24",
            "latest_label": "v2026.10.25",
        })
        self.assertIn("v2026.10.24", text)
        self.assertIn("v2026.10.25", text)
        self.assertIn("不是最新", text)

    def test_latest_state(self):
        text = log_overlay.self_check_hint({
            "state": "latest", "local_label": "v2026.10.24",
            "latest_label": "v2026.10.24"})
        self.assertIn("已是最新", text)

    def test_local_newer_and_missing_and_checking(self):
        newer = log_overlay.self_check_hint({
            "state": "local_newer", "local_label": "v2026.10.25",
            "latest_label": "v2026.10.24"})
        self.assertIn("线上 v2026.10.24", newer)

        missing = log_overlay.self_check_hint({
            "state": "remote_missing", "local_label": "v2026.10.24"})
        self.assertIn("线上未发布", missing)

        checking = log_overlay.self_check_hint({
            "state": "unknown", "checking": True,
            "local_label": "v2026.10.24"})
        self.assertIn("获取中", checking)

        failed = log_overlay.self_check_hint({
            "state": "unknown", "local_label": "v2026.10.24"})
        self.assertIn("获取失败", failed)

    def test_garbage_payload_falls_back_to_local(self):
        for payload in (None, {}, "bad", 42):
            with self.subTest(payload=payload):
                self.assertIn(version.version_label(),
                              log_overlay.self_check_hint(payload))


class SelfCheckHintStyleTests(unittest.TestCase):
    def test_red_bold_and_taller_than_the_body_text(self):
        self.assertEqual("#ff4d4f", log_overlay.SELF_CHECK_HINT_COLOR)
        self.assertEqual("bold", log_overlay.SELF_CHECK_HINT_FONT[2])
        self.assertGreaterEqual(log_overlay.SELF_CHECK_HINT_FONT[1], 10)
        # 比浮窗正文的 8pt 状态字更大；高度也要给提示留位置。
        self.assertGreater(log_overlay.SELF_CHECK_HINT_FONT[1], 8)
        self.assertGreater(log_overlay.WINDOW_HEIGHT, 772)

    def test_run_draws_the_hint_under_the_brand_row(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("hint_label", source)
        self.assertIn("SELF_CHECK_HINT_FONT", source)
        self.assertIn("SELF_CHECK_HINT_COLOR", source)
        self.assertIn("self_check_hint(None)", source)
        self.assertIn("self_check_hint(hint_info)", source)
        # 必须在品牌金线之后、自动化日志标题之前。
        self.assertLess(source.index("hint_label = tk.Label"),
                        source.index("# ---- header"))

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
