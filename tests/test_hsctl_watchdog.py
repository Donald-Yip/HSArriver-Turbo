# -*- coding: utf-8 -*-
"""hsctl 看门狗（计划任务 + ``serve start --auto`` 守卫）的离线测试。

看门狗要解决的问题：控制台（web_ui.py）就是常驻模式本体，它崩了没有任何东西
会拉起；夜里中断后能一直停到第二天。这里验证 hsctl 侧的接线：

* 隐藏启动器 .vbs 的内容与 UTF-16 编码（中文路径可用）；
* ``schtasks /create ... /sc MINUTE /mo 5 /rl HIGHEST`` 的参数拼装；
* 非管理员只打印管理员命令、**绝不**自己提权；
* 卸载 / 状态 JSON 字段；
* ``serve start --auto`` 在「上次是手动停止」时跳过、否则照常启动；
  普通 ``serve start`` 不看这个标记；
* ``serve stop`` 真正停掉后写一次手动停止标记（「本来就没在跑」不写）。

安全边界：所有 schtasks / taskkill 调用都被 mock；启动器和手动停止标记只写
``tempfile`` 临时目录，**绝不**碰真实 ``log/`` 或真实计划任务。
"""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import hsctl
from src.safety import resident_intent
from tests.test_hsctl import _free_port, run_cli


def _patch(testcase, target, attr, value):
    patcher = unittest.mock.patch.object(target, attr, value)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return patcher


class _TempDirMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hsctl_watchdog_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


# --------------------------------------------------------------- 启动器
class WatchdogLauncherTests(_TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.launcher = self.tmp / "hs_serve_watchdog.vbs"
        _patch(self, hsctl, "WATCHDOG_LAUNCHER", self.launcher)
        _patch(self, hsctl, "_python_for_task", lambda: r"C:\Python312\python.exe")

    def test_launcher_text_is_a_hidden_wscript_run(self):
        text = hsctl._watchdog_launcher_text()
        self.assertIn('Set sh = CreateObject("WScript.Shell")', text)
        self.assertIn(f'sh.CurrentDirectory = "{hsctl.ROOT}"', text)
        # 引号按 VBS 规则写成两层；命令本身带 --auto，保证看门狗不会把手动停止推翻。
        self.assertIn(r'"""C:\Python312\python.exe""', text)
        self.assertIn(str(hsctl.ROOT / "hsctl.py"), text)
        self.assertIn('serve start --auto --wait 120", 0, True', text)
        self.assertEqual(1, text.count('sh.Run "'), "启动器只该有一条 Run 命令")
        # 0 = 隐藏窗口：任务每分钟跑一次也不能在屏幕上闪黑框。
        self.assertIn(", 0, True", text)

    def test_launcher_is_utf16_with_bom_and_idempotent(self):
        path = hsctl._ensure_watchdog_launcher()
        self.assertEqual(self.launcher, path)
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xff\xfe"), "wscript 需要 UTF-16 BOM")
        self.assertEqual(hsctl._watchdog_launcher_text(), raw.decode("utf-16"))

        hsctl._ensure_watchdog_launcher()
        self.assertEqual(raw, self.launcher.read_bytes(), "重跑应生成同一份启动器")


# ------------------------------------------------------- install / uninstall
class WatchdogInstallTests(_TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.launcher = self.tmp / "hs_serve_watchdog.vbs"
        _patch(self, hsctl, "WATCHDOG_LAUNCHER", self.launcher)
        _patch(self, hsctl, "RESIDENT_INTENT_PATH", self.tmp / "resident_intent.json")
        self.calls = []

    def _fake_run(self, argv):
        self.calls.append(list(argv))
        return 0, ""

    def _states(self, *states):
        return unittest.mock.patch.object(
            hsctl, "_watchdog_state", side_effect=list(states)
        )

    def test_install_creates_minute_task_with_highest_privileges(self):
        with unittest.mock.patch.object(
            hsctl, "is_user_admin", return_value=True
        ), self._states(
            {"exists": False, "highest": None},
            {"exists": True, "highest": True},
        ), unittest.mock.patch.object(
            hsctl, "_run_quiet", side_effect=self._fake_run
        ):
            code, out, err = run_cli(["serve", "install-watchdog"])

        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.assertEqual(1, len(self.calls))
        argv = self.calls[0]
        self.assertEqual(
            ["schtasks", "/create", "/tn", hsctl.WATCHDOG_TASK_NAME],
            argv[:4],
        )
        self.assertEqual("/tr", argv[4])
        action = argv[5]
        self.assertIn("wscript.exe //B", action)
        self.assertIn(str(self.launcher), action)
        self.assertEqual(
            ["/sc", "MINUTE", "/mo", "5", "/rl", "HIGHEST", "/f"],
            argv[6:],
        )
        self.assertTrue(self.launcher.exists(), "创建任务前要先写出启动器")
        self.assertIn("已注册", out)
        self.assertIn("最高权限", out)
        self.assertIn(hsctl.WATCHDOG_TASK_NAME, out)

    def test_install_without_admin_prints_command_and_never_runs_schtasks(self):
        with unittest.mock.patch.object(
            hsctl, "is_user_admin", return_value=False
        ), self._states({"exists": False}), unittest.mock.patch.object(
            hsctl, "_run_quiet", side_effect=self._fake_run
        ):
            code, out, _ = run_cli(["serve", "install-watchdog"])

        self.assertEqual(hsctl.EXIT_API, code)
        self.assertEqual([], self.calls, "非管理员时绝不能真的去建任务")
        # 给用户的是「跑同一条 hsctl 命令」，不是需要嵌套引号的原始 schtasks
        # （PowerShell 会把 \" 拆散）。hsctl 建任务走 argv，零引号风险。
        self.assertIn("管理员", out)
        self.assertIn("serve install-watchdog", out)
        self.assertIn(str(hsctl.ROOT / "hsctl.py"), out)
        # PowerShell 直接粘贴以引号开头的命令会报「意外的标记」，必须带 & 调用符。
        self.assertIn('& "', out)
        self.assertNotIn("schtasks /create", out)

    def test_install_reports_existing_task_and_force_rebuilds(self):
        with self._states({"exists": True, "highest": True}), \
                unittest.mock.patch.object(
                    hsctl, "_run_quiet", side_effect=self._fake_run):
            code, out, _ = run_cli(["serve", "install-watchdog"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("已存在", out)
        self.assertEqual([], self.calls)

        with unittest.mock.patch.object(
            hsctl, "is_user_admin", return_value=True
        ), self._states(
            {"exists": True, "highest": False},
            {"exists": True, "highest": True},
        ), unittest.mock.patch.object(
            hsctl, "_run_quiet", side_effect=self._fake_run
        ):
            code, out, _ = run_cli(["serve", "install-watchdog", "--force"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertEqual(1, len(self.calls))
        self.assertIn("已注册", out)

    def test_install_fails_when_task_is_not_highest(self):
        with unittest.mock.patch.object(
            hsctl, "is_user_admin", return_value=True
        ), self._states(
            {"exists": False},
            {"exists": True, "highest": False},
        ), unittest.mock.patch.object(
            hsctl, "_run_quiet", side_effect=self._fake_run
        ):
            code, out, _ = run_cli(["serve", "install-watchdog"])

        self.assertEqual(hsctl.EXIT_API, code)
        self.assertIn("最高权限", out)
        self.assertIn("--force", out)

    def test_uninstall_deletes_or_reports_missing(self):
        with self._states({"exists": True}), \
                unittest.mock.patch.object(
                    hsctl, "_run_quiet", side_effect=self._fake_run):
            code, out, _ = run_cli(["serve", "uninstall-watchdog"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertEqual(
            [["schtasks", "/delete", "/tn", hsctl.WATCHDOG_TASK_NAME, "/f"]],
            self.calls,
        )
        self.assertIn("已删除", out)

        with self.subTest(exists=False):
            self.calls[:] = []
            with self._states({"exists": False}):
                code, out, _ = run_cli(["serve", "uninstall-watchdog"])
            self.assertEqual(hsctl.EXIT_OK, code)
            self.assertEqual([], self.calls)
            self.assertIn("不存在", out)


# --------------------------------------------------------------- 状态
class WatchdogStatusTests(_TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.launcher = self.tmp / "hs_serve_watchdog.vbs"
        self.intent_path = self.tmp / "resident_intent.json"
        _patch(self, hsctl, "WATCHDOG_LAUNCHER", self.launcher)
        _patch(self, hsctl, "RESIDENT_INTENT_PATH", self.intent_path)

    def _state(self, **overrides):
        state = {
            "name": hsctl.WATCHDOG_TASK_NAME,
            "exists": True,
            "highest": True,
            "action": 'C:\\Windows\\System32\\wscript.exe //B "C:\\x\\hs_serve_watchdog.vbs"',
            "actual_action": "C:\\Windows\\System32\\wscript.exe",
            "interval": hsctl.WATCHDOG_INTERVAL_MINUTES,
            "launcher": r"C:\x\hs_serve_watchdog.vbs",
            "manual_stop": True,
            "scope": "console",
            "autostart_blocked": True,
            "reason": "hsctl serve stop",
            "at": "2026-11-01T03:10:00",
            "is_admin_now": False,
            "query": "",
        }
        state.update(overrides)
        return state

    def test_status_json_has_the_fields_agents_need(self):
        state = self._state()
        with unittest.mock.patch.object(hsctl, "_watchdog_state",
                                        return_value=state):
            code, out, _ = run_cli(["--json", "serve", "watchdog-status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        payload = json.loads(out)
        for key in ("exists", "highest", "action", "interval",
                    "manual_stop", "scope", "autostart_blocked",
                    "reason", "at"):
            self.assertIn(key, payload, f"缺字段：{key}")
            self.assertEqual(state[key], payload[key], f"字段值不对：{key}")

    def test_status_text_reports_manual_exit_and_registration(self):
        with unittest.mock.patch.object(hsctl, "_watchdog_state",
                                        return_value=self._state()):
            code, out, _ = run_cli(["serve", "watchdog-status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        for token in (hsctl.WATCHDOG_TASK_NAME, "已注册：是", "以最高权限运行：是",
                      "5 分钟", "手动退出控制台", "hsctl serve stop",
                      "2026-11-01T03:10:00", "不会自动拉起"):
            self.assertIn(token, out)
        # 完整动作里带 vbs 路径（XML 只取得到 <Command>，不能只打印它）。
        self.assertIn("hs_serve_watchdog.vbs", out)

    def test_status_text_distinguishes_stopping_automation_only(self):
        # scope=automation：只是停了自动化，控制台崩了看门狗仍会拉起。
        state = self._state(scope="automation", autostart_blocked=False,
                            reason="用户停止自动化")
        with unittest.mock.patch.object(hsctl, "_watchdog_state",
                                        return_value=state):
            code, out, _ = run_cli(["serve", "watchdog-status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("手动停止自动化", out)
        self.assertIn("仍会拉起", out)
        self.assertNotIn("不会自动拉起", out)

    def test_status_for_missing_task_prints_admin_hint(self):
        state = self._state(exists=False, highest=None, manual_stop=False,
                            scope="", autostart_blocked=False,
                            reason="", at="")
        with unittest.mock.patch.object(hsctl, "_watchdog_state",
                                        return_value=state):
            code, out, _ = run_cli(["serve", "watchdog-status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("已注册：否", out)
        self.assertIn("serve install-watchdog", out)
        self.assertNotIn("schtasks /create", out)

    def test_intent_summary_reads_the_marker_file(self):
        resident_intent.record_manual_stop(
            "页面点了停止", at="2026-11-01T03:00:00", path=self.intent_path
        )
        summary = hsctl._resident_intent_summary()
        self.assertTrue(summary["manual_stop"])
        self.assertEqual("manual", summary["kind"])
        self.assertEqual("console", summary["scope"])
        self.assertEqual("页面点了停止", summary["reason"])
        self.assertEqual("2026-11-01T03:00:00", summary["at"])


# ----------------------------------------------------- serve start --auto
class ServeAutoGuardTests(_TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.intent_path = self.tmp / "resident_intent.json"
        _patch(self, hsctl, "RESIDENT_INTENT_PATH", self.intent_path)
        _patch(self, hsctl.Ctx, "_probe", lambda *args, **kwargs: False)
        self.direct = unittest.mock.Mock(return_value=hsctl.EXIT_OK)
        _patch(self, hsctl, "_serve_start_direct", self.direct)

    def _argv(self, *extra):
        return ["--port", str(_free_port()), "serve", "start", *extra]

    def test_auto_skips_start_when_last_exit_was_manual(self):
        resident_intent.record_manual_stop(
            "hsctl serve stop", at="2026-11-01T03:05:00", path=self.intent_path
        )
        code, out, err = run_cli(self._argv("--auto"))
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.direct.assert_not_called()
        for token in ("手动退出控制台", "看门狗本次不自动启动",
                      "hsctl serve stop", "2026-11-01T03:05:00"):
            self.assertIn(token, out)

    def test_auto_starts_after_manual_automation_stop(self):
        # 只是停了自动化（scope=automation）：控制台崩了看门狗仍要把它拉回来，
        # 常驻自己按 manual_stop 保持暂停等手动开始。
        resident_intent.record_manual_stop(
            "用户停止自动化", scope="automation", path=self.intent_path
        )
        code, _, err = run_cli(self._argv("--auto"))
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.direct.assert_called_once()

    def test_auto_starts_normally_without_marker(self):
        code, _, err = run_cli(self._argv("--auto"))
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.direct.assert_called_once()

    def test_auto_starts_after_error_marker(self):
        # 上次是异常退出（manual_stop=false）→ 看门狗应该照常拉起。
        self.intent_path.write_text(
            json.dumps({"version": 1, "manual_stop": False,
                        "reason": "crash", "at": "2026-11-01T04:00:00"}),
            encoding="utf8",
        )
        code, _, err = run_cli(self._argv("--auto"))
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.direct.assert_called_once()

    def test_manual_start_ignores_the_marker(self):
        # 普通 `serve start` 是人在操作：不看手动停止标记。
        resident_intent.record_manual_stop(
            "hsctl serve stop", path=self.intent_path
        )
        code, _, err = run_cli(self._argv())
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.direct.assert_called_once()


# ------------------------------------------------ serve stop 写手动标记
class ServeStopIntentTests(_TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.intent_path = self.tmp / "resident_intent.json"
        self.port = _free_port()
        _patch(self, hsctl, "RESIDENT_INTENT_PATH", self.intent_path)
        _patch(self, hsctl, "PORT_FILE", self.tmp / "ui_port.txt")
        _patch(self, hsctl, "PID_FILE", self.tmp / "ui_server.pid")
        _patch(
            self,
            hsctl,
            "read_port_file",
            lambda: {"port": self.port, "host": "127.0.0.1",
                     "pid": 4321, "token": "test-token"},
        )
        _patch(self, hsctl, "_pid_image_name", lambda pid: "python.exe")
        _patch(self, hsctl, "_pid_alive", lambda pid: True)
        self.kill = unittest.mock.Mock(return_value=(True, "ok"))
        _patch(self, hsctl, "_kill_pid", self.kill)
        self.self_stop = unittest.mock.Mock(return_value=False)
        _patch(self, hsctl, "_serve_self_stop", self.self_stop)

    def _probes(self, *results):
        remaining = list(results)

        def fake(probe_self, base):
            return remaining.pop(0) if remaining else False

        _patch(self, hsctl.Ctx, "_probe", fake)

    def _stop(self, *extra):
        return run_cli(["--port", str(self.port), "serve", "stop", *extra])

    def _summary(self):
        return resident_intent.summary(self.intent_path)

    def test_self_stop_records_manual_stop(self):
        self._probes(True, True)  # require_alive + 初始探活
        self.self_stop.return_value = True
        code, out, err = self._stop()
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.assertIn("自停通道", out)
        self.kill.assert_not_called()
        summary = self._summary()
        self.assertTrue(summary["manual_stop"])
        self.assertEqual("console", summary["scope"], "退出控制台才算挡住看门狗")
        self.assertEqual("hsctl serve stop", summary["reason"])
        self.assertTrue(summary["at"], "标记里应带时间")

    def test_kill_records_manual_stop(self):
        self._probes(True, True, False)  # require_alive + 初始探活 + 杀完的等待
        code, out, err = self._stop()
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.kill.assert_called_once_with(4321)
        self.assertIn("已停止", out)
        self.assertTrue(self._summary()["manual_stop"])

    def test_not_running_does_not_record_manual_stop(self):
        self._probes(False)  # require_alive 就失败 → 没在运行
        code, out, err = self._stop()
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.assertIn("没在运行", out)
        self.kill.assert_not_called()
        self.assertFalse(
            self.intent_path.exists(),
            "「本来就没在跑」不是手动停止，不该写标记（否则看门狗永远不启动）",
        )

    def test_kill_failure_does_not_record_manual_stop(self):
        self._probes(True, True)
        self.kill.return_value = (False, "拒绝访问")
        code, _, err = self._stop()
        self.assertEqual(hsctl.EXIT_API, code)
        self.assertIn("失败", err)
        self.assertFalse(self.intent_path.exists())

    def test_pid_already_gone_does_not_record_manual_stop(self):
        # pid 刚好自己没了（崩溃/被回收）：不是用户手动停的，不能挡住看门狗。
        self._probes(True, True, False)
        self.kill.return_value = (False, "not found")
        with unittest.mock.patch.object(hsctl, "_pid_alive", lambda pid: False):
            code, out, err = self._stop()
        self.assertEqual(hsctl.EXIT_OK, code, err)
        self.assertIn("已停止", out)
        self.assertFalse(self.intent_path.exists())

    def test_restart_does_not_record_manual_stop(self):
        stop = unittest.mock.Mock(return_value=hsctl.EXIT_OK)
        start = unittest.mock.Mock(return_value=hsctl.EXIT_OK)
        with unittest.mock.patch.object(hsctl, "_serve_stop", stop), \
                unittest.mock.patch.object(hsctl, "_serve_start", start), \
                unittest.mock.patch.object(hsctl.time, "sleep"):
            code, _, _ = run_cli(["serve", "restart"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIs(False, stop.call_args.kwargs.get("record_manual"),
                      "重启不是手动停止，不能给看门狗留下「别拉起」的标记")

    def test_restart_stop_is_internal_and_keeps_intent_clean(self):
        # restart 的停自动化必须带 internal 标记（不写手动标记、不毁定时计划），
        # 自停通道也要带 keep_intent=True。
        posts = []

        class _Ctx:
            base = "http://127.0.0.1:1"

            def _probe(self, base):
                return True

            def post(self, path, body):
                posts.append((path, dict(body or {})))
                return {"ok": True}

            def get(self, path):
                return {"running": False}

        args = unittest.mock.Mock(hard=False, force=False)
        self.self_stop.return_value = True
        import contextlib
        import io as _io

        with contextlib.redirect_stdout(_io.StringIO()) as buf:
            code = hsctl._serve_stop(args, _Ctx(), record_manual=False)
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("自停通道", buf.getvalue())
        self.assertEqual("/api/stop", posts[0][0])
        self.assertTrue(posts[0][1].get("_resident_internal"))
        self.assertTrue(posts[0][1].get("keep_schedule"))
        self.assertEqual(
            True, self.self_stop.call_args.kwargs.get("keep_intent")
        )
        self.assertFalse(
            self.intent_path.exists(),
            "restart 不能留「手动停止」标记，否则重启后常驻一直暂停",
        )


# --------------------------------------------------- 参数表 / 任务名兼容
class WatchdogParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = hsctl.build_parser()

    def test_install_watchdog_parses_force(self):
        ns = self.parser.parse_args(["serve", "install-watchdog", "--force"])
        self.assertEqual("install-watchdog", ns.action)
        self.assertTrue(ns.force)
        self.assertIs(hsctl.cmd_serve, ns.handler)

    def test_uninstall_and_status_parse(self):
        ns = self.parser.parse_args(["serve", "uninstall-watchdog"])
        self.assertEqual("uninstall-watchdog", ns.action)
        ns = self.parser.parse_args(["--json", "serve", "watchdog-status"])
        self.assertEqual("watchdog-status", ns.action)
        self.assertTrue(ns.json)

    def test_start_auto_flag_defaults_to_false(self):
        self.assertFalse(self.parser.parse_args(["serve", "start"]).auto)
        self.assertTrue(self.parser.parse_args(["serve", "start", "--auto"]).auto)


class TaskXmlNameTests(unittest.TestCase):
    """``_task_xml(name=...)``：看门狗任务复用查询逻辑，旧的默认调用不能变。"""

    def _run_with(self, name=None):
        proc = unittest.mock.Mock(
            returncode=0,
            stdout=b"<Task><Actions><Exec><Command>wscript.exe</Command>"
                   b"</Exec></Actions></Task>",
            stderr=b"",
        )
        with unittest.mock.patch.object(hsctl.subprocess, "run",
                                        return_value=proc) as run:
            xml = hsctl._task_xml() if name is None else hsctl._task_xml(name)
        return xml, run.call_args.args[0]

    def test_explicit_task_name_is_queried(self):
        xml, argv = self._run_with(hsctl.WATCHDOG_TASK_NAME)
        self.assertIn("<Command>wscript.exe</Command>", xml)
        self.assertEqual(
            ["schtasks", "/query", "/tn", hsctl.WATCHDOG_TASK_NAME, "/xml"],
            argv,
        )

    def test_default_still_queries_the_serve_task(self):
        _, argv = self._run_with()
        self.assertEqual(
            ["schtasks", "/query", "/tn", hsctl.TASK_NAME, "/xml"], argv
        )


if __name__ == "__main__":
    unittest.main()
