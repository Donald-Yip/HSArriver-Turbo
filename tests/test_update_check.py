# -*- coding: utf-8 -*-
"""GitHub 最新版本号拉取：解析、降级顺序、缓存与 UI 状态。

重点锁住“失败绝不冒充最新”：
  * 所有源 404 → remote_missing（线上还没发布）；
  * 网络全失败 → unknown（获取失败）；
  * 只有拿到合法日期才可能出现 latest / update_available / local_newer。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_check as uc


VERSION_PY = '#!/usr/bin/env python\nVERSION = "{0}"\n'


def reset_state(record=None):
    with uc._LOCK:
        uc._STATE["result"] = record
        uc._STATE["loaded"] = True
        uc._STATE["refreshing"] = False


class ParseVersionTests(unittest.TestCase):
    def test_accepts_the_formats_we_use(self):
        for raw in ("v2026.10.24", "2026.10.24", "2026-10-24", "2026-1024",
                    "20261024", " V2026.10.24 "):
            with self.subTest(raw=raw):
                self.assertEqual("2026.10.24", uc.parse_version(raw))

    def test_rejects_non_dates_and_old_tags(self):
        for raw in ("", None, "v1.0.0", "2026.13.40", "2026.02.30",
                    "v2026.10.24+abc", "2026/10/24", "not-a-date"):
            with self.subTest(raw=raw):
                self.assertIsNone(uc.parse_version(raw))

    def test_compare_versions(self):
        self.assertEqual("update_available",
                         uc.compare_versions("2026.10.24", "2026.10.25"))
        self.assertEqual("latest",
                         uc.compare_versions("2026.10.24", "v2026.10.24"))
        self.assertEqual("local_newer",
                         uc.compare_versions("2026.10.25", "2026.10.24"))
        self.assertEqual("unknown",
                         uc.compare_versions("2026.10.24", "bad"))


class FetchLatestTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.env = patch.dict(os.environ, {"HS_UPDATE_REPO": "owner/repo-a"})

    def tearDown(self):
        reset_state()

    def fake_fetch(self, routes):
        calls = []

        def fetch(url, timeout=None):
            calls.append(url)
            for needle, response in routes.items():
                if needle in url:
                    if isinstance(response, Exception):
                        raise response
                    return response
            raise OSError("unreachable")
        return fetch, calls

    def test_jsdelivr_version_file_wins(self):
        with self.env:
            fetch, calls = self.fake_fetch({
                "cdn.jsdelivr.net": (200, VERSION_PY.format("2026.10.24")),
                "raw.githubusercontent.com": (404, ""),
                "releases/latest": (404, ""),
            })
            result = uc.fetch_latest(fetch=fetch)

        self.assertEqual("2026.10.24", result["latest"])
        self.assertEqual("jsdelivr", result["source"])
        self.assertEqual("owner/repo-a", result["repo"])
        self.assertFalse(result["missing"])
        self.assertTrue(any("jsdelivr" in url for url in calls))

    def test_takes_the_newest_across_repos(self):
        with patch.dict(os.environ,
                        {"HS_UPDATE_REPO": "owner/repo-a,owner/repo-b"}):

            def fetch(url, timeout=None):
                if "repo-a" in url and "jsdelivr" in url:
                    return 200, VERSION_PY.format("2026.10.20")
                if "repo-b" in url and "jsdelivr" in url:
                    return 200, VERSION_PY.format("v2026.10.24")
                return 404, ""

            result = uc.fetch_latest(fetch=fetch)

        self.assertEqual("2026.10.24", result["latest"])
        self.assertEqual("owner/repo-b", result["repo"])

    def test_all_404_means_remote_missing(self):
        with self.env:
            fetch, _ = self.fake_fetch({})   # 所有 URL 都抛 OSError
            result = uc.fetch_latest(fetch=fetch)

        self.assertIsNone(result["latest"])
        self.assertFalse(result["missing"])
        self.assertIn("OSError", result["error"] or "")

        with self.env:
            fetch, _ = self.fake_fetch({"": (404, "")})
            result = uc.fetch_latest(fetch=fetch)

        self.assertIsNone(result["latest"])
        self.assertTrue(result["missing"])
        self.assertIsNone(result["error"])

    def test_network_errors_are_unknown_not_missing(self):
        with self.env:
            fetch, _ = self.fake_fetch({"": OSError("blocked")})
            result = uc.fetch_latest(fetch=fetch)

        self.assertIsNone(result["latest"])
        self.assertFalse(result["missing"])
        self.assertTrue(result["error"])

    def test_release_tag_is_the_fallback_source(self):
        with self.env:
            fetch, _ = self.fake_fetch({
                "version.py": (404, ""),
                "api.github.com/repos/owner/repo-a/releases/latest":
                    (200, '{"tag_name": "v2026.10.25"}'),
            })
            result = uc.fetch_latest(fetch=fetch)

        self.assertEqual("2026.10.25", result["latest"])
        self.assertIn("release", result["source"])

    def test_invalid_payloads_are_skipped(self):
        with self.env:
            fetch, _ = self.fake_fetch({
                "version.py": (200, VERSION_PY.format("v1.0.0")),
                "releases/latest": (200, '{"tag_name": "2026.13.40"}'),
            })
            result = uc.fetch_latest(fetch=fetch)

        self.assertIsNone(result["latest"])
        self.assertEqual("invalid_version_payload", result["error"])


class StatusTests(unittest.TestCase):
    def tearDown(self):
        reset_state()

    def test_unknown_without_any_record(self):
        reset_state(None)
        info = uc.status(local_version="2026.10.24")
        self.assertFalse(info["ok"])
        self.assertEqual("unknown", info["state"])
        self.assertEqual("v2026.10.24", info["local_label"])
        self.assertIsNone(info["latest_label"])
        self.assertTrue(info["stale"])

    def test_computes_each_state_from_cache(self):
        with patch.object(uc.time, "time", return_value=1000.0):
            reset_state({"latest": "2026.10.25", "fetched_at": 1000.0,
                         "checked_at": 1000.0, "missing": False,
                         "error": None, "source": "jsdelivr",
                         "repo": "owner/repo-a"})
            info = uc.status(local_version="2026.10.24")
        self.assertEqual("update_available", info["state"])
        self.assertEqual("v2026.10.25", info["latest_label"])
        self.assertTrue(info["ok"])  # ok = 成功拿到了最新号（不代表本地已最新）
        self.assertFalse(info["stale"])

        reset_state({"latest": "2026.10.24", "fetched_at": 1000.0,
                     "checked_at": 1000.0})
        self.assertEqual("latest",
                         uc.status(local_version="2026.10.24")["state"])

        reset_state({"latest": "2026.10.24", "fetched_at": 1000.0,
                     "checked_at": 1000.0})
        self.assertEqual("local_newer",
                         uc.status(local_version="2026.10.25")["state"])

    def test_remote_missing_and_failure_never_look_latest(self):
        reset_state({"latest": None, "missing": True, "fetched_at": 1000.0,
                     "checked_at": 1000.0})
        self.assertEqual("remote_missing",
                         uc.status(local_version="2026.10.24")["state"])
        self.assertFalse(uc.status(local_version="2026.10.24")["ok"])

        reset_state({"latest": None, "missing": False, "fetched_at": 1000.0,
                     "checked_at": 1000.0, "error": "blocked"})
        self.assertEqual("unknown",
                         uc.status(local_version="2026.10.24")["state"])


class RefreshTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.saved = patch.object(uc, "_save")
        self.saved.start()

    def tearDown(self):
        self.saved.stop()
        reset_state()

    def test_fresh_cache_skips_the_network(self):
        reset_state({"latest": "2026.10.24", "fetched_at": 10_000.0,
                     "checked_at": 10_000.0, "missing": False})
        called = []

        def fetch(url, timeout=None):
            called.append(url)
            raise OSError("should not fetch")

        uc.refresh(force=False, fetch=fetch, now=10_000.0 + 10)
        self.assertEqual([], called)

    def test_stale_cache_refreshes_and_updates_status(self):
        reset_state({"latest": "2026.10.20", "fetched_at": 0.0,
                     "checked_at": 0.0, "missing": False})
        with patch.dict(os.environ, {"HS_UPDATE_REPO": "owner/repo-a"}):
            uc.refresh(
                force=False,
                fetch=lambda url, timeout=None: (
                    (200, VERSION_PY.format("2026.10.24"))
                    if "jsdelivr" in url else (404, "")),
                now=uc.CACHE_TTL + 1,
            )
        info = uc.status(local_version="2026.10.24")
        self.assertEqual("latest", info["state"])
        self.assertEqual("2026.10.24", info["latest"])


class PersistenceTests(unittest.TestCase):
    def tearDown(self):
        reset_state()

    def test_persisted_cache_is_loaded_lazily(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "update_cache.json"
            with patch.object(uc, "CACHE_PATH", path):
                uc._store({"latest": "2026.10.24", "fetched_at": 1.0,
                           "checked_at": 1.0, "missing": False})
                with uc._LOCK:
                    uc._STATE["result"] = None
                    uc._STATE["loaded"] = False
                info = uc.status(local_version="2026.10.24")
        self.assertEqual("2026.10.24", info["latest"])
        self.assertTrue(info["stale"])


if __name__ == "__main__":
    unittest.main()
