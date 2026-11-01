# -*- coding: utf-8 -*-
"""命令行管理员告警横幅：非管理员时放大 + 加粗 + **重复三遍**。

用户口径（2026-10-26）：「把命令行的『当前不是管理员权限，自动化鼠标/键盘
操作可能失效』放大加粗重复三遍」。终端里改不了字号，所以实现成整框 + 块字符
边框 + ANSI 加粗亮色，并在不支持 ANSI 的终端退回纯文本（不能把转义序列打成
乱码），文案重复 3 遍。
"""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import web_ui


def render(ansi):
    stream = io.StringIO()
    web_ui._print_admin_warning(stream=stream, ansi=ansi)
    return stream.getvalue()


class AdminWarningBannerTests(unittest.TestCase):
    def setUp(self):
        # main() 会走守护启动/停止收尾；测试里必须隔离，避免留下真 RESIDENT 或
        # 「手动停止」全局状态（R9 复审 blocking：会让后续同步假线程测试死锁）。
        self._saved = (web_ui.RESIDENT, dict(web_ui._RESIDENT_INTENT_STATE))

    def tearDown(self):
        web_ui.RESIDENT, saved_intent = self._saved
        web_ui._RESIDENT_INTENT_STATE = saved_intent

    def test_plain_banner_repeats_warning_and_hint_three_times(self):
        output = render(ansi=False)

        self.assertEqual(3, output.count(web_ui.ADMIN_WARNING_TITLE))
        self.assertEqual(3, output.count(web_ui.ADMIN_WARNING_HINT))
        self.assertIn("管理员身份", output)
        self.assertIn("python web_ui.py", output)

    def test_plain_terminal_gets_no_escape_sequences(self):
        output = render(ansi=False)

        self.assertNotIn("\x1b[", output)
        self.assertIn("█", output)  # 块字符整框：纯文本下也醒目

    def test_ansi_terminal_gets_bold_bright_colors_and_configurable_repeat(self):
        output = render(ansi=True)

        self.assertIn("\x1b[1;4;91m", output)   # 加粗 + 下划线 + 亮红
        self.assertIn("\x1b[0m", output)
        self.assertEqual(3, output.count(web_ui.ADMIN_WARNING_TITLE))

        stream = io.StringIO()
        web_ui._print_admin_warning(stream=stream, ansi=False, repeat=1)
        self.assertEqual(1, stream.getvalue().count(web_ui.ADMIN_WARNING_TITLE))

    def test_no_ansi_when_output_is_not_a_terminal(self):
        """重定向到文件/管道时不能发转义序列（否则日志里全是乱码）。"""
        self.assertFalse(web_ui._enable_console_ansi(io.StringIO()))

    def test_main_outputs_the_banner_only_for_non_admins(self):
        class _NoopThread:
            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                return None

        server = SimpleNamespace(serve_forever=lambda: None)

        def run_main(is_admin):
            output = io.StringIO()
            with (
                patch.object(web_ui, "IS_ADMIN", is_admin),
                patch.object(web_ui.os, "chdir"),
                patch.object(web_ui.threading, "Thread", _NoopThread),
                # 不真的建守护线程/不写「手动停止」标记（KeyboardInterrupt 分支）。
                patch.object(web_ui, "_bind_resident"),
                patch.object(web_ui, "_resident_record_manual_stop"),
                patch.object(web_ui, "_register_hotkey"),
                patch.object(web_ui, "load_config", return_value={}),
                patch.object(web_ui, "_apply_constants"),
                patch.object(web_ui, "_boot_resume_schedule"),
                patch.object(web_ui, "start_selfcheck_async"),
                patch.object(web_ui.update_check, "start_periodic"),
                patch.object(web_ui, "_ControlServer", return_value=server),
                patch.object(web_ui, "_write_port_file"),
                patch.object(web_ui, "_clear_port_file"),
                patch.object(web_ui.time, "sleep", side_effect=KeyboardInterrupt),
                patch.object(web_ui.sys, "argv", ["web_ui.py", "--no-browser"]),
                redirect_stdout(output),
            ):
                web_ui.main()
            return output.getvalue()

        non_admin_output = run_main(False)
        admin_output = run_main(True)

        self.assertIn(web_ui.ADMIN_WARNING_TITLE, non_admin_output)
        self.assertNotIn(web_ui.ADMIN_WARNING_TITLE, admin_output)


if __name__ == "__main__":
    unittest.main()
