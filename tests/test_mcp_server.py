# -*- coding: utf-8 -*-
"""hsctl 的 MCP server 冒烟测试（stdio）。

用**子进程**起 `mcp_server.py`，走一遍 initialize → tools/list → tools/call：
  * 不用 `import mcp_server`，因为它导入 mcp 前会把仓库根从 sys.path 摘掉
    （仓库根的 click.py 会遮蔽真正的 click），那会污染测试进程的 sys.path；
  * 顺带验证 server 名字、协议、工具名与「doctor 能跑通」。

没装 mcp（requirements.txt 里有，但可能没同步）时整文件 skip。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp_server.py"

EXPECTED_TOOLS = {
    "doctor",
    "selfcheck",
    "status",
    "log",
    "ui",
    "screenshot",
    "start",
    "stop",
    "resume",
    "prepare",
    "cancel",
    "enter_battle",
    "mode",
    "deck",
    "calibrate",
    "restart_hearthstone",
    "config_show",
    "config_set",
    "delays_show",
    "delays_set",
    "hsctl",
}


def _mcp_available() -> bool:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import mcp.server.mcpserver"],
            cwd=tempfile.gettempdir(),  # 别在仓库根跑：那里有 click.py 会遮蔽真 click
            capture_output=True,
            timeout=60,
        )
    except Exception:
        return False
    return proc.returncode == 0


@unittest.skipUnless(_mcp_available(), "未安装 mcp（见 requirements.txt）")
class McpServerSmokeTests(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.addCleanup(self._stop)

    def _stop(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _wait(self, want_id, timeout=90.0):
        end = time.time() + timeout
        while time.time() < end:
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == want_id:
                return message
        return None

    def _handshake(self):
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        init = self._wait(1)
        self.assertIsNotNone(init, "initialize 没回")
        self.assertEqual("hsctl", init["result"]["serverInfo"]["name"])
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_lists_the_curated_tools(self):
        self._handshake()
        self._send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listing = self._wait(2)
        self.assertIsNotNone(listing, "tools/list 没回")
        names = {tool["name"] for tool in listing["result"]["tools"]}
        self.assertTrue(EXPECTED_TOOLS <= names, f"缺少工具：{EXPECTED_TOOLS - names}")

    def test_doctor_tool_runs_hsctl(self):
        self._handshake()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "doctor", "arguments": {}},
            }
        )
        call = self._wait(3, timeout=180.0)
        self.assertIsNotNone(call, "doctor 没回")
        self.assertFalse(call["result"].get("isError"))
        text = call["result"]["content"][0]["text"]
        payload = json.loads(text)
        self.assertIn("exit_code", payload)
        self.assertIn("data", payload)


if __name__ == "__main__":
    unittest.main()
