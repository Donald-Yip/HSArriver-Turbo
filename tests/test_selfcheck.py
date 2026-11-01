# -*- coding: utf-8 -*-
"""环境自检：Python 版本判定、依赖缺失/版本不符的结论、requirements 覆盖度。"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import selfcheck

ROOT = Path(__file__).resolve().parent.parent


class PythonVersionTests(unittest.TestCase):
    def test_accepts_the_recommended_312_64bit(self):
        item = selfcheck.python_item(version=(3, 12, 14), bits=64)

        self.assertEqual(selfcheck.STATUS_OK, item["status"])
        self.assertIn("3.12.14", item["detail"])

    def test_other_versions_and_bits_are_rejected(self):
        cases = (
            ("3.11", (3, 11, 9), 64, "hint", "3.12"),
            ("3.13", (3, 13, 1), 64, "hint", None),
            ("3.14", (3, 14, 0), 64, "hint", None),
            ("32 位", (3, 12, 14), 32, "detail", "32 位"),
        )
        for name, version, bits, field, needle in cases:
            with self.subTest(version=version, bits=bits):
                item = selfcheck.python_item(version=version, bits=bits)

                self.assertEqual(selfcheck.STATUS_FAIL, item["status"])
                if needle:
                    self.assertIn(needle, item[field])


class DependencyManifestTests(unittest.TestCase):
    def test_requirements_pins_are_parsed_and_tolerate_a_broken_file(self):
        pins = selfcheck._requirements_pins()

        self.assertEqual("2.6.2", pins["paddlepaddle"])
        self.assertEqual("2.5.2", pins["numpy"])
        self.assertEqual("8.4.2", pins["click"])
        self.assertEqual({}, selfcheck._requirements_pins("# 注释\n随便写点什么\n\n"))

    def test_manifest_covers_every_requirement_without_strangers(self):
        """requirements.txt 里每个包都要能在自检清单里看到（不许漏检）。"""
        pins = selfcheck._requirements_pins()
        listed = {selfcheck._normalize(dist)
                  for spec in selfcheck.DEPENDENCIES for dist in spec["dist"]}

        self.assertEqual(set(), set(pins) - listed)
        # opencv 三件套（headless / opencv-python / contrib）都在自检清单里，
        # 且 requirements.txt 已全部钉版本 —— 允许清单比 pin 多出它们。
        self.assertEqual(set(), listed - set(pins))

    def test_python_312_specific_packages_are_checked(self):
        keys = {selfcheck._normalize(spec["dist"][0])
                for spec in selfcheck.DEPENDENCIES}

        for expected in ("paddlepaddle", "paddleocr", "pywin32", "numpy",
                         "opencv-python-headless", "pillow", "pynput",
                         "keyboard", "psutil", "requests"):
            self.assertIn(expected, keys)


class DependencyItemTests(unittest.TestCase):
    def test_missing_package_fails_with_pip_hint(self):
        def importer(name):
            if name == "paddleocr":
                raise ImportError("No module named 'paddleocr'")
            return object()

        items = selfcheck.dependency_items(importer=importer,
                                          version_reader=lambda dists: "1.0",
                                          pins={})
        item = next(i for i in items if i["key"] == "paddleocr")

        self.assertEqual(selfcheck.STATUS_FAIL, item["status"])
        self.assertIn("ImportError", item["detail"])
        self.assertIn("pip install", item["hint"])

    def test_version_mismatch_and_unreadable_version_are_warnings(self):
        items = selfcheck.dependency_items(importer=lambda name: object(),
                                          version_reader=lambda dists: "9.9.9",
                                          pins={"numpy": "2.5.2"})
        item = next(i for i in items if i["key"] == "numpy")
        self.assertEqual(selfcheck.STATUS_WARN, item["status"])
        self.assertIn("2.5.2", item["detail"])
        self.assertTrue(item["required"])

        items = selfcheck.dependency_items(importer=lambda name: object(),
                                          version_reader=lambda dists: None,
                                          pins={})
        item = next(i for i in items if i["key"] == "numpy")
        self.assertEqual(selfcheck.STATUS_WARN, item["status"])

    def test_click_is_metadata_only_and_optional_dependency_is_not_required(self):
        """仓库自带的 click.py 会遮蔽第三方 click，所以只认发行包元数据。"""
        items = selfcheck.dependency_items(
            importer=lambda name: object(),
            version_reader=lambda dists: (
                None if "click" in dists or "setuptools" in dists else "1.0"),
            pins={})
        click = next(i for i in items if i["key"] == "click")
        self.assertEqual(selfcheck.STATUS_FAIL, click["status"])
        self.assertIn("click", click["detail"])

        extras = next(i for i in items if i["key"] == "setuptools")
        self.assertEqual(selfcheck.STATUS_FAIL, extras["status"])
        self.assertFalse(extras["required"])
        self.assertIn("可选", extras["hint"])


class RunChecksTests(unittest.TestCase):
    @staticmethod
    def _ok_environment():
        return [selfcheck._item("env", "测试环境项", selfcheck.STATUS_OK, "ok")]

    def _run(self, environment=None, **kwargs):
        kwargs.setdefault("importer", lambda name: object())
        kwargs.setdefault("version_reader", lambda dists: "1.0.0")
        kwargs.setdefault("pins", {})
        kwargs.setdefault("version", (3, 12, 14))
        kwargs.setdefault("bits", 64)
        if environment is None:
            kwargs.setdefault("environment_probe", self._ok_environment)
        elif callable(environment):
            kwargs.setdefault("environment_probe", environment)
        else:
            kwargs.setdefault("environment_probe", lambda: environment)
        return selfcheck.run_checks(**kwargs)

    def test_everything_present_is_ok(self):
        result = self._run()

        self.assertTrue(result["ok"])
        self.assertEqual(0, result["failed"])
        self.assertIn("全部达标", result["summary"])
        self.assertEqual(result["total"], len(result["items"]))

    def test_environment_failure_makes_the_check_fail(self):
        env = [selfcheck._item("admin", "管理员权限", selfcheck.STATUS_FAIL,
                               "当前不是管理员", "以管理员身份运行")]
        result = self._run(environment=env)

        self.assertFalse(result["ok"])
        self.assertEqual(1, result["failed"])
        self.assertIn("管理员权限", result["summary"])

    def test_probe_and_importer_exceptions_are_contained(self):
        def broken():
            raise RuntimeError("探测器炸了")

        result = self._run(environment=broken)

        self.assertTrue(result["ok"])
        item = next(i for i in result["items"] if i["key"] == "environment")
        self.assertEqual(selfcheck.STATUS_WARN, item["status"])
        self.assertIn("探测器炸了", item["detail"])

        def importer(name):
            raise RuntimeError("boom")

        result = self._run(importer=importer)

        self.assertFalse(result["ok"])
        self.assertGreater(result["failed"], 0)

    def test_result_is_json_serializable_and_report_is_readable(self):
        result = self._run(environment=lambda: [
            selfcheck._item("admin", "管理员权限", selfcheck.STATUS_FAIL,
                            "当前不是管理员", "以管理员身份运行")])
        text = json.dumps(result, ensure_ascii=False)
        report = selfcheck.format_report(result)

        self.assertIn("管理员权限", text)
        self.assertIn("[自检]", report)
        self.assertIn("❌", report)
        self.assertIn("以管理员身份运行", report)

    def test_warning_summary_when_only_optional_items_differ(self):
        env = [selfcheck._item("hearthstone", "炉石窗口", selfcheck.STATUS_WARN,
                               "没找到", "", False)]
        result = self._run(environment=env)

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["warned"])
        self.assertIn("提示", result["summary"])


class WindowedClientSizeTests(unittest.TestCase):
    """窗口化时客户区必须是 1920×1080（只提示，不拦）。"""

    def _items(self, width, height, mode):
        import display_mapping

        mapping = display_mapping.DisplayMapping(
            hwnd=1, client=(0, 0, width, height), screen_left=0, screen_top=0)
        with patch.object(display_mapping, "display_mapping",
                          return_value=mapping), \
                patch.object(display_mapping, "window_mode", return_value=mode):
            items = selfcheck._client_mapping_items(None)
        return {item["key"]: item for item in items}

    def test_windowed_size_is_checked_without_blocking(self):
        item = self._items(1920, 1080, "windowed")["client-windowed"]
        self.assertEqual(selfcheck.STATUS_OK, item["status"])

        item = self._items(1600, 900, "windowed")["client-windowed"]
        self.assertEqual(selfcheck.STATUS_WARN, item["status"])
        self.assertFalse(item["required"], "只是口径建议，不该拦运行")
        self.assertIn("1920×1080", item["hint"])
        self.assertIn("1600×900", item["detail"])

    def test_fullscreen_has_no_size_item(self):
        items = self._items(1600, 900, "fullscreen")

        self.assertNotIn("client-windowed", items)


if __name__ == "__main__":
    unittest.main()
