# -*- coding: utf-8 -*-
"""环境自检卡片可收起：按钮、本地记忆、失败自动展开。"""

import re
import unittest
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "web" / "index.html"


class SelfcheckCollapsePageTests(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf8")

    def test_toggle_button_and_body_exist(self):
        self.assertIn('id="btnSelfcheckToggle"', self.html)
        self.assertIn('id="selfcheckBody"', self.html)

    def test_body_wraps_the_list_and_the_explanation(self):
        body = self.html.split('id="selfcheckBody"', 1)[1].split("</section>", 1)[0]

        self.assertIn('id="selfcheckList"', body)
        self.assertIn("Python 必须是 3.12", body)

    def test_summary_stays_visible_when_collapsed(self):
        """收起只藏明细；通过/待修的结论行与徽章必须一直看得见。"""
        head = self.html.split('id="selfcheckBody"', 1)[0]

        self.assertIn('id="selfcheckSummary"', head)
        self.assertIn('id="selfcheckBadge"', head)


class ScriptHelpersTests(unittest.TestCase):
    def setUp(self):
        self.script = re.search(r"<script>(.*?)</script>",
                                INDEX_HTML.read_text(encoding="utf8"),
                                re.S).group(1)

if __name__ == "__main__":
    unittest.main()
