# -*- coding: utf-8 -*-
"""解释器自配置：探测 / 校验 / 保存 / 重入，以及"模板里不许出现本机路径"。

背景：项目必须跑在 Python 3.12 + requirements.txt 的依赖上，而每台机器、每个
agent 自带的 python 都不同。所以仓库里只放模板（`env.example.json`，只有占位符），
真正的路径由每台机器自己保存到本机专属的 `log/env.json`（log/ 不入库）。
这里把这些规则锁住：探测候选、校验（只 find_spec 不 import）、保存、以及
"别的 python 跑 hsctl 会自动重入到合格解释器"。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import hsctl


def make_temp_dir() -> Path:
    """普通权限临时目录（mkdtemp 的 0700 在受限环境里写不进去）。"""
    path = Path(tempfile.gettempdir()) / f"hs_env_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fake_probe(ok_paths, missing=None):
    """构造一个可注入的探针：只有 ok_paths 里的解释器算合格。"""
    missing = missing or {}

    def probe(path):
        path = str(path)
        if path in ok_paths:
            return {"ok": True, "executable": path, "version": (3, 12, 14),
                    "missing": [], "reason": "ok"}
        return {"ok": False, "executable": path, "version": (3, 13, 5),
                "missing": list(missing.get(path, ["win32gui"])),
                "reason": "故意不合格（测试）"}

    return probe


def patched_exists():
    """把 ``Path.exists`` 一律当 True：让测试能用假路径（不必真造可执行文件）。"""
    return mock.patch.object(Path, "exists", return_value=True)


class EnvFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "env.json"
        patches = [mock.patch.object(hsctl, "ENV_FILE", self.path),
                   mock.patch.dict(os.environ, {}, clear=False)]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        os.environ.pop("HS_PYTHON", None)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual({}, hsctl._read_env_file())

    def test_write_then_read_roundtrip(self):
        self.assertTrue(hsctl._write_env_file({"interpreter": "X:/py.exe"}))

        self.assertEqual("X:/py.exe", hsctl._read_env_file()["interpreter"])

    def test_broken_json_reads_as_empty(self):
        self.path.write_text("{ not json", encoding="utf8")

        self.assertEqual({}, hsctl._read_env_file())

    def test_pin_saves_and_reports_source(self):
        with mock.patch.object(hsctl, "verify_python",
                               side_effect=fake_probe({"X:/py.exe"})), patched_exists():
            self.assertTrue(hsctl._persist_env("X:/py.exe", "hsctl env --pin"))
            self.assertEqual("X:/py.exe", hsctl._read_env_file()["interpreter"])
            self.assertEqual("file", hsctl.env_pin_source())

    def test_env_var_wins_over_file(self):
        hsctl._write_env_file({"interpreter": "X:/py.exe"})
        with mock.patch.dict(os.environ, {"HS_PYTHON": "Y:/py.exe"}):
            self.assertEqual("env", hsctl.env_pin_source())


class VerifyPythonTests(unittest.TestCase):
    def test_missing_file_is_rejected_without_running_anything(self):
        def boom(*args, **kwargs):
            raise AssertionError("不该起子进程")

        record = hsctl.verify_python("X:/nope/python.exe", runner=boom)

        self.assertFalse(record["ok"])
        self.assertIn("不存在", record["reason"])

    def test_ok_probe(self):
        out = json.dumps({"version": [3, 12, 14], "executable": "X:/py.exe",
                          "missing": []})

        with patched_exists():
            record = hsctl.verify_python("X:/py.exe", runner=lambda a, t: (0, out))

        self.assertTrue(record["ok"], record)
        self.assertEqual((3, 12, 14), record["version"])

    def test_wrong_version_is_rejected(self):
        out = json.dumps({"version": [3, 13, 5], "executable": "X:/py.exe",
                          "missing": []})

        with patched_exists():
            record = hsctl.verify_python("X:/py.exe", runner=lambda a, t: (0, out))

        self.assertFalse(record["ok"])
        self.assertIn("3.13", record["reason"])

    def test_missing_dependencies_name_the_pip_packages(self):
        out = json.dumps({"version": [3, 12, 1], "executable": "X:/py.exe",
                          "missing": ["win32gui", "cv2"]})

        with patched_exists():
            record = hsctl.verify_python("X:/py.exe", runner=lambda a, t: (0, out))

        self.assertFalse(record["ok"])
        self.assertIn("pywin32", record["reason"])
        self.assertIn("opencv_python_headless", record["reason"])

    def test_unparseable_output_is_rejected(self):
        with patched_exists():
            record = hsctl.verify_python("X:/py.exe", runner=lambda a, t: (0, "junk"))

        self.assertFalse(record["ok"])

    def test_probe_code_only_uses_the_standard_library(self):
        """探针脚本不能 import 重依赖（否则校验会把 paddle 拉起来）。"""
        self.assertNotIn("import cv2", hsctl._PROBE_CODE)
        self.assertIn("find_spec", hsctl._PROBE_CODE)


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patches = [mock.patch.object(hsctl, "ENV_FILE", self.tmp / "env.json"),
                   mock.patch.object(hsctl, "ROOT", self.tmp),
                   mock.patch.object(hsctl, "_where_pythons", return_value=[])]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        for key in ("HS_PYTHON", "VIRTUAL_ENV", "CONDA_PREFIX",
                    "LOCALAPPDATA"):
            os.environ.pop(key, None)

    def _touch(self, name) -> str:
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf8")
        return str(path)

    def test_pythonw_is_normalized_to_python(self):
        self._touch("w/python.exe")
        self.assertEqual(str(self.tmp / "w" / "python.exe"),
                         hsctl._normalize_python(str(self.tmp / "w" / "pythonw.exe")))

    def test_conda_prefix_env_var_is_a_candidate(self):
        path = self._touch("envs/HTL/Scripts/python.exe")
        with mock.patch.dict(os.environ, {"CONDA_PREFIX": str(self.tmp / "envs" / "HTL")}):
            candidates = hsctl.discover_candidates()

        self.assertIn(path, candidates)

    def test_repo_venv_is_a_candidate(self):
        path = self._touch(".venv/Scripts/python.exe")

        self.assertIn(path, hsctl.discover_candidates())

    def test_duplicates_are_removed_case_insensitively(self):
        path = self._touch(".venv/Scripts/python.exe")
        with mock.patch.dict(os.environ, {"VIRTUAL_ENV": str(self.tmp / ".venv")}):
            candidates = [c.casefold() for c in hsctl.discover_candidates()]

        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertIn(path.casefold(), candidates)

    def test_current_interpreter_is_not_a_candidate(self):
        current = hsctl._normalize_python(os.sys.executable)
        with mock.patch.object(hsctl, "_where_pythons", return_value=[current]):
            self.assertNotIn(current, hsctl.discover_candidates())


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patches = [mock.patch.object(hsctl, "ENV_FILE", self.tmp / "env.json"),
                   mock.patch.object(hsctl, "ROOT", self.tmp),
                   mock.patch.object(hsctl, "_where_pythons", return_value=[])]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        os.environ.pop("HS_PYTHON", None)

    def test_pinned_file_wins_when_usable(self):
        hsctl._write_env_file({"interpreter": "X:/py.exe"})

        resolved = hsctl.resolve_interpreter(probe=fake_probe({"X:/py.exe"}))

        self.assertEqual("X:/py.exe", resolved["path"])
        self.assertEqual("file", resolved["source"])

    def test_candidates_are_probed_in_order(self):
        good = str(self.tmp / ".venv" / "Scripts" / "python.exe")
        Path(good).parent.mkdir(parents=True, exist_ok=True)
        Path(good).write_text("", encoding="utf8")
        bad = str(self.tmp / "venv" / "Scripts" / "python.exe")
        Path(bad).parent.mkdir(parents=True, exist_ok=True)
        Path(bad).write_text("", encoding="utf8")

        resolved = hsctl.resolve_interpreter(probe=fake_probe({good}))

        self.assertEqual(good, resolved["path"])
        self.assertEqual("candidate", resolved["source"])

    def test_no_usable_interpreter_returns_empty_path(self):
        resolved = hsctl.resolve_interpreter(probe=fake_probe(set()))

        self.assertEqual("", resolved["path"])
        self.assertEqual("none", resolved["source"])


class EnsureInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patches = [mock.patch.object(hsctl, "ENV_FILE", self.tmp / "env.json"),
                   mock.patch.object(hsctl, "ROOT", self.tmp),
                   mock.patch.object(hsctl, "_where_pythons", return_value=[])]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        for key in ("HS_PYTHON", "HSCTL_NO_PROXY", hsctl.REEXEC_MARKER):
            os.environ.pop(key, None)

    def test_ok_current_interpreter_does_not_reexec(self):
        with mock.patch.object(hsctl.os, "execve") as execve:
            outcome = hsctl.ensure_interpreter(
                probe=fake_probe({hsctl._normalize_python(os.sys.executable)}))

        self.assertFalse(outcome["changed"])
        execve.assert_not_called()

    def test_no_proxy_escape_hatch(self):
        with mock.patch.dict(os.environ, {"HSCTL_NO_PROXY": "1"}), \
                mock.patch.object(hsctl.os, "execve") as execve:
            outcome = hsctl.ensure_interpreter(probe=fake_probe(set()))

        self.assertFalse(outcome["changed"])
        self.assertIn("HSCTL_NO_PROXY", outcome["reason"])
        execve.assert_not_called()

    def test_reexecs_into_the_pinned_interpreter_and_saves_it(self):
        """本机已固定别的解释器时：重入过去，并把固定值留在 log/env.json。"""
        hsctl._write_env_file({"interpreter": "X:/py.exe"})
        with patched_exists(), mock.patch.object(hsctl.os, "execve") as execve:
            hsctl.ensure_interpreter(probe=fake_probe({"X:/py.exe"}))

        # execve 成功不会返回（测试里被 mock 成"返回"）；断言它被正确调用。
        self.assertTrue(execve.called)
        argv = execve.call_args[0][1]
        self.assertEqual("X:/py.exe", argv[0])
        self.assertTrue(str(argv[1]).endswith("hsctl.py"))
        self.assertEqual("X:/py.exe",
                         hsctl._read_env_file()["interpreter"])

    def test_auto_pins_the_first_ok_candidate(self):
        """当前解释器不合格、只探到一个合格候选时：自动保存它。"""
        good = str(self.tmp / ".venv" / "Scripts" / "python.exe")
        Path(good).parent.mkdir(parents=True, exist_ok=True)
        Path(good).write_text("", encoding="utf8")
        with patched_exists(), mock.patch.object(hsctl.os, "execve") as execve:
            hsctl.ensure_interpreter(probe=fake_probe({good}))

        self.assertTrue(execve.called)
        self.assertEqual(good, hsctl._read_env_file()["interpreter"])

    def test_marker_prevents_an_infinite_reexec_loop(self):
        hsctl._write_env_file({"interpreter": "X:/py.exe"})
        with mock.patch.dict(os.environ, {hsctl.REEXEC_MARKER: "1"}), \
                mock.patch.object(hsctl.os, "execve") as execve:
            outcome = hsctl.ensure_interpreter(probe=fake_probe({"X:/py.exe"}))

        self.assertFalse(outcome["changed"])
        execve.assert_not_called()

    def test_nothing_usable_reports_the_reason(self):
        outcome = hsctl.ensure_interpreter(probe=fake_probe(set()))

        self.assertEqual("", outcome["path"])
        self.assertFalse(outcome["changed"])


class DoctorVerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.patcher = mock.patch.object(hsctl, "ENV_FILE", self.tmp / "env.json")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        os.environ.pop("HS_PYTHON", None)

    def _env(self, current_ok=False, pinned_ok=False, missing=None,
             ok_candidates=()):
        current = {"path": "X:/cur.exe", "ok": current_ok, "missing": list(missing or [])}
        pinned = ({"path": "X:/pinned.exe", "ok": pinned_ok}
                  if pinned_ok or pinned_ok is False and False else None)
        return {"current": current, "pinned": pinned,
                "candidates": [{"path": p, "ok": True} for p in ok_candidates]}

    def test_env_ok_when_saved_and_usable(self):
        hsctl._write_env_file({"interpreter": "X:/pinned.exe"})
        verdict, steps = hsctl._doctor_env_verdict(
            {"current": {"path": "X:/cur.exe", "ok": True},
             "pinned": {"path": "X:/pinned.exe", "ok": True},
             "candidates": []})

        self.assertEqual("env_ok", verdict)
        self.assertEqual([], steps)

    def test_usable_but_unsaved_asks_to_save_the_current_interpreter(self):
        verdict, steps = hsctl._doctor_env_verdict(
            {"current": {"path": "X:/cur.exe", "ok": True}, "pinned": None,
             "candidates": []})

        self.assertEqual("env_ok_unsaved", verdict)
        self.assertIn('hsctl env --pin "X:/cur.exe"', steps[0])

    def test_unsaved_uses_the_only_ok_candidate(self):
        verdict, steps = hsctl._doctor_env_verdict(
            {"current": {"path": "X:/bad.exe", "ok": False, "missing": []},
             "pinned": None, "candidates": [{"path": "X:/good.exe", "ok": True}]})

        self.assertEqual("env_ok_unsaved", verdict)
        self.assertIn('hsctl env --pin "X:/good.exe"', steps[0])

    def test_missing_dependencies_give_a_pip_command(self):
        verdict, steps = hsctl._doctor_env_verdict(
            {"current": {"path": "X:/cur.exe", "ok": False,
                         "missing": ["win32gui"]},
             "pinned": None, "candidates": []})

        self.assertEqual("env_deps_missing", verdict)
        self.assertIn("pywin32", steps[0])
        self.assertIn("X:/cur.exe", steps[0])

    def test_nothing_usable_gives_bootstrap_steps(self):
        verdict, steps = hsctl._doctor_env_verdict(
            {"current": {"path": "X:/cur.exe", "ok": False, "missing": []},
             "pinned": None, "candidates": []})

        self.assertEqual("env_no_valid_interpreter", verdict)
        self.assertTrue(any("venv" in step for step in steps))
        self.assertTrue(any("pip install -r requirements.txt" in step
                            for step in steps))


class TemplateTests(unittest.TestCase):
    """仓库里的模板：只有占位符，绝不含本机路径（否则别的机器直接踩坑）。"""

    def test_template_has_no_machine_specific_path(self):
        text = (hsctl.ROOT / "env.example.json").read_text(encoding="utf8")
        data = json.loads(text)

        self.assertIn("interpreter", data)
        self.assertIsNone(data["pinned_at"])
        for value in data.values():
            if isinstance(value, str):
                self.assertNotRegex(value, r"[A-Za-z]:[\\/]", value)

    def test_template_is_not_ignored_by_git(self):
        """模板必须能进版本库（实际生效的 log/env.json 才是被忽略的那个）。"""
        import subprocess

        proc = subprocess.run(["git", "check-ignore", "--quiet",
                               "env.example.json"], cwd=str(hsctl.ROOT),
                              capture_output=True)
        self.assertNotEqual(0, proc.returncode,
                            "env.example.json 被 .gitignore 吞了，模板进不了仓库")


if __name__ == "__main__":
    unittest.main()
