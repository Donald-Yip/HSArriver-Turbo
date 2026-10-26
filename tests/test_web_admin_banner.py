# -*- coding: utf-8 -*-
"""命令行管理员告警横幅：非管理员时放大 + 加粗 + **重复三遍**。

用户口径（2026-10-26）：「把命令行的『当前不是管理员权限，自动化鼠标/键盘
操作可能失效』放大加粗重复三遍」。终端里改不了字号，所以实现成整框 + 块字符
边框 + ANSI 加粗亮色，并在不支持 ANSI 的终端退回纯文本（不能把转义序列打成
乱码），文案重复 3 遍。
"""

import inspect
import io
import unittest

import web_ui


def render(ansi):
    stream = io.StringIO()
    web_ui._print_admin_warning(stream=stream, ansi=ansi)
    return stream.getvalue()


class AdminWarningBannerTests(unittest.TestCase):
    def test_message_is_printed_three_times(self):
        output = render(ansi=False)

        self.assertEqual(3, output.count(web_ui.ADMIN_WARNING_TITLE))

    def test_banner_carries_the_fix_hint(self):
        output = render(ansi=False)

        self.assertEqual(3, output.count(web_ui.ADMIN_WARNING_HINT))
        self.assertIn("管理员身份", output)
        self.assertIn("python web_ui.py", output)

    def test_plain_terminal_gets_no_escape_sequences(self):
        output = render(ansi=False)

        self.assertNotIn("\x1b[", output)
        self.assertIn("█", output)  # 块字符整框：纯文本下也醒目

    def test_ansi_terminal_gets_bold_and_bright_colors(self):
        output = render(ansi=True)

        self.assertIn("\x1b[1;4;91m", output)   # 加粗 + 下划线 + 亮红
        self.assertIn("\x1b[0m", output)
        self.assertEqual(3, output.count(web_ui.ADMIN_WARNING_TITLE))

    def test_repeat_count_is_configurable(self):
        stream = io.StringIO()

        web_ui._print_admin_warning(stream=stream, ansi=False, repeat=1)

        self.assertEqual(1, stream.getvalue().count(web_ui.ADMIN_WARNING_TITLE))

    def test_no_ansi_when_output_is_not_a_terminal(self):
        """重定向到文件/管道时不能发转义序列（否则日志里全是乱码）。"""
        self.assertFalse(web_ui._enable_console_ansi(io.StringIO()))

    def test_main_prints_the_banner_only_for_non_admins(self):
        source = inspect.getsource(web_ui.main)

        self.assertIn("if not IS_ADMIN:", source)
        self.assertIn("_print_admin_warning()", source)
        # 旧写法（拆成两行 print，且"建议以管理员身份"无条件打印）已移除。
        self.assertNotIn("当前不是管理员权限，自动化鼠标/键盘操作可能失效，", source)


if __name__ == "__main__":
    unittest.main()
