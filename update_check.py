# -*- coding: utf-8 -*-
"""从 GitHub（含国内可达镜像）拉取“线上最新版本号”，供浮窗对比本机版本。

版本号的**唯一来源仍然是 version.py**（版本号 = 发布日期，形如 ``2026.10.24``）。
本模块只是额外去网上问一句“线上最新是哪天”，用来在浮窗红字里提示用户
“群文件是不是最新”。设计约束：

* **导入不联网**：只有 `refresh()/refresh_async()/start_periodic()` 才会发请求；
* **绝不阻塞界面**：浮窗每 350ms 调一次 `status()`，只读内存缓存；
* **多源降级**：Release tag → raw version.py → jsDelivr；国内直连不通时走
  gh-proxy；全部失败时保留上次成功缓存，并明确返回 ``unknown``/``remote_missing``，
  **任何失败都不会被当成“已是最新”**；
* **多仓取最新**：默认同时查 README 里公开的仓库和本机 pushDefault 的镜像仓，
  取合法日期里最大的那个；可用环境变量 ``HS_UPDATE_REPO``（逗号分隔）覆盖。

缓存落盘在 ``log/update_cache.json``（`log/` 与 `*.json` 都被 .gitignore 忽略，
不会混进仓库）。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import version

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "log" / "update_cache.json"

#: 后台检查间隔 / 缓存有效期（秒）。6 小时足够：脚本是“每天改一点”的节奏。
CACHE_TTL = 6 * 3600
#: 单个 HTTP 请求超时（秒）与整条降级链的总预算（秒）。
REQUEST_TIMEOUT = 3.0
TOTAL_BUDGET = 8.0
#: 默认检查的仓库。第一个是 README/网页里展示的公开仓库，第二个是本机
#: `git remote -v` 里的 mine（HSArriver-Turbo，pushDefault）。
DEFAULT_REPOS = (
    "magicstrangelzb/HearthstoneLegendArriver",
    "Donald-Yip/HSArriver-Turbo",
)

_VERSION_RE = re.compile(r"VERSION\s*=\s*[\"']([^\"']+)[\"']")
_DATE_RE = re.compile(r"^[vV]?(\d{4})[.\-]?(\d{2})[.\-]?(\d{2})$")

_LOCK = threading.Lock()
_STATE = {"result": None, "loaded": False, "refreshing": False,
          "periodic": False}


# ---------------------------------------------------------------- 版本号解析
def parse_version(value):
    """把 ``v2026.10.24`` / ``2026-10-24`` / ``2026-1024`` 归一成 ``2026.10.24``。

    认不出、或不是真实日期（2026.13.40）时返回 None。历史 ``v1.0.0`` 这类
    三段短号天然匹配不上，会被跳过。
    """
    match = _DATE_RE.fullmatch(str(value or "").strip())
    if match is None:
        return None
    try:
        parsed = datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    return f"{parsed.year:04d}.{parsed.month:02d}.{parsed.day:02d}"


def compare_versions(local, latest):
    """返回 ``latest`` / ``update_available`` / ``local_newer`` / ``unknown``。"""
    local_date = parse_version(local)
    latest_date = parse_version(latest)
    if local_date is None or latest_date is None:
        return "unknown"
    if latest_date > local_date:
        return "update_available"
    if latest_date == local_date:
        return "latest"
    return "local_newer"


def configured_repos():
    """要检查的 GitHub 仓库列表（``HS_UPDATE_REPO`` 可覆盖，逗号分隔）。"""
    raw = str(os.environ.get("HS_UPDATE_REPO") or "").strip()
    if not raw:
        return list(DEFAULT_REPOS)
    repos = [item.strip().strip("/") for item in raw.split(",") if item.strip()]
    return repos or list(DEFAULT_REPOS)


# ---------------------------------------------------------------- 取数
def _source_urls(repo):
    """按“国内可达优先”的顺序给出 (来源名, URL, 解析方式)。"""
    return (
        ("jsdelivr", f"https://cdn.jsdelivr.net/gh/{repo}@main/version.py",
         "version_py"),
        ("ghproxy-raw",
         f"https://gh-proxy.com/https://raw.githubusercontent.com/{repo}/main/version.py",
         "version_py"),
        ("raw", f"https://raw.githubusercontent.com/{repo}/main/version.py",
         "version_py"),
        ("ghproxy-release",
         f"https://gh-proxy.com/https://api.github.com/repos/{repo}/releases/latest",
         "release_json"),
        ("release",
         f"https://api.github.com/repos/{repo}/releases/latest",
         "release_json"),
    )


def _http_get(url, timeout=REQUEST_TIMEOUT):
    """最小的 HTTP GET，返回 ``(status_code, text)``；网络异常向上抛。"""
    from urllib import error, request

    req = request.Request(
        url,
        headers={
            "User-Agent": f"{version.NAME}/{version.VERSION} (update-check)",
            "Accept": "application/json, text/plain, */*",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read(512 * 1024)
            return int(getattr(response, "status", 200)), body.decode(
                "utf-8", "replace")
    except error.HTTPError as exc:
        try:
            body = exc.read(512 * 1024).decode("utf-8", "replace")
        except Exception:
            body = ""
        return int(exc.code), body


def _version_from_payload(kind, text):
    if kind == "version_py":
        match = _VERSION_RE.search(text or "")
        return parse_version(match.group(1)) if match else None
    if kind == "release_json":
        try:
            payload = json.loads(text or "{}")
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        return parse_version(payload.get("tag_name") or payload.get("name"))
    return None


def fetch_latest(fetch=None, now=None):
    """逐个仓库/镜像问最新版本号，取合法日期的最大值。

    返回 ``{latest, source, repo, missing, error}``；``fetch`` 可注入
    （测试用），签名为 ``fetch(url, timeout=...) -> (status, text)``。
    """
    fetch = fetch or _http_get
    deadline = time.monotonic() + TOTAL_BUDGET
    candidates = []
    saw_missing = False
    last_error = None
    for repo in configured_repos():
        for source, url, kind in _source_urls(repo):
            if time.monotonic() >= deadline:
                last_error = last_error or "timeout_budget"
                break
            try:
                try:
                    status, text = fetch(url, timeout=REQUEST_TIMEOUT)
                except TypeError:
                    status, text = fetch(url)
            except Exception as exc:  # 网络/DNS/SSL/超时：换下一个源
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if status == 200:
                found = _version_from_payload(kind, text)
                if found:
                    candidates.append((found, source, repo))
                    break
                last_error = "invalid_version_payload"
                continue
            if status == 404:
                saw_missing = True
                continue
            last_error = f"HTTP {status}"
        # 这个仓库已拿到结果就不再试它的镜像；继续查下一个仓库。
    if candidates:
        candidates.sort(key=lambda item: parse_version(item[0]), reverse=True)
        latest, source, repo = candidates[0]
        return {"latest": latest, "source": source, "repo": repo,
                "missing": False, "error": None}
    return {
        "latest": None,
        "source": None,
        "repo": None,
        # 所有源都明确 404 = 远端确实还没发布；有网络错误就不敢这么说。
        "missing": saw_missing and last_error is None,
        "error": last_error,
    }


# ---------------------------------------------------------------- 缓存/状态
def _load_persisted():
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _save(record):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass


def _record():
    with _LOCK:
        if not _STATE["loaded"]:
            _STATE["result"] = _load_persisted()
            _STATE["loaded"] = True
        return _STATE["result"]


def _store(record):
    with _LOCK:
        _STATE["result"] = record
        _STATE["loaded"] = True
    _save(record)


def is_checking():
    with _LOCK:
        return bool(_STATE["refreshing"])


def status(local_version=None):
    """给 UI 用的快照（只读缓存，不联网）。"""
    local = parse_version(local_version or version.VERSION) or version.VERSION
    record = _record()
    local_label = f"v{local}"
    if not record:
        return {
            "ok": False,
            "state": "unknown",
            "checking": is_checking(),
            "local": local,
            "local_label": local_label,
            "latest": None,
            "latest_label": None,
            "source": None,
            "repo": None,
            "checked_at": None,
            "stale": True,
            "error": None,
        }
    latest = parse_version(record.get("latest"))
    if latest is None:
        state = "remote_missing" if record.get("missing") else "unknown"
    else:
        state = compare_versions(local, latest)
    checked_at = record.get("checked_at")
    try:
        stale = (time.time() - float(checked_at)) >= CACHE_TTL
    except (TypeError, ValueError):
        stale = True
    return {
        "ok": latest is not None,
        "state": state,
        "checking": is_checking(),
        "local": local,
        "local_label": local_label,
        "latest": latest,
        "latest_label": f"v{latest}" if latest else None,
        "source": record.get("source"),
        "repo": record.get("repo"),
        "checked_at": checked_at,
        "stale": stale,
        "error": record.get("error"),
    }


def refresh(force=False, fetch=None, now=None):
    """同步刷新一次（后台线程调用；TTL 内且非 force 时直接返回缓存）。"""
    now = time.time() if now is None else float(now)
    record = _record()
    if record is not None and not force:
        try:
            if now - float(record.get("fetched_at") or 0) < CACHE_TTL:
                return status()
        except (TypeError, ValueError):
            pass
    try:
        fetched = fetch_latest(fetch=fetch, now=now)
    except Exception as exc:  # 本函数绝不能把异常抛给线程/界面
        fetched = {"latest": None, "source": None, "repo": None,
                   "missing": False, "error": f"{type(exc).__name__}: {exc}"}
    _store({
        "latest": fetched.get("latest"),
        "source": fetched.get("source"),
        "repo": fetched.get("repo"),
        "missing": bool(fetched.get("missing")),
        "error": fetched.get("error"),
        "checked_at": now,
        "fetched_at": now,
    })
    return status()


def refresh_async(force=False, fetch=None):
    """后台刷新（同一时刻只跑一个），返回线程对象或 None。"""
    with _LOCK:
        if _STATE["refreshing"]:
            return None
        _STATE["refreshing"] = True

    def _run():
        try:
            refresh(force=force, fetch=fetch)
        except Exception:
            pass
        finally:
            with _LOCK:
                _STATE["refreshing"] = False

    thread = threading.Thread(target=_run, name="hs-update-check", daemon=True)
    thread.start()
    return thread


def start_periodic(interval=CACHE_TTL, fetch=None):
    """进程级后台循环：启动先查一次，之后每 interval 秒查一次。"""
    with _LOCK:
        if _STATE["periodic"]:
            return None
        _STATE["periodic"] = True

    def _loop():
        while True:
            with _LOCK:
                _STATE["refreshing"] = True
            try:
                refresh(force=True, fetch=fetch)
            except Exception:
                pass
            finally:
                with _LOCK:
                    _STATE["refreshing"] = False
            time.sleep(max(60.0, float(interval)))

    thread = threading.Thread(target=_loop, name="hs-update-loop", daemon=True)
    thread.start()
    return thread
