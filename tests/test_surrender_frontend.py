"""前端往返校验：用 node 把 web/index.html 的脚本跑进假 DOM。

检查两件事：
  1. 嵌套的「且 / 或 / 非」配置载入表单后，再收集回来**结构不变**（不会被压平、
     `turn` / `card_type` / 多卡数组这些表单表达不了的字段也不会丢）；
  2. 卡组卡片收起状态记在 localStorage、重载后仍收起，且收起时保存不丢条件。

没有 node 解释器时跳过（不算失败）。
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "frontend_surrender_roundtrip.js"

_NODE_CANDIDATES = [
    shutil.which("node"),
    r"C:\Users\DonaldYip\.workbuddy\binaries\node\versions\22.22.2-6\node.exe",
    r"D:\nodejs\node.exe",
]


def _node():
    for path in _NODE_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


class FrontendRoundTripTests(unittest.TestCase):
    def test_nested_config_survives_the_form(self):
        node = _node()
        if node is None:
            self.skipTest("没有 node，跳过前端脚本校验")
        proc = subprocess.run([node, str(SCRIPT)], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=60)
        output = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn("OK: A 且 (B 或 C)", output)
        self.assertIn("OK: 非 (B 或 C)", output)
        self.assertIn("OK: 收起状态下保存不丢条件", output)


if __name__ == "__main__":
    unittest.main()
