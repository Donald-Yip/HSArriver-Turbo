# -*- coding: utf-8 -*-
"""环境自检卡片可收起：按钮与明细在 body 里，结论行/徽章必须一直看得见。"""

import unittest
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parent.parent / "web" / "index.html"


class SelfcheckCollapsePageTests(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf8")

    def test_toggle_body_wraps_the_details(self):
        body = self.html.split('id="selfcheckBody"', 1)[1].split("</section>", 1)[0]

        self.assertIn('id="btnSelfcheckToggle"', self.html)
        self.assertIn('id="selfcheckList"', body)
        self.assertIn("Python 必须是 3.12", body)

    def test_summary_stays_visible_when_collapsed(self):
        """收起只藏明细；通过/待修的结论行与徽章必须一直看得见。"""
        head = self.html.split('id="selfcheckBody"', 1)[0]

        self.assertIn('id="selfcheckSummary"', head)
        self.assertIn('id="selfcheckBadge"', head)


if __name__ == "__main__":
    unittest.main()
