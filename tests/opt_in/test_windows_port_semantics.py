"""Windows-only stdlib HTTP server port semantics used by the control plane."""

from __future__ import annotations

import http.server
import os
import unittest

import web_ui


class WindowsPortSemanticsTests(unittest.TestCase):
    def test_plain_httpserver_would_have_shared_it(self) -> None:
        if os.name != "nt":
            self.skipTest("这个语义差异只在 Windows 上成立")

        first = http.server.ThreadingHTTPServer(("127.0.0.1", 0), web_ui.Handler)
        self.addCleanup(first.server_close)
        port = int(first.server_address[1])
        second = http.server.ThreadingHTTPServer(("127.0.0.1", port), web_ui.Handler)
        second.server_close()
