#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""hsctl —— 炉石脚本（HSLegendArriver）的命令行控制台。

它是 web_ui.py 那个本地 HTTP 控制面的**薄客户端**：只做「命令行参数 ↔ HTTP
JSON」的翻译与结果渲染，不含任何自动化逻辑。所以可以用它（或让 agent 用它）
实时控制正在运行的脚本：看状态、追日志、开始/停止、热改参数、定时、校准、
重启炉石、托管控制台进程。

为什么不用 Click：
    项目根目录有 click.py（炉石的鼠标点击模块）。在仓库里 `import click` 会
    解析到它，而不是 PyPI 的 click —— 任何基于 Click 的 CLI 在这里都会直接
    失败（实测：HTL 环境里 click.__file__ 就是仓库根的 click.py）。
    所以本文件**只用标准库**（argparse + urllib），Python 3.9+ 均可运行。

用法::

    hsctl                        # 裸命令进入交互 REPL
    hsctl status                 # 看状态
    hsctl status --watch 2       # 每 2 秒刷新一行状态
    hsctl log -f                 # 实时跟随日志（Ctrl+C 退出）
    hsctl start | stop | resume | prepare
    hsctl delays set pre_action_delay_seconds=5
    hsctl human-like set --enabled
    hsctl surrender set --file rules.json    # 卡组识别投降（只判对方）
    hsctl card 神秘访客                      # 查卡名（本地，不联网）
    hsctl schedule at 22:30 --end 23:30
    hsctl serve start            # 后台拉起控制台（网页仍可用）
    hsctl --json status          # 机器可读输出，给 agent 用

退出码（agent 靠它分支）::

    0  成功
    1  控制台返回业务失败（例如「自动化运行中，请先停止」）
    2  参数用法错误
    3  连不上控制台（脚本没在运行 / 端口找不到）

配合 agent 使用：把本文件同目录的 SKILL.md 交给 agent，它就知道该怎么编排命令。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from version import VERSION as HSCTL_VERSION, version_info, version_label

ROOT = Path(__file__).resolve().parent
PORT_FILE = ROOT / "log" / "ui_port.txt"
PID_FILE = ROOT / "log" / "ui_server.pid"
SERVE_LOG = ROOT / "log" / "cli_serve.log"
# 「最高权限」计划任务的启动器（由 serve install-task 生成；任务指向它而不是内联命令）
TASK_LAUNCHER = ROOT / "log" / "hs_serve_elevated.cmd"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_BASE_PORT = 8765
PORT_SCAN_COUNT = 20  # 与 web_ui.py 的 BASE_PORT..BASE_PORT+19 对齐
DEFAULT_TIMEOUT = 15.0
PROBE_TIMEOUT = 0.3

EXIT_OK = 0
EXIT_API = 1
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3
# 环境不可用（找不到/校验不过的解释器，或缺依赖）。agent 见到它就跑 `hsctl doctor`。
EXIT_ENV = 4

# --------------------------------------------------------------------- 运行环境
# 为什么有这一整块：这个项目**必须**跑在 Python 3.12 + 那一堆 win32/cv2/paddle 依赖上，
# 而每台机器、每个 agent 自带的 python 都不同 —— 把某一台机器的路径写进代码/默认值
# 一定会在别的机器上翻车。所以这里的做法是：
#   1) 仓库里放 `log/env.example.json`（只有占位符与说明，**不含任何本机路径**）；
#   2) 每台机器把选定的解释器**保存**到本机专属的 `log/env.json`（log/ 不入库）；
#   3) `hsctl doctor` 一条命令告诉 agent"现在什么状态、该执行哪几条命令"；
#   4) 任何 agent 用任何 python 跑 hsctl，都会被自动代理到那个解释器上。
ENV_FILE = ROOT / "log" / "env.json"
# 模板放在仓库根、并显式加进版本库（log/ 整个目录被忽略，里面放不了"要提交的"模板）。
ENV_TEMPLATE = ROOT / "env.example.json"
# 解释器校验：3.12.x + 这些模块必须**存在**（只 find_spec，不 import —— 不拉重依赖）。
REQUIRED_MODULES = (
    "win32gui",
    "cv2",
    "numpy",
    "PIL",
    "paddleocr",
    "paddle",
    "psutil",
    "keyboard",
    "pynput",
    "requests",
    "click",
)
# 模块名 → pip 包名（只用于给用户/agent 打印"缺哪个包该装什么"）。
MODULE_PACKAGES = {
    "win32gui": "pywin32",
    "cv2": "opencv_python_headless",
    "PIL": "Pillow",
    "paddleocr": "paddleocr",
    "paddle": "paddlepaddle",
    "psutil": "psutil",
    "keyboard": "keyboard",
    "pynput": "pynput",
    "requests": "Requests",
    "click": "click",
    "numpy": "numpy",
}
EXPECTED_PYTHON = (3, 12)
PROBE_TIMEOUT = 60.0
REEXEC_MARKER = "HSCTL_REEXEC"

# 在被代理的解释器里跑的探针：把版本与缺失模块打成一行 JSON。只用标准库。
_PROBE_CODE = (
    "import sys,json,importlib.util\n"
    "mods=%r\n"
    "def has(m):\n"
    "    try:\n"
    "        return importlib.util.find_spec(m) is not None\n"
    "    except Exception:\n"
    "        return False\n"
    "missing=[m for m in mods if not has(m)]\n"
    "print(json.dumps({'version':list(sys.version_info[:3]),\n"
    "                  'executable':sys.executable,\n"
    "                  'missing':missing}))\n"
)


def _env_text(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def _read_env_file() -> dict:
    """读本机专属的 log/env.json（不存在/坏掉都返回 {}）。"""
    try:
        data = json.loads(ENV_FILE.read_text(encoding="utf8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_env_file(data: dict) -> bool:
    """原子替换写入 log/env.json（写不了返回 False，不影响命令本身）。"""
    try:
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = ENV_FILE.with_name(ENV_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
        os.replace(tmp, ENV_FILE)
        return True
    except Exception:
        return False


def env_pin_source() -> str:
    """当前解释器是从哪儿定下来的：env / file / current / candidate / none。"""
    if _env_text("HS_PYTHON"):
        return "env"
    pinned = str(_read_env_file().get("interpreter") or "").strip()
    if pinned and Path(pinned).exists():
        return "file"
    return "none"


def verify_python(python: str, *, runner=None) -> dict:
    """跑一次探针，返回 {'ok', 'version', 'missing', 'reason', 'executable'}。

    runner 可注入（测试用），签名 ``runner(argv, timeout) -> (返回码, 文本)``。
    """
    python = str(python or "").strip()
    if not python:
        return {
            "ok": False,
            "executable": "",
            "version": None,
            "missing": [],
            "reason": "解释器路径为空",
        }
    if not Path(python).exists():
        return {
            "ok": False,
            "executable": python,
            "version": None,
            "missing": [],
            "reason": "文件不存在",
        }
    argv = [python, "-c", _PROBE_CODE % (list(REQUIRED_MODULES),)]
    runner = runner or _run_capture
    try:
        code, out = runner(argv, PROBE_TIMEOUT)
    except Exception as exc:
        return {
            "ok": False,
            "executable": python,
            "version": None,
            "missing": [],
            "reason": f"探针执行失败：{type(exc).__name__}: {exc}",
        }
    if code != 0:
        return {
            "ok": False,
            "executable": python,
            "version": None,
            "missing": [],
            "reason": f"解释器无法运行探针（退出码 {code}）：{(out or '').strip()[:160]}",
        }
    payload = None
    for line in reversed((out or "").strip().splitlines()):
        try:
            payload = json.loads(line)
            break
        except Exception:
            continue
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "executable": python,
            "version": None,
            "missing": [],
            "reason": "探针输出无法解析",
        }
    version = tuple(int(v) for v in (payload.get("version") or []))
    missing = [str(m) for m in (payload.get("missing") or [])]
    if version[:2] != EXPECTED_PYTHON:
        return {
            "ok": False,
            "executable": str(payload.get("executable") or python),
            "version": version,
            "missing": missing,
            "reason": f"版本 {'.'.join(str(v) for v in version)} 不是 "
            f"{EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]}（3.13+ 装不上 OCR 轮子）",
        }
    if missing:
        return {
            "ok": False,
            "executable": str(payload.get("executable") or python),
            "version": version,
            "missing": missing,
            "reason": "缺依赖："
            + "、".join(MODULE_PACKAGES.get(m, m) for m in missing),
        }
    return {
        "ok": True,
        "executable": str(payload.get("executable") or python),
        "version": version,
        "missing": [],
        "reason": "ok",
    }


def _run_capture(argv, timeout: float = PROBE_TIMEOUT) -> tuple:
    """跑一条命令，返回 (返回码, 文本)。只用于探针，失败不抛。"""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    raw = (proc.stdout or b"") + (proc.stderr or b"")
    for enc in ("utf-8", "mbcs"):
        try:
            return proc.returncode, raw.decode(enc)
        except Exception:
            continue
    return proc.returncode, raw.decode("utf-8", "replace")


def _normalize_python(path) -> str:
    """把 pythonw.exe 换成同目录的 python.exe（pythonw 下 stdout 可能是 None）。"""
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    if os.name == "nt" and text.lower().endswith("pythonw.exe"):
        plain = str(Path(text).with_name("python.exe"))
        if Path(plain).exists():
            return plain
    return text


def _where_pythons() -> list:
    """`where python` 之类的兜底：拿到 PATH 上的解释器。"""
    if os.name != "nt":
        return []
    found = []
    for name in ("python.exe", "python3.exe"):
        try:
            proc = subprocess.run(["where", name], capture_output=True, timeout=10)
        except Exception:
            continue
        if proc.returncode != 0:
            continue
        for line in (proc.stdout or b"").decode("mbcs", "replace").splitlines():
            line = line.strip()
            if line:
                found.append(line)
    return found


def discover_candidates() -> list:
    """列出机器上"可能是本项目解释器"的候选路径（去重、保序、只留真实文件）。

    这里**只是候选**，合格与否一律靠 verify_python 判定 —— 尤其是 conda 的 HTL
    这类命名习惯只在部分机器上成立，绝不能当成默认值。
    """
    raw = []
    for key in ("HS_PYTHON",):
        raw.append(_env_text(key))
    for key in ("VIRTUAL_ENV", "CONDA_PREFIX"):
        prefix = _env_text(key)
        if prefix:
            raw.append(str(Path(prefix) / "Scripts" / "python.exe"))
            raw.append(str(Path(prefix) / "bin" / "python3"))
    pinned = str(_read_env_file().get("interpreter") or "").strip()
    if pinned:
        raw.append(pinned)
    # 常见 conda 位置（仅候选；有的机器根本没有 conda）。
    bases = [
        Path(r"D:\miniconda3"),
        Path(r"D:\anaconda3"),
        Path.home() / "miniconda3",
        Path.home() / "anaconda3",
        Path.home() / "miniforge3",
        Path(os.environ.get("LOCALAPPDATA", "")) / "miniconda3",
        Path(os.environ.get("LOCALAPPDATA", "")) / "anaconda3",
    ]
    for base in bases:
        for env_name in ("HTL", "htl"):
            raw.append(str(base / "envs" / env_name / "python.exe"))
        raw.append(str(base / "python.exe"))
    for name in (".venv", "venv"):
        raw.append(str(ROOT / name / "Scripts" / "python.exe"))
        raw.append(str(ROOT / name / "bin" / "python3"))
    raw.extend(_where_pythons())
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    if local.exists():
        try:
            for item in sorted(local.glob("Python31*")):
                raw.append(str(item / "python.exe"))
        except Exception:
            pass

    seen, out = set(), []
    current = _normalize_python(sys.executable).casefold()
    for item in raw:
        path = _normalize_python(item)
        if not path:
            continue
        key = path.casefold()  # Windows 路径大小写不敏感：别把同一个环境列两遍
        if key in seen:
            continue
        seen.add(key)
        if os.path.basename(path).lower().startswith("python") is False:
            # `py.exe`（启动器）不是解释器：显式跳过，避免误报"找不到环境"。
            continue
        try:
            if not Path(path).is_file():
                continue
        except OSError:
            continue
        if key == current:
            continue
        out.append(path)
    return out


def resolve_interpreter(probe=None, *, probe_candidates: bool = True) -> dict:
    """按优先级找出**合格**的解释器。

    顺序：``HS_PYTHON`` → 本机 ``log/env.json`` → 已在跑的当前解释器 → 候选清单。
    返回 ``{"path","source","record","candidates":[{path,ok,reason,missing,version}],
    "current_ok","current"}``；找不到合格的就是 ``path=""``。
    """
    probe = probe or verify_python
    current = _normalize_python(sys.executable)
    result = {
        "path": "",
        "source": "none",
        "record": None,
        "candidates": [],
        "current_ok": False,
        "current": current,
    }

    env_python = _normalize_python(_env_text("HS_PYTHON"))
    if env_python:
        record = probe(env_python)
        result["candidates"].append({"path": env_python, **record})
        if record.get("ok"):
            result.update({"path": env_python, "source": "env", "record": record})
            return result

    pinned = _normalize_python(_read_env_file().get("interpreter"))
    if pinned:
        cached = _read_env_file().get("last_ok") or {}
        record = (
            dict(cached, reason="cached")
            if cached.get("executable") == pinned and cached.get("version")
            else None
        )
        if record is None or not probe_candidates:
            record = probe(pinned)
        result["candidates"].append({"path": pinned, **record})
        if record.get("ok"):
            result.update({"path": pinned, "source": "file", "record": record})
            return result

    record = probe(current)
    result["current_ok"] = bool(record.get("ok"))
    if record.get("ok"):
        result.update({"path": current, "source": "current", "record": record})
        return result
    if not probe_candidates:
        return result

    for candidate in discover_candidates():
        record = probe(candidate)
        result["candidates"].append({"path": candidate, **record})
        if record.get("ok"):
            result.update({"path": candidate, "source": "candidate", "record": record})
            return result
    return result


def _persist_env(path: str, source: str) -> bool:
    """把选定的解释器**保存**到本机专属 log/env.json。"""
    data = _read_env_file()
    data["interpreter"] = str(path)
    data["pinned_at"] = datetime.now().isoformat(timespec="seconds")
    data["source"] = source
    return _write_env_file(data)


def _cache_env_probe(record: dict) -> None:
    """把一次合格校验的结果缓存进 env.json（下次免探针）。"""
    data = _read_env_file()
    data["last_ok"] = {
        "executable": record.get("executable"),
        "version": list(record.get("version") or []),
        "missing": [],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_env_file(data)


def ensure_interpreter(*, probe=None, write: bool = True) -> dict:
    """`hsctl` 入口用的解释器保证：合格就返回，不合格就用合格的重入。

    返回 ``{"path","source","changed","reason"}``；``changed=True`` 表示已经
    ``os.execv`` 重入过（正常情况下不会返回到调用方）。
    """
    probe = probe or verify_python
    if _env_text("HSCTL_NO_PROXY"):
        return {
            "path": _normalize_python(sys.executable),
            "source": "current",
            "changed": False,
            "reason": "HSCTL_NO_PROXY=1",
        }
    current = _normalize_python(sys.executable)
    record = probe(current)
    if record.get("ok"):
        if write:
            _cache_env_probe(record)
        return {
            "path": current,
            "source": "current",
            "changed": False,
            "reason": "ok",
            "record": record,
        }

    resolved = resolve_interpreter(probe=probe)
    if not resolved.get("path"):
        return {
            "path": "",
            "source": "none",
            "changed": False,
            "reason": record.get("reason") or "没有合格解释器",
            "record": record,
            "candidates": resolved.get("candidates") or [],
        }

    if resolved["path"] == current or _env_text(REEXEC_MARKER):
        # 已经是目标解释器（或已经重入过一次）：不再重入，按现状尽力执行。
        return {
            "path": resolved["path"],
            "source": resolved["source"],
            "changed": False,
            "reason": "已在目标解释器上",
            "record": resolved.get("record"),
        }

    if write and resolved["source"] in ("candidate", "current"):
        _persist_env(resolved["path"], resolved["source"])
    if write:
        _cache_env_probe(resolved.get("record") or {})

    env = dict(os.environ)
    env[REEXEC_MARKER] = "1"
    argv = [resolved["path"], str(ROOT / "hsctl.py")] + list(sys.argv[1:])
    try:
        os.execve(resolved["path"], argv, env)
    except Exception as exc:
        # 重入失败（极少数：权限/路径问题）：不阻断，按当前解释器继续，让命令
        # 自己在需要时报"缺依赖"。
        return {
            "path": resolved["path"],
            "source": resolved["source"],
            "changed": False,
            "reason": f"切换解释器失败：{type(exc).__name__}: {exc}",
        }


def interpreter_report(*, probe=None) -> dict:
    """给 `hsctl doctor` / `hsctl env` 用的环境快照。"""
    probe = probe or verify_python
    current = _normalize_python(sys.executable)
    current_record = probe(current)
    pinned_path = _normalize_python(_read_env_file().get("interpreter"))
    pinned_record = probe(pinned_path) if pinned_path else None
    candidates = []
    seen = set()
    for item in ([pinned_path] if pinned_path else []) + discover_candidates():
        path = _normalize_python(item)
        key = path.casefold()
        if not path or key in seen:
            continue
        seen.add(key)
        record = (pinned_record if key == pinned_path.casefold() else None) or probe(
            path
        )
        candidates.append({"path": path, **record})
    return {
        "current": {"path": current, **current_record},
        "pinned": (
            {"path": pinned_path, **(pinned_record or {})} if pinned_path else None
        ),
        "pinned_source": env_pin_source(),
        "env_file": str(ENV_FILE),
        "env_file_exists": ENV_FILE.exists(),
        "template": str(ENV_TEMPLATE),
        "candidates": candidates,
    }


# 「进入对战」的最长等待：等主界面 + 等选择套牌界面 + 拉起炉石，再留一点余量。
ENTER_BATTLE_WAIT_SECONDS = 600.0
ENTER_BATTLE_POLL_SECONDS = 2.0

# 「重启炉石」的最长等待：控制台侧等主界面上限 180s（RESTART_MAIN_MENU_TIMEOUT_SECONDS），
# 前面还有停自动化 + 杀进程 + 清日志 + 拉战网/炉石，再留一点余量。
RESTART_WAIT_SECONDS = 300.0
RESTART_POLL_SECONDS = 2.0

# 延时字段：与 config.py 的 _USER_DELAY_KEYS 一一对应（这里只用于帮助文本）。
DELAY_KEYS = (
    "mulligan_ready_delay_seconds",
    "mulligan_post_ocr_delay_seconds",
    "mulligan_retry_delay_seconds",
    "first_turn_per_card_delay_seconds",
    "pre_action_delay_seconds",
    "post_action_delay_seconds",
    "draw_extra_delay_per_card_seconds",
    "discover_target_delay_seconds",
    "ocr_preprocess_scale",
)
# 生效时机（与 web_ui.py 的 _delay_effect_scope 保持一致，用于本地提示）。
DELAY_EFFECT = {
    "mulligan_ready_delay_seconds": "live",
    "mulligan_retry_delay_seconds": "live",
    "pre_action_delay_seconds": "live",
    "first_turn_per_card_delay_seconds": "live",
    "discover_target_delay_seconds": "live",
    "mulligan_post_ocr_delay_seconds": "next_start",
    "post_action_delay_seconds": "next_start",
    "draw_extra_delay_per_card_seconds": "next_start",
    "ocr_preprocess_scale": "restart",
}
EFFECT_LABEL = {
    "live": "立即生效",
    "next_start": "下次「开始对战」生效",
    "restart": "需重启脚本进程",
}
# 抓图类型：与 web_ui.py 的 SCREENSHOT_KINDS 一一对应（有测试盯着别漂移）。
SCREENSHOT_KINDS = ("client", "full", "roi", "boxes")


class TransportError(Exception):
    """连不上控制台（脚本没跑 / 端口不对 / 超时）。"""


class ApiFailure(Exception):
    """控制台回了 ok:false（业务性失败，带中文原因）。"""


# --------------------------------------------------------------------- 传输层
class Ctx:
    """一次命令的上下文：输出模式 + 目标地址（解析一次后缓存）。"""

    def __init__(self, args):
        self.json = bool(getattr(args, "json", False))
        self.host = getattr(args, "host", DEFAULT_HOST) or DEFAULT_HOST
        self.port = getattr(args, "port", None)
        self.timeout = float(
            getattr(args, "timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT
        )
        self._base = None

    # ---- 地址发现
    @property
    def base(self) -> str:
        if self._base is None:
            self._base = self._resolve_base()
        return self._base

    def _resolve_base(self) -> str:
        """按 显式 --port → HS_PORT → 端口文件 → 扫端口 的顺序找控制台。

        端口会因为占用而顺延（8765..8785），所以优先读 web_ui 写的端口文件；
        文件可能过期（进程已被杀），所以要探活确认。
        """
        if self.port:
            base = f"http://{self.host}:{int(self.port)}"
            self._require_alive(base)
            return base

        env_port = os.environ.get("HS_PORT")
        if env_port:
            try:
                base = f"http://{self.host}:{int(env_port)}"
                self._require_alive(base)
                return base
            except (TransportError, ValueError):
                pass

        info = read_port_file()
        if info and info.get("port"):
            host = info.get("host") or self.host
            base = f"http://{host}:{int(info['port'])}"
            if self._probe(base):
                return base

        start = DEFAULT_BASE_PORT
        try:
            start = int(env_port) if env_port else DEFAULT_BASE_PORT
        except ValueError:
            start = DEFAULT_BASE_PORT
        for port in range(start, start + PORT_SCAN_COUNT):
            base = f"http://{self.host}:{port}"
            if self._probe(base):
                return base

        raise TransportError(
            f"没找到正在运行的控制台（已探测 {self.host}:{start}-"
            f"{start + PORT_SCAN_COUNT - 1}）。\n"
            "  先启动脚本：python web_ui.py --no-browser\n"
            "  或用本 CLI 托管：hsctl serve start"
        )

    def _probe(self, base: str) -> bool:
        try:
            with urllib.request.urlopen(
                base + "/api/status", timeout=PROBE_TIMEOUT
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _require_alive(self, base: str):
        if not self._probe(base):
            raise TransportError(
                f"连不上 {base}：脚本没在运行，或端口不对。\n"
                "  先启动脚本：python web_ui.py --no-browser\n"
                "  或用本 CLI 托管：hsctl serve start"
            )

    # ---- 请求
    def request(self, method: str, path: str, body=None, params=None) -> dict:
        url = self.base + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf8")
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError(f"请求 {path} 失败：{exc}")
        try:
            payload = json.loads(raw)
        except Exception:
            raise TransportError(f"控制台对 {path} 返回了非 JSON 响应：{raw[:200]!r}")
        if not isinstance(payload, dict):
            raise TransportError(f"控制台对 {path} 返回了意外的结构：{payload!r}")
        # 统一的业务失败约定：{"ok": false, "error": "..."}
        if payload.get("ok") is False:
            raise ApiFailure(str(payload.get("error") or "控制台返回失败"))
        return payload

    def get(self, path: str, params=None) -> dict:
        return self.request("GET", path, params=params)

    def post(self, path: str, body=None) -> dict:
        return self.request("POST", path, body=body if body is not None else {})

    # ---- 输出
    def out(self, text: str = ""):
        print(text)

    def emit(self, payload: dict, human: str):
        """统一出口：--json 打原始 JSON，否则打人读文本。"""
        if self.json:
            print(json.dumps(payload, ensure_ascii=False))
        elif human:
            print(human)


def read_port_file():
    """读 web_ui 写的端口文件（log/ui_port.txt）；缺失/写坏返回 None。"""
    try:
        return json.loads(PORT_FILE.read_text(encoding="utf8"))
    except Exception:
        return None


# --------------------------------------------------------------------- 渲染
def _num(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "开" if value else "关"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _dur(seconds) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "-"
    if total <= 0:
        return "-"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _flag(value) -> str:
    return "开" if value else "关"


def render_status(s: dict, url: str = "") -> str:
    lines = []
    running = bool(s.get("running"))
    phase = s.get("phase") or "-"
    head = "运行中" if running else ("启动中" if s.get("starting") else "未运行")
    lines.append(
        f"控制台   {url or '-'}   （管理员权限：{'是' if s.get('is_admin') else '否'}）"
    )
    lines.append(f"状态     {head} · {phase} · {s.get('state_label') or '待机'}")
    if s.get("stop_after_game"):
        lines.append("停止     ⏳ 已请求「本局结束后停止」")
    lines.append(
        f"就绪     {'是' if s.get('prepared') else '否'}"
        f"     浮窗 {_flag(s.get('overlay_running'))}"
        f"     热键 {'已注册' if s.get('hotkey_ok') else '未注册'}"
    )

    nm = s.get("name_match") or {}
    matched = nm.get("matched")
    mark = {True: "✅ 匹配", False: "❌ 不匹配（检查用户 ID）"}.get(
        matched, "… 未读到玩家名"
    )
    name = (s.get("config") or {}).get("name") or nm.get("config") or "（未填写）"
    lines.append(f"账号     {name}   {mark}")

    games = int(s.get("games") or 0)
    wins = int(s.get("wins") or 0)
    lines.append(
        f"战绩     {games} 场 {wins} 胜 ({_num(s.get('win_rate'))}%)"
        f" · 认输 {int(s.get('concede_count') or 0)}"
        f" · 本局 {_dur(s.get('game_elapsed_sec'))}"
    )

    lv = s.get("liveness") or {}
    lines.append(
        f"存活检测 {_flag(lv.get('enabled'))} · 炉石 {_num(lv.get('status'))}"
        f" · 停滞 {_num(lv.get('log_age'))}s"
        f" ({_num(lv.get('log_stale_warn_seconds'))}s 告警 /"
        f" {_num(lv.get('log_stale_stop_seconds'))}s 停止)"
    )
    if lv.get("alert"):
        lines.append(f"         ⚠️ {lv['alert']}")

    cd = s.get("concede") or {}
    det = s.get("concede_detect") or {}
    extra = ""
    if det.get("rate") is not None:
        extra = f" (最近胜率 {_num(det.get('rate'))}% ×{_num(det.get('streak'))})"
    lines.append(
        f"自动投降 {_flag(cd.get('enabled'))} · 胜率<"
        f"{_num(cd.get('threshold'))}% 连续 "
        f"{_num(cd.get('rounds'))} 回合{extra}"
    )

    sr = s.get("auto_surrender") or {}
    sr_decks = sr.get("decks") or []
    if not sr.get("enabled"):
        sr_text = "关"
    elif not sr_decks:
        sr_text = "开 · 还没配卡组"
    else:
        names = [deck.get("name") or deck.get("id") or "?" for deck in sr_decks[:3]]
        more = f" 等 {len(sr_decks)} 套" if len(sr_decks) > 3 else ""
        sr_text = f"开 · {len(sr_decks)} 套：{'、'.join(names)}{more}"
    hit = (s.get("surrenders") or {}).get("hit") or {}
    if hit.get("name"):
        sr_text += f"（本局命中「{hit['name']}」）"
    lines.append(f"卡组投降 {sr_text}")

    rk = s.get("rank_stop") or {}
    keep = rk.get("action") == "keep"
    if not rk.get("enabled"):
        rank_text = "关"
    elif rk.get("mode") == "legend":
        rank_text = "开 · 上传说即停"
    elif keep:
        rank_text = (
            f"开 · 控分：名次优于第 {_num(rk.get('legend_number'))} 名"
            f"就开局投降（本次已投 {_num(rk.get('keep_count'))} 局）"
        )
    else:
        rank_text = f"开 · 上到传说第 {_num(rk.get('legend_number'))} 名即停"
    lines.append(f"段位目标 {rank_text}")

    hl = s.get("human_like") or {}
    lines.append(
        f"活人感   {_flag(hl.get('enabled'))}"
        f" · 看卡牌 {_flag(hl.get('hand_hover_enabled'))}"
        f" {_num(hl.get('post_delay_min'))}~{_num(hl.get('post_delay_max'))}s"
        f" · 悬停 {_num(hl.get('hover_min'))}~{_num(hl.get('hover_max'))}s"
        f" · 看随从 {_flag(hl.get('minion_hover_enabled'))}"
        f" {_num(hl.get('minion_hover_min'))}~{_num(hl.get('minion_hover_max'))}s"
    )

    sched = (s.get("schedule_start"), s.get("schedule_end"))
    lines.append(
        "定时     "
        + (f"{sched[0]} → {sched[1] or '（不设结束）'}" if sched[0] else "（无）")
    )

    delays = s.get("delays") or {}
    if delays:
        items = [
            f"{k.replace('_seconds', '').replace('_scale', '')}={_num(v)}"
            for k, v in delays.items()
        ]
        lines.append("延时     " + "  ".join(items[:4]))
        if len(items) > 4:
            lines.append("         " + "  ".join(items[4:]))

    sc = s.get("selfcheck") or {}
    if not sc.get("checked"):
        lines.append("自检     尚未完成")
    else:
        lines.append(
            f"自检     {'✅ 通过' if sc.get('ok') else '❌ 未通过'}"
            f" ({int(sc.get('failed') or 0)} 失败 /"
            f" {int(sc.get('warned') or 0)} 警告)"
            f" · Python {_num(sc.get('python'))}"
        )

    lines.append(f"最后错误 {s.get('last_error') or '（无）'}")
    if not s.get("is_admin"):
        lines.append(
            "⚠️  非管理员权限：点击可能被系统拦掉（现象是「脚本在跑但点了没反应」）。"
        )
    return "\n".join(lines)


def _one_line(s: dict) -> str:
    running = bool(s.get("running"))
    head = "运行中" if running else ("启动中" if s.get("starting") else "未运行")
    games = int(s.get("games") or 0)
    wins = int(s.get("wins") or 0)
    return (
        f"{datetime.now():%H:%M:%S}  {head}·{s.get('state_label') or '待机'}"
        f"  {wins}胜/{games}场({_num(s.get('win_rate'))}%)"
        f"  本局 {_dur(s.get('game_elapsed_sec'))}"
        f"  浮窗{_flag(s.get('overlay_running'))}"
        + ("  [本局后停]" if s.get("stop_after_game") else "")
    )


def _log_line(entry, json_mode: bool) -> str:
    if json_mode:
        return json.dumps(entry, ensure_ascii=False)
    if isinstance(entry, dict):
        return (
            f"{entry.get('t', '--:--:--')} [{entry.get('level', '?'):>5}] "
            f"{entry.get('msg', '')}"
        )
    return str(entry)


def _ok_message(ctx: Ctx, payload: dict) -> int:
    """POST 类命令的统一回话。"""
    msg = payload.get("message") or "完成"
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"✓ {msg}")
        for warn in payload.get("warnings") or []:
            print(f"  ⚠️ {warn}")
        note = payload.get("note")
        if note:
            print(f"  ℹ️ {note}")
    return EXIT_OK


# --------------------------------------------------------------------- 环境自诊
def _console_report(ctx: Ctx) -> dict:
    """控制台侧的诊断信息（连不上就说明没跑，不算错误）。"""
    info = read_port_file() or {}
    report = {
        "running": False,
        "url": None,
        "pid": info.get("pid"),
        "executable": info.get("executable"),
        "token_present": bool(info.get("token")),
        "is_admin": None,
        "phase": None,
        "console_python_ok": None,
        "selfcheck_failed": None,
        "note": "",
    }
    try:
        alive = ctx._probe(ctx.base)
    except TransportError:
        alive = False
    if not alive:
        report["note"] = "控制台没在运行（端口文件可能过期）"
        return report
    report["running"] = True
    try:
        report["url"] = ctx.base
    except TransportError:
        pass
    try:
        status = ctx.get("/api/status")
        report["is_admin"] = bool(status.get("is_admin"))
        report["phase"] = status.get("phase")
        selfcheck = status.get("selfcheck") or {}
        report["selfcheck_failed"] = selfcheck.get("failed")
    except (ApiFailure, TransportError) as exc:
        report["note"] = f"读 /api/status 失败：{exc}"
    try:
        full = ctx.get("/api/selfcheck") or {}
        result = full.get("result") or {}
        executable = result.get("executable")
        if executable:
            report["executable"] = executable
            report["console_python"] = result.get("python")
            report["console_python_ok"] = bool(result.get("ok")) and report[
                "executable"
            ] == _normalize_python(sys.executable)
    except (ApiFailure, TransportError):
        pass
    return report


def _doctor_env_verdict(env: dict) -> tuple:
    """环境相关的 verdict + next_steps（不含控制台部分）。

    verdict 的语义（agent 按它决定要不要动手）：
      * ``env_ok``             —— 本机已保存合格解释器，什么都不用做；
      * ``env_ok_unsaved``     —— 环境能用，但**还没保存**到 log/env.json：
                                  换 agent/换 shell 时可能又用错解释器，
                                  所以下一步是 `hsctl env --pin <当前解释器>`（一条命令）；
      * ``env_deps_missing``   —— 找到 3.12 但缺包 → 给精确到解释器的 pip 命令；
      * ``env_no_valid_interpreter`` —— 全都不合格 → 给 bootstrap（conda/venv）。
    """
    current = env.get("current") or {}
    pinned = env.get("pinned") or {}
    pinned_path = _normalize_python(_read_env_file().get("interpreter"))
    steps = []

    if pinned_path and pinned.get("ok"):
        return "env_ok", steps

    # 合格但不等于"已保存"的那一个：优先当前解释器，其次任何合格候选。
    ok_candidates = [c for c in (env.get("candidates") or []) if c.get("ok")]
    if current.get("ok"):
        steps.append(f'hsctl env --pin "{current.get("path")}"')
        steps.append(
            "# ↑ 把本机解释器保存到 log/env.json（log/ 不入库，每台机器各存一份）"
        )
        return "env_ok_unsaved", steps
    if pinned_path and not pinned.get("ok"):
        steps.append(
            "# log/env.json 里固定的解释器现在不可用：" + str(pinned.get("reason"))
        )
    if len(ok_candidates) == 1:
        steps.append(f'hsctl env --pin "{ok_candidates[0]["path"]}"')
        steps.append("python hsctl.py --json doctor    # 复验到 env_ok")
        return "env_ok_unsaved", steps
    if len(ok_candidates) > 1:
        steps.append("# 本机有多个合格解释器，选一个保存（本机专属 log/env.json）：")
        for item in ok_candidates:
            steps.append(f'hsctl env --pin "{item["path"]}"')
        return "env_ok_unsaved", steps
    if current.get("missing"):
        pkgs = " ".join(MODULE_PACKAGES.get(m, m) for m in current["missing"])
        py = current.get("path") or sys.executable
        steps.append(
            f'"{py}" -m pip install {pkgs} -i https://pypi.tuna.tsinghua.edu.cn/simple'
        )
        return "env_deps_missing", steps
    steps.append("conda create -p <你的路径>\\envs\\HTL python=3.12    # 有 conda 时")
    steps.append("python -m venv .venv                              # 没有 conda 时")
    steps.append(
        '"<新解释器>" -m pip install -r requirements.txt '
        "-i https://pypi.tuna.tsinghua.edu.cn/simple"
    )
    steps.append('hsctl env --pin "<新解释器>"')
    return "env_no_valid_interpreter", steps


def cmd_doctor(args, ctx: Ctx) -> int:
    """一条命令给出"环境/控制台现在什么状态、下一步执行什么"。"""
    env = interpreter_report()
    pinned_path = _normalize_python(_read_env_file().get("interpreter"))
    verdict, steps = _doctor_env_verdict(env)
    env_saved = verdict == "env_ok"

    console = _console_report(ctx)
    if env_saved:
        if not console["running"]:
            verdict = "console_not_running"
            steps = [
                "hsctl serve start --via-task   # 不弹 UAC；任务没注册就先 serve install-task"
            ]
        elif console.get("is_admin") is False:
            verdict = "console_not_admin"
            steps = [
                "hsctl serve task-status",
                "hsctl serve stop              # 自停通道（不需要提权）",
                "hsctl serve start --via-task  # 提权重启，点击才会生效",
            ]
        elif console.get("console_python_ok") is False:
            verdict = "env_wrong_interpreter"
            steps = [
                'hsctl env --pin "<正确的 python.exe 路径>"',
                "hsctl serve stop",
                "hsctl serve start --via-task",
            ]

    payload = {
        # ok 的语义：环境可用（是否已保存见 verdict / env_saved）。控制台没跑不算"环境坏"。
        "ok": verdict in ("env_ok", "env_ok_unsaved", "console_not_running"),
        "verdict": verdict,
        "env_saved": env_saved,
        "this_python": env.get("current"),
        "pinned": env.get("pinned"),
        "pinned_path": pinned_path or None,
        "pinned_source": env.get("pinned_source"),
        "env_file": env.get("env_file"),
        "env_template": env.get("template"),
        "candidates": env.get("candidates"),
        "console": console,
        "next_steps": steps,
        "note": (
            "本机专属解释器配置保存在 log/env.json（log/ 不入库，每台机器/每个 clone "
            "各自保存一份）；模板见 log/env.example.json。"
            "保存/更换都用 hsctl env --pin <路径>。"
        ),
    }
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    print(f"环境：{'✓' if verdict.startswith('env_ok') else '✗'} {verdict}")
    cur = env.get("current") or {}
    version = ".".join(str(v) for v in (cur.get("version") or [])) or "?"
    print(
        f"  当前解释器：{cur.get('path')}（Python {version}）"
        f"{'  合格' if cur.get('ok') else '  不合格：' + str(cur.get('reason'))}"
    )
    if env.get("pinned"):
        print(
            f"  已保存：{env['pinned'].get('path')}（来源 {env.get('pinned_source')}）"
        )
    else:
        print("  已保存：（还没有：log/env.json 不存在或没有 interpreter）")
    if cur.get("missing"):
        print(
            "  缺依赖：" + "、".join(MODULE_PACKAGES.get(m, m) for m in cur["missing"])
        )
    print(
        f"控制台：{'运行中 ' + str(console.get('url')) if console['running'] else '没在运行'}"
        + (
            f"  管理员：{'是' if console.get('is_admin') else '否'}"
            f"（控制台 Python {console.get('console_python') or '?'}）"
            if console["running"]
            else ""
        )
    )
    if steps:
        print("下一步：")
        for step in steps:
            print(f"  {step}")
    print(f"  （{payload['note']}）")
    return EXIT_OK


def cmd_env(args, ctx: Ctx) -> int:
    """看/固定/探测本机的 Python 解释器（保存到 log/env.json）。"""
    action = getattr(args, "env_action", None) or "show"
    if action == "discover":
        candidates = []
        for path in discover_candidates():
            candidates.append({"path": path, **verify_python(path)})
        payload = {
            "ok": True,
            "template": str(ENV_TEMPLATE),
            "env_file": str(ENV_FILE),
            "candidates": candidates,
        }
        if ctx.json:
            print(json.dumps(payload, ensure_ascii=False))
            return EXIT_OK
        if not candidates:
            print(
                "没有探测到候选解释器。用 hsctl env --pin <python.exe 路径> 手动指定。"
            )
            return EXIT_OK
        for item in candidates:
            flag = "✓" if item["ok"] else "✗"
            version = ".".join(str(v) for v in (item.get("version") or [])) or "?"
            print(
                f"  {flag} {item['path']}  ({version}) {'' if item['ok'] else item['reason']}"
            )
        return EXIT_OK

    if action == "pin":
        target = str(getattr(args, "path", "") or "").strip()
        if target.lower() == "auto" or not target:
            resolved = resolve_interpreter()
            if not resolved.get("path"):
                raise ApiFailure(
                    "没有探测到合格解释器（Python 3.12 + 依赖齐全）。\n"
                    "  先看候选：hsctl env --discover\n"
                    "  或手动指定：hsctl env --pin <python.exe 路径>"
                )
            target = resolved["path"]
        record = verify_python(target)
        if not record.get("ok"):
            raise ApiFailure(
                f"这个解释器不合格：{target}\n  原因：{record.get('reason')}\n"
                "  （要求 Python 3.12 + requirements.txt 里的依赖齐全）"
            )
        if not _persist_env(record.get("executable") or target, "hsctl env --pin"):
            raise ApiFailure(f"写不了 {ENV_FILE}（检查目录权限）")
        _cache_env_probe(record)
        payload = {
            "ok": True,
            "interpreter": record.get("executable") or target,
            "env_file": str(ENV_FILE),
            "message": f"已保存本机解释器到 {ENV_FILE}",
            "note": "这是本机专属配置（log/ 不入库）；换机器/换 agent 各自保存一份。",
        }
        if ctx.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"✓ {payload['message']}")
            print(
                f"  {record.get('executable') or target}"
                f"  Python {'.'.join(str(v) for v in (record.get('version') or []))}"
            )
            print(f"  {payload['note']}")
        return EXIT_OK

    report = interpreter_report()
    if ctx.json:
        print(json.dumps(report, ensure_ascii=False))
        return EXIT_OK
    cur = report["current"]
    print(f"当前解释器：{cur['path']}")
    print(
        f"  版本：{'.'.join(str(v) for v in (cur.get('version') or [])) or '?'}"
        f"  合格：{'是' if cur.get('ok') else '否'}"
    )
    if not cur.get("ok"):
        print(f"  原因：{cur.get('reason')}")
    pinned = report.get("pinned")
    print(
        f"已固定解释器（log/env.json）：{(pinned or {}).get('path') or '（无）'}"
        f"  来源：{report.get('pinned_source')}"
    )
    ok_candidates = [c for c in report["candidates"] if c.get("ok")]
    print(
        f"候选：{len(report['candidates'])} 个，其中合格 {len(ok_candidates)} 个"
        f"（hsctl env --discover 看全部）"
    )
    if ok_candidates:
        print('固定其中一个：hsctl env --pin "' + ok_candidates[0]["path"] + '"')
    return EXIT_OK


# --------------------------------------------------------------------- 命令
def cmd_status(args, ctx: Ctx) -> int:
    if getattr(args, "watch", 0):
        return _watch_status(args, ctx)
    s = ctx.get("/api/status")
    ctx.emit(s, render_status(s, ctx.base))
    return EXIT_OK


def _watch_status(args, ctx: Ctx) -> int:
    interval = max(0.5, float(args.watch))
    try:
        while True:
            s = ctx.get("/api/status")
            if ctx.json:
                print(json.dumps(s, ensure_ascii=False), flush=True)
            else:
                print(_one_line(s), flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        return EXIT_OK


def cmd_log(args, ctx: Ctx) -> int:
    payload = ctx.get("/api/logs", {"seq": 0})
    lines = list(payload.get("lines") or [])
    seq = int(payload.get("seq") or 0)
    if args.lines:
        lines = lines[-int(args.lines) :]
    for entry in lines:
        print(_log_line(entry, ctx.json))
    if not args.follow:
        return EXIT_OK
    # 跟随：/api/logs?seq= 是增量接口，只回 seq 之后的新行。
    try:
        while True:
            time.sleep(max(0.2, float(args.interval)))
            payload = ctx.get("/api/logs", {"seq": seq})
            seq = int(payload.get("seq") or seq)
            for entry in payload.get("lines") or []:
                print(_log_line(entry, ctx.json), flush=True)
    except KeyboardInterrupt:
        print()
        return EXIT_OK


def cmd_selfcheck(args, ctx: Ctx) -> int:
    payload = ctx.post("/api/selfcheck") if args.refresh else ctx.get("/api/selfcheck")
    result = payload.get("result") or {}
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    print(
        f"环境自检：{'✅ 通过' if result.get('ok') else '❌ 未通过'}"
        f"  ({result.get('summary') or ''})"
    )
    for item in result.get("checks") or []:
        status = str(item.get("status") or "")
        mark = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(status, "•")
        print(
            f"  {mark} {item.get('label') or item.get('name') or ''}"
            f"  {item.get('detail') or item.get('message') or ''}".rstrip()
        )
    return EXIT_OK


def cmd_start(args, ctx: Ctx) -> int:
    return _ok_message(
        ctx, ctx.post("/api/start", {"keep_score": bool(args.keep_score)})
    )


def cmd_resume(args, ctx: Ctx) -> int:
    """浮窗「恢复」的语义：续跑，战绩继续累计（不清零）。"""
    return _ok_message(ctx, ctx.post("/api/start", {"keep_score": True}))


def cmd_prepare(args, ctx: Ctx) -> int:
    return _ok_message(ctx, ctx.post("/api/prepare", {}))


def cmd_stop(args, ctx: Ctx) -> int:
    return _ok_message(
        ctx, ctx.post("/api/stop", {"mode": "after_game" if args.after_game else "now"})
    )


def cmd_cancel(args, ctx: Ctx) -> int:
    """急停：叫停正在跑的手动流程（进对战 / 重启后的等待）以及自动化。

    与 `stop` 的关键区别：`stop` 只管 FSM 自动化，自动化没在跑时它会回
    「当前没有正在运行的自动化」，而手动 `enter-battle` 的等待循环还在反复点
    画面中心 —— 用户喊"取消"却停不下来就是这事。这条命令把两边一起停，并且
    **立刻生效**：置位控制台的急停开关，等待循环下一轮（≤0.5 秒）退出，
    之后不再点任何坐标。
    """
    payload = ctx.post("/api/cancel", {})
    if not payload.get("ok"):
        return _fail(ctx, "api", payload.get("error") or "取消失败")
    return _ok_message(ctx, payload)


def cmd_calibrate(args, ctx: Ctx) -> int:
    body = {}
    target = getattr(args, "target", None)
    if target:
        body["target"] = target
    return _ok_message(ctx, ctx.post("/api/calibrate", body))


# ---------------------------------------------------------------- 界面识别
def _score_text(item) -> str:
    """一行「命中 1.00」/「未命中 0.31」/「—（原因）」。

    刻意用纯中文而不是 ✅/❌：中文 Windows 控制台是 GBK，那些符号不在编码里
    （打印会抛 UnicodeEncodeError），而 agent 的终端未必是 UTF-8。
    """
    if not isinstance(item, dict):
        return "—"
    score = item.get("score")
    if score is None or float(score) < 0:
        reason = item.get("reason") or "不可用"
        return f"—（{reason}）"
    return f"{'命中' if item.get('visible') else '未命中'} {float(score):.2f}"


def cmd_ui(args, ctx: Ctx) -> int:
    """认一次当前界面（只读）：主界面 / 选择套牌界面 / 没认出 + 两个匹配分。

    `--learn` 是唯一会写盘的分支：按当前（可校准）框从当前画面重裁识别模板。
    """
    if args.learn:
        return _ok_message(ctx, ctx.post("/api/ui_learn", {"kind": args.learn}))
    params = {"frame": getattr(args, "frame", None) or None}
    payload = ctx.get("/api/ui_state", params)
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK if payload.get("ok") else EXIT_API
    if not payload.get("ok"):
        print(f"[失败] {payload.get('error')}")
        return EXIT_API
    ui = payload.get("ui") or {}
    traditional = ui.get("traditional") or {}
    start = ui.get("start") or {}
    deck = ui.get("deck") or {}
    print(f"当前界面：{payload.get('state_label')}（state={ui.get('state')}）")
    print(f"  传统对战  {_score_text(traditional)}   框={traditional.get('box')}")
    print(f"  开始按钮  {_score_text(start)}   框={start.get('box')}")
    print(f"  卡组格    {deck.get('slots_found')}/9 格有名牌文字（≥6 才算卡组界面）")
    if ui.get("note"):
        print(f"  注：{ui['note']}")
    index = payload.get("deck_index")
    if index:
        print(f"  自动选卡组：已停用（保存值 {index} 已忽略；要选用 deck-click N）")
    else:
        print("  自动选卡组：已停用（要选用 deck-click N / enter-battle --deck N）")
    if payload.get("frame_path"):
        print(f"  这一帧已存：{payload['frame_path']}（可以直接读这张图核对）")
    return EXIT_OK


def cmd_deck(args, ctx: Ctx) -> int:
    """查看/清理旧的「自动选卡组」设置（自动化已停用自动点卡组）。

    `deck --clear` 清掉本机旧值；`deck N` 会被 API 拒绝并提示改用
    `deck-click N`（立即选）或 `enter-battle --deck N`（进对战时选）。
    """
    index = getattr(args, "index", None)
    if getattr(args, "clear", False) or (index is not None and int(index) <= 0):
        return _ok_message(ctx, ctx.post("/api/deck", {"clear": True}))
    if index is None:
        payload = ctx.get("/api/ui_state")
        current = payload.get("deck_index")
        message = (
            "自动选卡组：已停用（不会再每局自动点卡组）"
            + (f"；保存值 {current} 已忽略，可用 deck --clear 清掉" if current else "")
        )
        if ctx.json:
            print(
                json.dumps(
                    {"ok": True, "deck_index": current, "message": message},
                    ensure_ascii=False,
                )
            )
        else:
            print(message)
            print(
                "  要现在选：python hsctl.py deck-click 8    "
                "进对战时选：python hsctl.py enter-battle --deck 8"
            )
        return EXIT_OK
    if not 1 <= int(index) <= 9:
        print(f"卡组序号必须是 1-9（当前页只有 9 格），收到 {index}", file=sys.stderr)
        return EXIT_USAGE
    payload = ctx.post("/api/deck", {"index": int(index)})
    if not payload.get("ok"):
        print(f"[失败] {payload.get('error') or '自动选卡组已停用'}", file=sys.stderr)
        return EXIT_API
    return _ok_message(ctx, payload)


def cmd_deck_click(args, ctx: Ctx) -> int:
    """快速选卡组：确认在选牌界面后直接点第 N 格（同步、秒级，不走状态机）。

    单格保护：那一格名牌上没白字（不像卡组/不在选牌界面）就不点并说明原因。
    """
    try:
        index = int(args.index)
    except (TypeError, ValueError):
        print("卡组序号必须是 1-9 的整数", file=sys.stderr)
        return EXIT_USAGE
    if not 1 <= index <= 9:
        print(f"卡组序号必须是 1-9（当前页只有 9 格），收到 {index}", file=sys.stderr)
        return EXIT_USAGE
    payload = ctx.post("/api/deck_click", {"index": index})
    if not payload.get("ok"):
        return _fail(ctx, "api", payload.get("error") or "没能点卡组")
    return _ok_message(ctx, payload)


def cmd_mode(args, ctx: Ctx) -> int:
    """手动切换对战模式：hsctl mode wild|standard|casual（狂野/标准/休闲）。

    前提：脚本停在「选择套牌」界面、自动化未运行。同步执行约 2-3 秒，
    点右上角打开模式菜单后点对应模式；坐标可在 ui_config.json 的
    mode_click 段覆盖。
    """
    mode = str(args.mode).strip().lower()
    aliases = {
        "wild": "wild",
        "standard": "standard",
        "casual": "casual",
        "狂野": "wild",
        "标准": "standard",
        "休闲": "casual",
    }
    if mode not in aliases:
        print("模式必须是 wild/standard/casual（或 狂野/标准/休闲）", file=sys.stderr)
        return EXIT_USAGE
    payload = ctx.post("/api/mode", {"mode": aliases[mode]})
    if not payload.get("ok"):
        return _fail(ctx, "api", payload.get("error") or "没能切换模式")
    return _ok_message(ctx, payload)


def cmd_watchdog(args, ctx: Ctx) -> int:
    """中断重启看门狗：日志长时间不变就重启炉石并把这一局重开。

    用法：
      hsctl watchdog on|off            开关
      hsctl watchdog status           看当前状态（默认）
      hsctl watchdog set --stale 300 --max-restarts 3 --window 600

    与「存活检测」（hsctl liveness）的区别：存活检测判定无响应后**停止**并告警，
    等人处理；本开关打开后改为**重启炉石并自动重新进入对战**，无人值守自己爬
    回来。开关打开时由它接管「日志停滞」，存活检测那条自动停止不再触发。
    """
    sub = getattr(args, "watchdog_cmd", None) or "status"
    if sub == "on":
        return _ok_message(ctx, ctx.post("/api/watchdog", {"enabled": True}))
    if sub == "off":
        return _ok_message(ctx, ctx.post("/api/watchdog", {"enabled": False}))
    if sub == "set":
        body = {}
        if getattr(args, "stale", None) is not None:
            body["stale_seconds"] = args.stale
        if getattr(args, "max_restarts", None) is not None:
            body["max_restarts"] = args.max_restarts
        if getattr(args, "window", None) is not None:
            body["window_seconds"] = args.window
        if not body:
            raise ApiFailure("没有要改的字段（用 --stale / --max-restarts / --window）")
        return _ok_message(ctx, ctx.post("/api/watchdog", body))

    payload = ctx.get("/api/status")
    wd = payload.get("watchdog") or {}
    if ctx.json:
        print(json.dumps({"ok": True, "watchdog": wd}, ensure_ascii=False))
        return EXIT_OK
    state_labels = {
        "disabled": "已关闭",
        "ok": "正常（日志在更新）",
        "idle": "自动化未运行",
        "stale": "日志停滞（即将重启）",
        "settling": "重启后宽限期中",
        "exhausted": "重启次数用尽（已停手等人工）",
        "unknown": "无法判定",
        "error": "读取失败",
    }
    print("中断重启看门狗")
    print(f"  开关      : {'开' if wd.get('enabled') else '关'}")
    print(f"  状态      : {state_labels.get(wd.get('status'), wd.get('status'))}")
    print(f"  触发阈值  : 日志停滞 {wd.get('stale_seconds', 0):.0f} 秒")
    print(
        f"  限流      : {wd.get('window_seconds', 0) / 60:.0f} 分钟内最多 "
        f"{wd.get('max_restarts', 0)} 次（已用 {wd.get('restarts_in_window', 0)} 次，"
        f"累计 {wd.get('restarts_total', 0)} 次）"
    )
    age = wd.get("log_age")
    print(f"  日志停滞  : {'—' if age is None else f'{age:.0f} 秒'}")
    if wd.get("restart_running"):
        print("  正在重启  : 是")
    if wd.get("message"):
        print(f"  最近事件  : {wd['message']}")
    return EXIT_OK


def cmd_queue(args, ctx: Ctx) -> int:
    """待做命令队列：一次性提交多条命令，控制台按顺序执行（断线不影响）。

    用法：
      hsctl queue run "stop mode=after_game" "wait-stopped" "deck-click 1" "start"
      hsctl queue status
      hsctl queue cancel
    支持：stop [mode=after_game|now] / wait-stopped [timeout=秒] /
          deck 序号（已停用，会被拒绝）| deck-click 序号 / mode wild|standard|casual /
          prepare / start [keep_score=1] / delay 秒
    某一步失败即停，剩余标记 skipped；提交前会整体校验语法，写错一条都不收。
    """
    sub = getattr(args, "queue_cmd", "status")
    if sub == "status":
        payload = ctx.get("/api/queue")
        q = payload.get("queue") or {}
        if ctx.json:
            print(json.dumps(payload, ensure_ascii=False))
            return EXIT_OK
        print(f"running: {q.get('running')} | {q.get('message') or ''}")
        for i, item in enumerate(q.get("items") or []):
            mark = {
                "pending": "…",
                "running": "▶",
                "ok": "✓",
                "failed": "✗",
                "skipped": "－",
                "cancelled": "⊘",
            }.get(item.get("status"), "?")
            print(f"  {mark} [{i + 1}] {item.get('action')} {item.get('args') or ''}")
        return EXIT_OK
    if sub == "cancel":
        return _ok_message(ctx, ctx.post("/api/queue/cancel", {}))
    # run：把剩余位置参数当 step 字符串提交
    steps = list(getattr(args, "steps", []) or [])
    if not steps:
        print(
            '用法：hsctl queue run "stop mode=after_game" "wait-stopped" "deck-click 1" "start"',
            file=sys.stderr,
        )
        return EXIT_USAGE
    payload = ctx.post("/api/queue", {"steps": steps})
    if not payload.get("ok"):
        return _fail(ctx, "api", payload.get("error") or "队列提交失败")
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"✓ {payload.get('message')}")
    if getattr(args, "no_wait", False):
        return EXIT_OK
    # 轮询轻量进度接口直到跑完
    while True:
        q = (ctx.get("/api/queue") or {}).get("queue") or {}
        if not q.get("running"):
            if ctx.json:
                print(json.dumps({"ok": True, "queue": q}, ensure_ascii=False))
            else:
                print(
                    f"{'✅' if all(i.get('status') == 'ok' for i in q.get('items') or []) else '❌'} "
                    f"{q.get('message')}"
                )
            return (
                EXIT_OK
                if not q.get("items")
                or all(i.get("status") == "ok" for i in q["items"])
                else EXIT_API
            )
        time.sleep(2)


def cmd_enter_battle(args, ctx: Ctx) -> int:
    """一键进入对战：拉起炉石 → 等主界面 → 点「传统对战」→ 选卡组 →（可选）点「开始」。

    认不出界面时只会点画面中心（安全点），到超时就退出并说明原因 —— 不会盲点。
    默认**不点「开始」**（那等于开始排队）；要一路打到排队加 `--start`。
    """
    body = {"click_start": bool(getattr(args, "start", False))}
    # 只有用户显式给了 --interval 才下发：原来默认发 3.0，把控制台默认的
    # 0.5 秒轮询盖掉了，界面认出来要慢一拍，「取消」也要多等 3 秒。
    if getattr(args, "interval", None):
        body["interval"] = float(args.interval)
    deck = getattr(args, "deck", None)
    if deck is not None:
        if not 1 <= int(deck) <= 9:
            print(f"--deck 必须是 1-9，收到 {deck}", file=sys.stderr)
            return EXIT_USAGE
        body["deck_index"] = int(deck)
    if getattr(args, "wait_timeout", None):
        body["timeout"] = float(args.wait_timeout)
    payload = ctx.post("/api/enter_battle", body)
    if not payload.get("ok"):
        return _fail(ctx, "api", payload.get("error") or "进入对战没能开始")
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"✓ {payload.get('message')}")
    if getattr(args, "no_wait", False):
        return EXIT_OK
    return _wait_enter_battle(ctx)


def _wait_enter_battle(ctx: Ctx) -> int:
    """等这次「进入对战」跑完（轮询便宜的进度接口，不反复截屏）。

    用户在终端按 Ctrl+C 时**必须先把控制台那边喊停**：真正的点击跑在控制台的
    后台线程里，只杀掉本地 CLI 的话它还在那儿一遍遍点画面中心 —— 这就是
    「让它取消它不听话」的根因。所以这里捕获 KeyboardInterrupt → POST
    /api/cancel，确认急停已生效后才退出。
    """
    try:
        return _wait_enter_battle_loop(ctx)
    except KeyboardInterrupt:
        # 兜底：正常做法是**在任何地方按 Ctrl+Q**（控制台常驻的全局热键），
        # 这里只防"终端里手滑按了 Ctrl+C 就以为停了"——点击跑在控制台线程里，
        # 本地退出等于什么都没停，所以要顺手替用户发一次取消。
        print("\n收到终端中断，正在替你在控制台叫停……", file=sys.stderr)
        try:
            result = ctx.post("/api/cancel", {"source": "cli-interrupt"})
            text = result.get("message") or "已发送取消请求"
        except Exception as exc:
            text = f"取消请求没发出去（{exc}）；请手动执行 python hsctl.py cancel"
        print(f"  {text}", file=sys.stderr)
        return EXIT_API


def _wait_enter_battle_loop(ctx: Ctx) -> int:
    last_stage = ""
    deadline = time.monotonic() + ENTER_BATTLE_WAIT_SECONDS
    while time.monotonic() < deadline:
        state = (ctx.get("/api/enter_battle") or {}).get("enter_battle") or {}
        stage = str(state.get("stage") or "")
        if stage != last_stage and not ctx.json and stage:
            print(f"  … {stage}")
            last_stage = stage
        if not state.get("running"):
            if ctx.json:
                print(json.dumps(state, ensure_ascii=False))
            else:
                print(f"{'✅' if state.get('ok') else '❌'} {state.get('message')}")
                for step in state.get("steps") or []:
                    at = f" @{step['at']}" if step.get("at") else ""
                    print(f"    · {step.get('stage')}：{step.get('detail') or ''}{at}")
            return EXIT_OK if state.get("ok") else EXIT_API
        time.sleep(ENTER_BATTLE_POLL_SECONDS)
    print(
        "等待超时：后台可能还在跑，用 python hsctl.py log -f 看进度。", file=sys.stderr
    )
    return EXIT_API


def cmd_overlay(args, ctx: Ctx) -> int:
    action = args.action
    if action == "toggle":
        return _ok_message(ctx, ctx.post("/api/overlay", {}))
    running = bool((ctx.get("/api/status") or {}).get("overlay_running"))
    want = action == "on"
    if running == want:
        if ctx.json:
            print(
                json.dumps(
                    {"ok": True, "enabled": running, "message": "已是目标状态"},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"浮窗已经{'开着' if running else '关着'}（未做改动）")
        return EXIT_OK
    return _ok_message(ctx, ctx.post("/api/overlay", {}))


def cmd_regions(args, ctx: Ctx) -> int:
    payload = ctx.get("/api/regions")
    result = payload.get("result") or {}
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    mapping = result.get("mapping") or {}
    if mapping.get("available"):
        client = mapping.get("client") or {}
        print(
            f"客户区 {client.get('width')}×{client.get('height')}"
            f" @屏幕 {tuple(mapping.get('screen_origin') or [])}"
        )
    for item in result.get("checks") or []:
        status = str(item.get("status") or "")
        mark = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(status, "•")
        print(
            f"  {mark} {item.get('label') or ''}  {item.get('detail') or ''}".rstrip()
        )
    print("（截图已写出：见日志）")
    return EXIT_OK


# ---------------------------------------------------------------- 出错反馈包
def cmd_feedback(args, ctx: Ctx) -> int:
    """导出/查看本次运行的错误反馈包（重试/报错文本 + 对应截图）。

    export：把本次运行的重试/报错 + 截图 + 结构化日志打包成 zip，发给作者排查。
    status：看看这次运行捕获了多少重试/报错与截图。
    """
    if args.action == "status":
        payload = ctx.get("/api/feedback/status") or {}
        if ctx.json:
            print(json.dumps(payload, ensure_ascii=False))
            return EXIT_OK
        print(
            f"本次运行：重试 {payload.get('retry_incidents', 0)} 处 · "
            f"报错 {payload.get('error_incidents', 0)} 处 · "
            f"截图 {payload.get('screenshots', 0)} 张"
        )
        if payload.get("streak"):
            print(
                f"  当前连续重试：{payload.get('streak')}"
                f"（{payload.get('streak_code') or ''}）"
            )
        return EXIT_OK
    payload = ctx.post("/api/feedback/export", {})
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    if payload.get("ok"):
        print(f"✓ 已导出本次出错截屏与日志：{payload.get('path')}")
        print(
            f"  已自动截取出错画面 {payload.get('screenshots', 0)} 张 / "
            f"{payload.get('incidents', 0)} 处异常，直接发给作者即可。"
        )
    else:
        print(f"✗ 导出失败：{payload.get('error')}", file=sys.stderr)
        return EXIT_API
    return EXIT_OK


# ---------------------------------------------------------------- 抓图（看画面）
def cmd_screenshot(args, ctx: Ctx) -> int:
    """按需抓一帧当前画面并存成 PNG（只截图：不点击、不移动鼠标）。

    这是给 agent「看画面」用的：拿到 path 之后直接读那张 PNG 就能看到脚本眼里的
    情况。kind=client（默认）只截炉石客户区。
    """
    body = {"kind": args.kind}
    if args.max_width:
        body["max_width"] = int(args.max_width)
    if args.out:
        body["out"] = args.out
    if getattr(args, "points", None):
        body["points"] = args.points
    if args.watch:
        return _watch_screenshot(args, ctx, body)
    payload = ctx.post("/api/screenshot", body)
    _render_shot(ctx, payload)
    if args.open:
        _open_path(payload.get("path"))
    return EXIT_OK


def _render_shot(ctx: Ctx, payload: dict, compact: bool = False) -> None:
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    if compact:
        print(
            f"  {datetime.now():%H:%M:%S}  "
            f"{payload.get('width')}×{payload.get('height')}  "
            f"{payload.get('path')}",
            flush=True,
        )
        return
    print(f"✓ 已抓图：{payload.get('path')}")
    print(
        f"  {payload.get('width')}×{payload.get('height')}   类型 {payload.get('kind')}"
    )
    if payload.get("latest"):
        print(f"  固定路径（每次都覆盖这一个）：{payload.get('latest')}")
    mapping = payload.get("mapping") or {}
    if mapping.get("available"):
        client = mapping.get("client") or {}
        print(
            f"  客户区 {client.get('width')}×{client.get('height')}"
            f" @屏幕 {tuple(mapping.get('screen_origin') or [])}"
        )
    if payload.get("note"):
        print(f"  ℹ️ {payload.get('note')}")


def _watch_screenshot(args, ctx: Ctx, body: dict) -> int:
    """持续抓图，每次覆盖同一个文件 —— 适合"实时盯着画面"。

    固定文件名（默认 agent_live.png）让看图软件/agent 反复读同一个路径就是最新的。
    带上 quiet，免得把网页日志刷满。
    """
    body = dict(body, quiet=True)
    if not body.get("out"):
        body["out"] = "agent_live.png"
    interval = max(0.3, float(args.interval))
    print(
        f"持续抓图：每 {interval:g}s 覆盖一张，Ctrl+C 退出"
        f"（看图工具打开 tmp_screens\\{body['out']} 即可实时刷新）"
    )
    try:
        while True:
            payload = ctx.post("/api/screenshot", body)
            _render_shot(ctx, payload, compact=not ctx.json)
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
        return EXIT_OK


def _open_path(path) -> None:
    if not path:
        return
    try:
        if os.name == "nt":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        print(f"  （打开图片失败：{exc}）")


def cmd_restart_hearthstone(args, ctx: Ctx) -> int:
    if not args.yes:
        if not sys.stdin.isatty():
            raise ApiFailure(
                "这是高风险命令（会结束炉石进程并清空其日志目录），"
                "非交互环境下请显式加 --yes"
            )
        try:
            answer = input("将结束炉石进程并清空它的日志目录，确认继续？[y/N] ")
        except (EOFError, KeyboardInterrupt):
            # isatty 为真但 stdin 已经 EOF/被打断（agent、重定向、Ctrl+C）：
            # 拿不到明确答复就按「不确认」处理，绝不能默认往下走。
            print("\n没拿到确认，已取消。")
            return EXIT_OK
        if answer.strip().lower() not in ("y", "yes"):
            print("已取消。")
            return EXIT_OK
    payload = ctx.post("/api/restart_hearthstone", {"confirm": True})
    if not payload.get("ok"):
        return _fail(ctx, "api", payload.get("error") or "重启炉石没能开始")
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"✓ {payload.get('message')}")
    if getattr(args, "no_wait", False):
        return EXIT_OK
    return _wait_restart_hearthstone(ctx)


def _wait_restart_hearthstone(ctx: Ctx) -> int:
    """等到重启真正完成（= 认到主界面、「传统对战」可点），不靠调用方自己 sleep。

    只轮询轻量的进度接口；超时就把最后阶段说清楚并返回失败，不谎报成功。
    """
    last_stage = ""
    deadline = time.monotonic() + RESTART_WAIT_SECONDS
    while time.monotonic() < deadline:
        state = (ctx.get("/api/restart_hearthstone") or {}).get("restart") or {}
        stage = str(state.get("stage") or "")
        if stage != last_stage and not ctx.json and stage:
            print(f"  … {stage}")
            last_stage = stage
        if not state.get("running"):
            if ctx.json:
                print(json.dumps(state, ensure_ascii=False))
            else:
                print(f"{'✅' if state.get('ok') else '❌'} {state.get('message')}")
            return EXIT_OK if state.get("ok") else EXIT_API
        time.sleep(RESTART_POLL_SECONDS)
    print(
        "等待超时：后台可能还在跑，用 python hsctl.py log -f 看进度。", file=sys.stderr
    )
    return EXIT_API


# ---- 配置 / 参数：show 都复用 /api/status 里已经算好的那一节
def _section(ctx: Ctx, key: str) -> dict:
    s = ctx.get("/api/status")
    value = s.get(key)
    return value if isinstance(value, dict) else {}


def _print_section(ctx: Ctx, key: str, title: str, fields) -> int:
    section = _section(ctx, key)
    if ctx.json:
        print(json.dumps(section, ensure_ascii=False))
        return EXIT_OK
    print(f"{title}：")
    for name, label in fields:
        print(f"  {label:<18} {_num(section.get(name))}")
    return EXIT_OK


def cmd_config_show(args, ctx: Ctx) -> int:
    cfg = (ctx.get("/api/status") or {}).get("config") or {}
    if ctx.json:
        print(json.dumps(cfg, ensure_ascii=False))
        return EXIT_OK
    print(f"用户 ID   {cfg.get('name') or '（未填写）'}")
    print(f"日志目录  {cfg.get('log_root') or '（未填写）'}")
    return EXIT_OK


def cmd_config_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    if args.name is not None:
        body["name"] = args.name
    if args.log_root is not None:
        body["log_root"] = args.log_root
    if len(body) == 1:
        raise ApiFailure("至少要给 --name 或 --log-root 之一")
    return _ok_message(ctx, ctx.post("/api/config", body))


def cmd_delays_show(args, ctx: Ctx) -> int:
    delays = _section(ctx, "delays")
    if ctx.json:
        print(json.dumps(delays, ensure_ascii=False))
        return EXIT_OK
    print("当前延时（= 默认值叠加 ui_config.json 的 delays 段）：")
    for key in DELAY_KEYS:
        if key in delays:
            print(
                f"  {key:<38} {_num(delays[key]):>8}"
                f"   {EFFECT_LABEL.get(DELAY_EFFECT[key], '')}"
            )
    return EXIT_OK


def cmd_delays_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    for pair in args.pairs:
        if "=" not in pair:
            raise ApiFailure(f"要写成 键=值 的形式，收到：{pair}")
        key, _, raw = pair.partition("=")
        key = key.strip()
        if key not in DELAY_KEYS:
            raise ApiFailure(
                f"未知延时字段：{key}\n可用字段：\n  " + "\n  ".join(DELAY_KEYS)
            )
        try:
            body[key] = float(raw.strip())
        except ValueError:
            raise ApiFailure(f"{key} 必须是数字，收到：{raw.strip()!r}")
    return _ok_message(ctx, ctx.post("/api/delays", body))


def cmd_human_like_show(args, ctx: Ctx) -> int:
    return _print_section(
        ctx,
        "human_like",
        "活人感",
        (
            ("enabled", "总开关"),
            ("hand_hover_enabled", "看卡牌（随机延时）"),
            ("minion_hover_enabled", "看随从"),
            ("post_delay_min", "操作后延时下限"),
            ("post_delay_max", "操作后延时上限"),
            ("hover_min", "悬停下限"),
            ("hover_max", "悬停上限"),
            ("minion_hover_min", "随从悬停下限"),
            ("minion_hover_max", "随从悬停上限"),
        ),
    )


def cmd_human_like_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    mapping = (
        ("enabled", args.enabled),
        ("hand_hover_enabled", args.hand_hover),
        ("minion_hover_enabled", args.minion_hover),
        ("post_delay_min", args.post_delay_min),
        ("post_delay_max", args.post_delay_max),
        ("hover_min", args.hover_min),
        ("hover_max", args.hover_max),
        ("minion_hover_min", args.minion_hover_min),
        ("minion_hover_max", args.minion_hover_max),
    )
    body.update({k: v for k, v in mapping if v is not None})
    if len(body) == 1:
        raise ApiFailure("没有要改的字段（用 --enabled / --post-delay-min 等）")
    return _ok_message(ctx, ctx.post("/api/human_like", body))


def cmd_liveness_show(args, ctx: Ctx) -> int:
    return _print_section(
        ctx,
        "liveness",
        "炉石存活检测",
        (
            ("enabled", "总开关"),
            ("status", "当前状态"),
            ("process", "进程"),
            ("log_age", "Power.log 停滞(秒)"),
            ("log_stale_warn_seconds", "告警阈值(秒)"),
            ("log_stale_stop_seconds", "自动停止阈值(秒)"),
        ),
    )


def cmd_liveness_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    mapping = (
        ("enabled", args.enabled),
        ("log_stale_warn_seconds", args.warn),
        ("log_stale_stop_seconds", args.stop),
    )
    body.update({k: v for k, v in mapping if v is not None})
    if len(body) == 1:
        raise ApiFailure("没有要改的字段（用 --enabled / --warn / --stop）")
    return _ok_message(ctx, ctx.post("/api/liveness", body))


def cmd_concede_show(args, ctx: Ctx) -> int:
    return _print_section(
        ctx,
        "concede",
        "自动投降",
        (
            ("enabled", "总开关"),
            ("threshold", "胜率阈值(%)"),
            ("rounds", "连续回合数"),
        ),
    )


def cmd_concede_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    mapping = (
        ("enabled", args.enabled),
        ("threshold", args.threshold),
        ("rounds", args.rounds),
    )
    body.update({k: v for k, v in mapping if v is not None})
    if len(body) == 1:
        raise ApiFailure("没有要改的字段（用 --enabled / --threshold / --rounds）")
    return _ok_message(ctx, ctx.post("/api/concede", body))


def cmd_concede_now(args, ctx: Ctx) -> int:
    """手动投降：立刻点「齿轮 → 认输」，不需要先停自动化。"""
    return _ok_message(ctx, ctx.post("/api/concede_now", {}))


# -------------------------------------------------------- 卡组识别投降（只判对方）
def _surrender_config(ctx: Ctx) -> dict:
    cfg = (ctx.get("/api/status") or {}).get("auto_surrender")
    return cfg if isinstance(cfg, dict) else {}


def _surrender_decks_lines(cfg: dict):
    """把每套卡组翻译成一句中文；返回 (行, 错误行)。"""
    decks = cfg.get("decks") or []
    if not decks:
        return [], []
    try:
        import card_catalog
        import surrender_rules

        compiled, errors, _corrected = surrender_rules.compile_config(cfg)
    except Exception as exc:  # 卡牌库缺失 / 段落格式坏了
        return [], [f"条件解析失败（{exc}），可用 --json 看原文"]
    lines = []
    for deck in compiled:
        hero = (
            card_catalog.class_label(deck.hero_class) if deck.hero_class else "任意职业"
        )
        lines.append(
            f"  · {deck.name or deck.id}（{hero}）："
            f"{surrender_rules.describe_expr(deck.expr)}"
        )
    bad = [
        f"  ⚠️ {err.get('path') or 'config'}：{err.get('message')}"
        for err in (errors or [])
    ]
    return lines, bad


def _surrender_tree_lines(node, indent: int = 0) -> list:
    """把 /api/status 的 tree 渲染成缩进行（保留「且 / 或 / 非」结构）。"""
    if not node:
        return []
    pad = "      " + "    " * indent
    ok = bool(node.get("ok"))
    children = node.get("children") or []
    if not children:
        return [f"{pad}{'✓' if ok else '·'} {node.get('text') or ''}"]
    label = {"all": "且", "any": "或", "not": "非"}.get(
        node.get("op"), node.get("op") or ""
    )
    lines = [f"{pad}{'✓' if ok else '·'} {label}"]
    for child in children:
        lines.extend(_surrender_tree_lines(child, indent + 1))
    return lines


def cmd_surrender(args, ctx: Ctx) -> int:
    """卡组识别投降：识别到「对方是某套有特征牌的卡组」就立刻认输。

    用法：
      hsctl surrender                 # 看配置（默认）
      hsctl surrender on | off        # 总开关（改完立即生效）
      hsctl surrender check           # 看这一局的实时判定（对方职业 / 每条件是否满足）
      hsctl surrender set --file rules.json    # 整段替换（confirm 由 --yes 给）
      hsctl surrender clear           # 清空所有卡组

    判定**只看对方**：对方打出/出现过/开局生效的卡才算数，自己手里的不算。
    """
    sub = getattr(args, "action", None) or "show"

    if sub in ("on", "off"):
        enabled = sub == "on"
        payload = ctx.post("/api/surrender_rules", {"enabled": enabled})
        if not enabled:
            return _ok_message(ctx, payload)
        cfg = payload.get("config") or {}
        if ctx.json:
            print(json.dumps(payload, ensure_ascii=False))
            return EXIT_OK
        print(f"✓ {payload.get('message') or '已开启卡组识别投降'}")
        if not (cfg.get("decks") or []):
            print(
                "  ℹ️ 还没有配置卡组：用 `hsctl surrender set --file <json>` "
                "或在网页「卡组识别投降」里加"
            )
        return EXIT_OK

    if sub == "clear":
        payload = ctx.post(
            "/api/surrender_rules", {"config": {"enabled": False, "decks": []}}
        )
        return _ok_message(ctx, payload)

    if sub == "set":
        raw = _load_surrender_payload(args)
        body = {"config": raw}
        if getattr(args, "yes", False):
            body["confirm"] = True
        payload = ctx.post("/api/surrender_rules", body)
        if not payload.get("ok") and payload.get("needs_confirm"):
            msg = (
                payload.get("error")
                or payload.get("message")
                or "这套规则在当前对局里已经能命中了"
            )
            if ctx.json:
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(f"⚠️ {msg}")
                hit = payload.get("hit") or {}
                if hit.get("name"):
                    print(f"  会立刻投降：「{hit['name']}」")
                print("  确认要这么存就加 --yes 重跑一次。")
            return EXIT_API
        if not payload.get("ok"):
            # 校验失败：把「哪一条、错在哪」和候选卡名都打出来
            if ctx.json:
                print(json.dumps(payload, ensure_ascii=False))
                return EXIT_API
            print(
                f"✗ {payload.get('error') or payload.get('message') or '规则不合法，未保存'}"
            )
            for err in payload.get("errors") or []:
                print(f"  · {err.get('path') or 'config'}：{err.get('message')}")
                for name in err.get("suggestions") or []:
                    print(f"      你可能想找：{name}")
            return EXIT_API
        if ctx.json:
            print(json.dumps(payload, ensure_ascii=False))
            return EXIT_OK
        print(f"✓ {payload.get('message') or '已保存'}")
        for item in payload.get("corrected") or []:
            print(f"  ℹ️ 卡名「{item.get('from')}」已自动纠正为「{item.get('to')}」")
        for warn in payload.get("warnings") or []:
            print(f"  ⚠️ {warn}")
        return EXIT_OK

    if sub == "check":
        return _print_surrender_check(ctx, _surrender_config(ctx))

    # show（默认）
    cfg = _surrender_config(ctx)
    if ctx.json:
        print(json.dumps(cfg, ensure_ascii=False))
        return EXIT_OK
    decks = cfg.get("decks") or []
    print(
        f"卡组识别投降：{'开' if cfg.get('enabled') else '关'} · 共 {len(decks)} 套卡组"
    )
    lines, bad = _surrender_decks_lines(cfg)
    for line in lines + bad:
        print(line)
    if not decks:
        print(
            "  （还没有卡组：在网页「卡组识别投降」里加，或 "
            "hsctl surrender set --file rules.json）"
        )
    return EXIT_OK


def _load_surrender_payload(args) -> dict:
    """从 --file / --json 读整段配置（也支持 stdin 的 `-`）。"""
    path = getattr(args, "file", None)
    text = getattr(args, "rules", None)
    if path:
        try:
            text = (
                Path(path).read_text(encoding="utf8")
                if path != "-"
                else sys.stdin.read()
            )
        except OSError as exc:
            raise ApiFailure(f"读不到配置文件 {path}：{exc}")
    if not text:
        raise ApiFailure("要给规则：--file rules.json（或 --rules '{...}'）")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ApiFailure(f"不是合法 JSON：{exc}")
    if not isinstance(data, dict):
        raise ApiFailure("规则文件最外层要是一个对象（{enabled, decks, ...}）")
    # 允许直接给一个卡组数组或单套卡组，省得每次手写外壳
    if isinstance(data.get("decks"), list):
        return data
    if "when" in data:
        return {"enabled": True, "decks": [data]}
    raise ApiFailure("规则文件里没有 decks 段（也不像单套卡组：缺 when）")


def _print_surrender_check(ctx: Ctx, cfg: dict) -> int:
    """实时判定：对方读到什么、每套卡组满足到第几项。"""
    state = (ctx.get("/api/status") or {}).get("surrenders") or {}
    if ctx.json:
        print(
            json.dumps(
                {"ok": True, "enabled": bool(cfg.get("enabled")), "state": state},
                ensure_ascii=False,
            )
        )
        return EXIT_OK
    if not cfg.get("enabled"):
        print("卡组识别投降：关（用 hsctl surrender on 打开）")
    if state.get("error"):
        print(f"⚠️ 读取失败：{state['error']}")
        return EXIT_API
    if not state.get("decks"):
        print("（还没有可判定的卡组，或自动化未启动/尚未进入对局）")
        if state.get("compile_errors"):
            for err in state["compile_errors"]:
                print(f"  ⚠️ {err.get('path') or 'config'}：{err.get('message')}")
        return EXIT_OK
    import card_catalog

    cls = state.get("opponent_class") or ""
    print(
        f"对方职业：{card_catalog.class_label(cls) if cls else '未读到'}"
        f"    回合：{state.get('round') or '-'}"
        f"    {'我方回合' if state.get('my_turn') else '对方回合'}"
    )
    facts = state.get("facts") or {}
    if facts.get("played") or facts.get("start_of_game"):
        print(
            f"已记录：对方打出 {len(state.get('opponent_played') or [])} 张"
            f" · 出现过 {len(state.get('opponent_revealed') or [])} 张"
            f" · 开局生效 {len(state.get('start_of_game') or [])} 张"
        )
    for deck in state["decks"]:
        hero = (
            card_catalog.class_label(deck.get("class"))
            if deck.get("class")
            else "任意职业"
        )
        head = "✓ 命中，会投降" if deck.get("matched") else "× 未满足"
        print(
            f"  {head} 「{deck.get('name') or deck.get('id')}」"
            f"（{hero}）{deck.get('done')}/{deck.get('total')}"
        )
        if deck.get("tree"):
            for line in _surrender_tree_lines(deck["tree"]):
                print(line)
            continue
        for item in deck.get("checks") or []:
            print(f"      {'✓' if item.get('ok') else '·'} {item.get('text')}")
    if state.get("triggered"):
        print("本局已触发过投降（不会重复）")
    return EXIT_OK


def cmd_card(args, ctx: Ctx) -> int:
    """查卡：中文名补全（默认）/ 单卡详情（--show）。纯本地 cards.json，不联网。

    用法::

        hsctl card 神秘            # 补全候选（打错字也会给几个最像的）
        hsctl card 神秘访客 --show  # 单卡详情（含自动纠错与多版本 id）
    """
    import card_catalog

    catalog = card_catalog.default_catalog()
    if catalog is None:
        print(f"卡牌库不可用：{card_catalog.catalog_error()}")
        return EXIT_API
    sub = "show" if getattr(args, "show", False) else "search"
    text = " ".join(args.words or []).strip()
    if not text:
        raise ApiFailure("要查什么卡？（例：hsctl card search 神秘访客）")

    if sub == "show":
        result = catalog.lookup(text, aliases=None)
        if ctx.json:
            print(
                json.dumps(
                    {
                        "ok": result.ok,
                        "query": result.query,
                        "cards": [hit.to_dict() for hit in result.hits],
                        "error": result.error,
                        "suggestions": list(result.suggestions),
                        "corrected_from": result.corrected_from,
                    },
                    ensure_ascii=False,
                )
            )
            return EXIT_OK if result.ok else EXIT_API
        if not result.ok:
            print(f"✗ {result.error}")
            for name in result.suggestions:
                print(f"  你可能想找：{name}")
            return EXIT_API
        hit = result.hits[0]
        if result.corrected_from:
            print(f"（「{result.corrected_from}」按「{hit.name}」处理）")
        print(hit.describe())
        if len(hit.ids) > 1:
            print(f"  同名版本：{'、'.join(hit.ids)}")
        return EXIT_OK

    hits = catalog.search(text, limit=getattr(args, "limit", 8) or 8)
    if ctx.json:
        print(
            json.dumps(
                {"ok": True, "query": text, "cards": [hit.to_dict() for hit in hits]},
                ensure_ascii=False,
            )
        )
        return EXIT_OK
    if not hits:
        suggestion = catalog.fuzzy_suggest(text, limit=3)
        print(f"没有匹配「{text}」的卡")
        for hit in suggestion:
            print(f"  你可能想找：{hit.describe()}")
        return EXIT_API
    for hit in hits:
        print(hit.describe())
    return EXIT_OK


def cmd_rank_stop_show(args, ctx: Ctx) -> int:
    return _print_section(
        ctx,
        "rank_stop",
        "段位目标",
        (
            ("enabled", "总开关"),
            ("mode", "方式（legend/legend_number）"),
            ("legend_number", "目标名次"),
            ("action", "到该名次后（stop=停止 / keep=控分）"),
            ("keep_count", "本次运行已投降局数（控分）"),
            ("last_number", "最近读到的名次"),
            ("triggered", "已触发停止"),
        ),
    )


def cmd_rank_stop_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    mapping = (
        ("enabled", args.enabled),
        ("mode", args.mode),
        ("legend_number", args.legend_number),
        ("action", args.action),
    )
    body.update({k: v for k, v in mapping if v is not None})
    if len(body) == 1:
        raise ApiFailure(
            "没有要改的字段（用 --enabled / --mode / --legend-number / --action）"
        )
    return _ok_message(ctx, ctx.post("/api/rank_stop", body))


def cmd_schedule_show(args, ctx: Ctx) -> int:
    s = ctx.get("/api/status")
    payload = {
        "schedule_start": s.get("schedule_start"),
        "schedule_end": s.get("schedule_end"),
        "phase": s.get("phase"),
    }
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    if not payload["schedule_start"]:
        print("定时任务：（无）")
    else:
        print(
            f"定时任务：{payload['schedule_start']} 开始，"
            f"{payload['schedule_end'] or '不设结束（运行至手动停止）'}"
        )
    return EXIT_OK


def _parse_when(text: str) -> datetime:
    """解析时间：HH:MM(:SS) 按「今天，已过则明天」；其余交给 fromisoformat。"""
    text = (text or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if m:
        now = datetime.now()
        cand = now.replace(
            hour=int(m.group(1)),
            minute=int(m.group(2)),
            second=int(m.group(3) or 0),
            microsecond=0,
        )
        if cand <= now:
            cand += timedelta(days=1)
        return cand
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ApiFailure(
            f"看不懂的时间：{text!r}（用 HH:MM、HH:MM:SS 或 2026-05-30T22:30）"
        )


def cmd_schedule_at(args, ctx: Ctx) -> int:
    if args.in_minutes is not None:
        start = datetime.now() + timedelta(minutes=float(args.in_minutes))
    elif args.start:
        start = _parse_when(args.start)
    else:
        raise ApiFailure(
            "要给一个开始时间：hsctl schedule at 22:30 或 hsctl schedule at --in 30"
        )
    end = None
    if args.duration is not None:
        end = start + timedelta(minutes=float(args.duration))
    elif args.end:
        end = _parse_when(args.end)
        if end <= start:
            end += timedelta(days=1)
    body = {"start": start.isoformat(timespec="minutes")}
    if end:
        body["end"] = end.isoformat(timespec="minutes")
    payload = ctx.post("/api/schedule", body)
    if ctx.json:
        print(json.dumps({**payload, **body}, ensure_ascii=False))
        return EXIT_OK
    print(
        f"✓ 定时任务已设置：{start:%Y-%m-%d %H:%M} 开始"
        f"{f'，{end:%Y-%m-%d %H:%M} 结束' if end else '，不设结束'}"
    )
    print("  需要保持脚本（web_ui.py）一直运行。")
    return EXIT_OK


def cmd_schedule_cancel(args, ctx: Ctx) -> int:
    return _ok_message(ctx, ctx.post("/api/schedule/cancel", {}))


# ---- 进程托管
def _detached_flags():
    if os.name == "nt":
        return subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


def _pid_image_name(pid: int):
    """拿 pid 的映像名（Windows 用 tasklist）；查不到返回 None。"""
    if os.name != "nt":
        return None
    try:
        # 中文 Windows 上 tasklist 输出是 GBK/ANSI 编码；text=True 默认按
        # locale 之外的解释会 UnicodeDecodeError 崩掉读线程，这里显式按
        # bytes 拿回来再宽容解码。
        raw = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=15,
        ).stdout
        out = raw.decode("utf-8", errors="replace")
        if not out.strip() or "\ufffd" in out:
            out = raw.decode("gbk", errors="replace")
    except Exception:
        return None
    return out.strip() or None


def _pid_alive(pid: int) -> bool:
    """进程还在不在（只查，不发信号）。

    **不能用 `os.kill(pid, 0)`**：Windows 上 `os.kill` 对任何信号都是
    TerminateProcess(信号值)，"探测"会直接把进程杀掉。所以还是问 tasklist。
    """
    line = _pid_image_name(pid)
    if not line:
        return False
    # 查到的时候是 CSV 行（以引号开头）；查不到时 tasklist 打的是一句本地化提示。
    return line.lstrip().startswith('"')


def _serve_status(ctx: Ctx, quiet: bool = False) -> int:
    info = read_port_file()
    alive = False
    try:
        alive = ctx._probe(ctx.base)
    except TransportError:
        alive = False
    payload = {"running": alive, "port_file": str(PORT_FILE), "port_info": info}
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    if not quiet:
        if alive:
            print(f"控制台在运行：{ctx.base}")
            if info:
                print(f"  pid {info.get('pid')}  启动于 {info.get('started_at')}")
        else:
            print("控制台没在运行。")
            print("  启动：hsctl serve start    （或 python web_ui.py）")
    return EXIT_OK


def cmd_serve(args, ctx: Ctx) -> int:
    if args.action == "status":
        return _serve_status(ctx)
    if args.action == "start":
        return _serve_start(args, ctx)
    if args.action == "stop":
        return _serve_stop(args, ctx)
    if args.action == "restart":
        _serve_stop(args, ctx)
        time.sleep(1.0)
        return _serve_start(args, ctx)
    if args.action == "install-task":
        return cmd_serve_install_task(args, ctx)
    if args.action == "uninstall-task":
        return cmd_serve_uninstall_task(args, ctx)
    if args.action == "task-status":
        return cmd_serve_task_status(args, ctx)
    raise ApiFailure(f"未知动作：{args.action}")


# ---------------------------------------------------------------- 提权启动
# 非提权进程**无法**静默创建提权子进程 —— 这是 Windows 的安全边界，任何 agent
# 都越不过（WorkBuddy 的「完全访问权限」只是它自己的确认层，不等于管理员令牌）。
# 只有两条路：
#   1) --elevate   ：ShellExecute("runas") → 弹 UAC 同意框 → **需要有人在电脑前点**；
#   2) --via-task  ：注册一个「最高权限」计划任务；之后任何非提权进程都能 /run 它，
#                    由任务计划程序以 SYSTEM 身份按最高权限拉起，**不再弹 UAC**。
#    手机远程 / 无人值守只有第 2 条路走得通。
TASK_NAME = "HSLegendArriver_serve"


def is_user_admin() -> bool:
    """当前进程是否已提权（管理员）。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _python_for_task() -> str:
    """计划任务用哪个解释器：优先**本机固定**的那个（log/env.json）。

    为什么要这样：计划任务的启动器是把"生成它时的那个 python"硬编码进去的。
    如果那次生成用的是别的工具自带的 Python（版本不符、没依赖），之后每次
    `serve start --via-task` 都会用错解释器，自检会"全部依赖 fail"。
    所以只要本机固定过解释器，就以它为准（见 `hsctl env --pin`）。

    另：pythonw.exe 下 sys.stdout 可能是 None，而 web_ui 会用 _TeeStream 接管
    stdout，拿 None 去 write 会炸；所以这里刻意避开 pythonw。
    """
    pinned = _normalize_python(_read_env_file().get("interpreter"))
    if pinned and Path(pinned).exists():
        return pinned
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "pythonw.exe":
        plain = exe.with_name("python.exe")
        if plain.exists():
            return str(plain)
    return str(exe)


def _task_action() -> str:
    """计划任务的动作：指向一个生成好的启动器 .cmd。

    为什么要绕一层启动器：`schtasks /tr` 的参数本身要用引号包住，如果里面再嵌
    `cmd /c cd /d "路径" && "python" ...` 这种带引号的命令，用户在 cmd 里粘贴时
    会被内层引号提前截断（实测跑不通）。启动器把嵌套引号收进文件里，`/tr` 就只剩
    一个普通路径。

    启动器内容刻意让任务去跑 `hsctl serve start` 而不是直接跑 web_ui.py：这条命令
    **自带幂等**（先探活，已在运行就什么都不做），所以任务被误触发（例如 ONCE 触发
    器的时间已经过去）也不会平白多起一个控制台抢同一份配置。
    """
    return str(TASK_LAUNCHER)


def _task_launcher_text() -> str:
    """启动器脚本内容。注释只用 ASCII，避免 .cmd 的代码页问题。"""
    return (
        "@echo off\r\n"
        "rem Generated by: hsctl serve install-task\r\n"
        "rem The scheduled task points at this file so that schtasks /tr needs\r\n"
        "rem no nested quotes. Rerun install-task to regenerate.\r\n"
        "chcp 65001 >nul\r\n"
        f'cd /d "{ROOT}"\r\n'
        f'"{_python_for_task()}" "{ROOT / "hsctl.py"}" serve start --wait 120\r\n'
    )


def _ensure_task_launcher() -> Path:
    TASK_LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    # newline="" 关掉换行翻译：否则显式的 \r\n 会被再翻译成 \r\r\n，cmd 里每行多一个空行。
    TASK_LAUNCHER.write_text(_task_launcher_text(), encoding="utf8", newline="")
    return TASK_LAUNCHER


def _task_create_argv() -> list:
    _ensure_task_launcher()
    return [
        "schtasks",
        "/create",
        "/tn",
        TASK_NAME,
        "/tr",
        _task_action(),
        "/sc",
        "ONCE",
        "/st",
        "00:00",  # 只手动触发，不靠时间计划
        "/rl",
        "HIGHEST",  # ← 关键：以最高权限运行
        "/f",
    ]


def _task_create_hint() -> str:
    """给用户复制粘贴用的管理员命令（单层引号，可直接粘贴执行）。"""
    _ensure_task_launcher()
    return (
        f'schtasks /create /tn "{TASK_NAME}" /tr "{_task_action()}" '
        f"/sc ONCE /st 00:00 /rl HIGHEST /f"
    )


def _run_quiet(argv) -> tuple:
    """跑一条外部命令，返回 (returncode, 输出)。"""
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=60)
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    raw = (proc.stdout or b"") + (proc.stderr or b"")
    for enc in ("mbcs", "utf-8"):
        try:
            return proc.returncode, raw.decode(enc)
        except Exception:
            continue
    return proc.returncode, raw.decode("utf-8", "replace")


def _task_xml():
    """取计划任务的 XML 定义（用于确认真的勾了「最高权限」）。

    `schtasks /query /xml` 在中文 Windows 上吐 UTF-16LE，所以先看有没有 NUL 字节。
    """
    try:
        proc = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/xml"],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout or b""
    if b"\x00" in raw[:64]:
        return raw.decode("utf-16", "replace")
    return raw.decode("mbcs" if os.name == "nt" else "utf-8", "replace")


def _task_state() -> dict:
    code, out = _run_quiet(["schtasks", "/query", "/tn", TASK_NAME])
    exists = code == 0
    xml = _task_xml() if exists else None
    return {
        "name": TASK_NAME,
        "exists": exists,
        "highest": ("HighestAvailable" in xml) if xml else None,
        "action": _task_action(),
        "is_admin_now": is_user_admin(),
        "query": (out or "").strip()[:400],
    }


def _launch_runas(exe: str, params: str) -> tuple:
    """ShellExecute('runas') 请求提权，返回 (是否发起成功, 返回码)。

    返回码 5 = ERROR_ACCESS_DENIED，通常就是用户在 UAC 框上点了「否」。
    """
    import ctypes

    fn = ctypes.windll.shell32.ShellExecuteW
    fn.restype = ctypes.c_void_p
    fn.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    res = fn(None, "runas", exe, params, str(ROOT), 1)  # SW_SHOWNORMAL
    code = 0 if res is None else int(res)
    return code > 32, code


def _base_port() -> int:
    try:
        return int(os.environ.get("HS_PORT") or DEFAULT_BASE_PORT)
    except (TypeError, ValueError):
        return DEFAULT_BASE_PORT


def _find_console(ctx: Ctx):
    """找一个活着的控制台：端口文件优先，然后从期望端口起顺延扫。"""
    seen = set()
    candidates = []
    info = read_port_file()
    if info and info.get("port"):
        try:
            candidates.append((info.get("host") or DEFAULT_HOST, int(info["port"])))
        except (TypeError, ValueError):
            pass
    start = ctx.port or _base_port()
    for port in range(start, start + PORT_SCAN_COUNT):
        candidates.append((ctx.host, port))
    for host, port in candidates:
        if (host, port) in seen:
            continue
        seen.add((host, port))
        base = f"http://{host}:{port}"
        try:
            with urllib.request.urlopen(
                base + "/api/status", timeout=PROBE_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    return base
        except Exception:
            continue
    return None


def _wait_for_console(ctx: Ctx, timeout: float, proc=None):
    """等控制台就绪；进程提前退出就立刻放弃。返回 base url 或 None。"""
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        base = _find_console(ctx)
        if base:
            return base
        if proc is not None and proc.poll() is not None:
            return None
        time.sleep(0.5)
    return None


def _tail(path, count: int = 15) -> str:
    try:
        lines = path.read_text(encoding="utf8", errors="replace").splitlines()[-count:]
        return "\n".join(lines)
    except Exception:
        return "（读不到日志）"


def _report_ready(base: str) -> None:
    status = None
    try:
        with urllib.request.urlopen(base + "/api/status", timeout=3) as resp:
            status = json.loads(resp.read().decode("utf8"))
    except Exception:
        pass
    print(f"✓ 控制台已就绪：{base}")
    if isinstance(status, dict) and not status.get("is_admin"):
        print("  ⚠️ 当前不是管理员权限：自动化点击会被系统拦掉（点了没反应）。")
        print("     一次搞定（管理员 PowerShell 跑一次）：hsctl serve install-task")
        print("     之后启动改用：hsctl serve start --via-task")
    print("  网页控制台仍然可用；停止：hsctl serve stop")


def _serve_start(args, ctx: Ctx) -> int:
    try:
        if ctx._probe(ctx.base):
            print(f"控制台已经在运行：{ctx.base}（未重复启动）")
            return EXIT_OK
    except TransportError:
        pass
    if not (ROOT / "web_ui.py").exists():
        raise ApiFailure(f"找不到 {ROOT / 'web_ui.py'}")
    if getattr(args, "via_task", False):
        return _serve_start_via_task(args, ctx)
    if getattr(args, "elevate", False):
        return _serve_start_elevated(args, ctx)
    return _serve_start_direct(args, ctx)


def _serve_start_direct(args, ctx: Ctx) -> int:
    """普通（非提权）后台上拉。"""
    cmd = [sys.executable, str(ROOT / "web_ui.py")]
    if not args.browser:
        cmd.append("--no-browser")
    env = dict(os.environ)
    if ctx.port:
        env["HS_PORT"] = str(int(ctx.port))
    SERVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(SERVE_LOG, "a", encoding="utf8")
    log_handle.write(
        f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} hsctl serve start =====\n"
    )
    log_handle.flush()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=_detached_flags(),
        )
    except Exception as exc:
        raise ApiFailure(f"启动失败：{exc}")
    print(f"已在后台拉起控制台（pid {proc.pid}），等待它监听端口……")
    base = _wait_for_console(ctx, args.wait, proc=proc)
    if not base:
        if proc.poll() is not None:
            raise ApiFailure(
                f"控制台进程已退出（退出码 {proc.poll()}）。"
                f"\n日志尾部（{SERVE_LOG}）：\n{_tail(SERVE_LOG)}"
            )
        raise ApiFailure(
            "控制台在限定时间内没有起来。"
            f"\n日志尾部（{SERVE_LOG}）：\n{_tail(SERVE_LOG)}"
        )
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid), encoding="utf8")
    _report_ready(base)
    return EXIT_OK


def _serve_start_elevated(args, ctx: Ctx) -> int:
    """UAC 提权启动：会弹同意框，需要有人在电脑前点「是」。"""
    if os.name != "nt":
        raise ApiFailure("--elevate 只在 Windows 上可用")
    if ctx.port:
        # ShellExecute 没有 env 参数，只能改本进程环境让子进程继承。
        os.environ["HS_PORT"] = str(int(ctx.port))
    params = subprocess.list2cmdline(
        [str(ROOT / "web_ui.py")] + ([] if args.browser else ["--no-browser"])
    )
    print("正在请求提权：屏幕上会弹出 UAC 同意框，请点「是」。")
    print(
        "  （这一步必须有人的电脑前操作；无人值守请改用："
        "hsctl serve install-task，然后 hsctl serve start --via-task）"
    )
    ok, code = _launch_runas(_python_for_task(), params)
    if not ok:
        if code == 5:
            raise ApiFailure(
                "提权被拒绝（UAC 框上点了「否」或超时取消），控制台没有启动。"
            )
        raise ApiFailure(f"提权启动失败（ShellExecute 返回 {code}）。")
    base = _wait_for_console(ctx, args.wait)
    if not base:
        raise ApiFailure(
            "已发起提权，但控制台在限定时间内没有就绪。请确认 UAC 框已点「是」。"
        )
    _report_ready(base)
    return EXIT_OK


def _run_task_now() -> dict:
    """触发「最高权限」计划任务：schtasks 优先，被策略拦掉就降级 PowerShell。

    为什么需要降级：有的机器把 `schtasks.exe` 列进了程序黑名单（本机实测），
    这时 schtasks 的任何子命令都跑不了，但 PowerShell 的 `Start-ScheduledTask`
    走的是任务计划程序 COM 接口，不受影响。

    返回 ``{"ok","mechanism","detail"}``；mechanism ∈ {schtasks, powershell}。
    """
    code, out = _run_quiet(["schtasks", "/run", "/tn", TASK_NAME])
    if code == 0:
        return {"ok": True, "mechanism": "schtasks", "detail": ""}
    first = (out or "").strip()[:200]
    ps = "Start-ScheduledTask -TaskName '" + TASK_NAME + "'"
    code2, out2 = _run_quiet(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps]
    )
    if code2 == 0:
        return {
            "ok": True,
            "mechanism": "powershell",
            "detail": f"schtasks 不可用（{first}），已改用 PowerShell Start-ScheduledTask",
        }
    return {
        "ok": False,
        "mechanism": "none",
        "detail": f"schtasks: {first}；PowerShell: {(out2 or '').strip()[:200]}",
    }


def _serve_start_via_task(args, ctx: Ctx) -> int:
    """通过「最高权限」计划任务启动：不弹 UAC，可无人值守。"""
    state = _task_state()
    if not state["exists"]:
        raise ApiFailure(
            "计划任务还没注册。请用**管理员** PowerShell 跑一次：\n  "
            + _task_create_hint()
            + "\n（或者：hsctl serve install-task）"
        )
    if state["highest"] is False:
        print("⚠️ 这个任务没有勾选「最高权限」，起来后仍然不是管理员。")
        print("   重建：hsctl serve uninstall-task && hsctl serve install-task")
    triggered = _run_task_now()
    if not triggered["ok"]:
        raise ApiFailure(f"触发计划任务失败：{triggered['detail']}")
    if triggered["mechanism"] == "powershell":
        print(f"schtasks 在本机不可用，已改用 PowerShell 触发计划任务。")
    print(f"已触发计划任务 {TASK_NAME}（由任务计划程序以最高权限启动，不弹 UAC）。")
    base = _wait_for_console(ctx, args.wait)
    if not base:
        raise ApiFailure(
            "已触发计划任务，但控制台在限定时间内没有就绪。"
            "\n  查任务状态：hsctl serve task-status"
            f"\n  日志尾部（{SERVE_LOG}）：\n{_tail(SERVE_LOG)}"
        )
    _report_ready(base)
    return EXIT_OK


def cmd_serve_install_task(args, ctx: Ctx) -> int:
    state = _task_state()
    if state["exists"] and not args.force:
        print(
            f"计划任务已存在：{TASK_NAME}"
            f"（最高权限：{'是' if state['highest'] else '否/未知'}）"
        )
        print("  要覆盖重建请加 --force。")
        return EXIT_OK
    if not is_user_admin():
        # 不替用户弹 UAC：把命令给出去，让他在管理员窗口里执行一次。
        print("✗ 注册「最高权限」计划任务需要管理员权限，当前进程不是管理员。")
        print("  请在**管理员** PowerShell / cmd 里执行下面这一条（只需一次）：")
        print()
        print("  " + _task_create_hint())
        print()
        print("  跑完回到普通窗口执行：hsctl serve start --via-task")
        return EXIT_API
    code, out = _run_quiet(_task_create_argv())
    if code != 0:
        raise ApiFailure(f"注册计划任务失败：{(out or '').strip()[:400]}")
    state = _task_state()
    print(
        f"✓ 计划任务已注册：{TASK_NAME}"
        f"（最高权限：{'是' if state['highest'] else '未知'}）"
    )
    print(
        "  之后任何非提权进程（包括 WorkBuddy 这类 agent）都能触发它，且不会再弹 UAC："
    )
    print("    hsctl serve start --via-task")
    print("  卸载：hsctl serve uninstall-task")
    return EXIT_OK


def cmd_serve_uninstall_task(args, ctx: Ctx) -> int:
    if not _task_state()["exists"]:
        print("计划任务不存在（无需卸载）。")
        return EXIT_OK
    code, out = _run_quiet(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    if code != 0:
        raise ApiFailure(f"删除计划任务失败：{(out or '').strip()[:300]}")
    print(f"✓ 计划任务已删除：{TASK_NAME}")
    return EXIT_OK


def cmd_serve_task_status(args, ctx: Ctx) -> int:
    state = _task_state()
    if ctx.json:
        print(json.dumps(state, ensure_ascii=False))
        return EXIT_OK
    if not state["exists"]:
        print(f"计划任务：不存在（{TASK_NAME}）")
        print("  注册（管理员跑一次）：" + _task_create_hint())
        return EXIT_OK
    print(f"计划任务：{TASK_NAME}")
    print(f"  以最高权限运行：{'是' if state['highest'] else '否 / 未知'}")
    print(f"  当前进程是管理员：{'是' if state['is_admin_now'] else '否'}")
    print(f"  动作：{state['action']}")
    return EXIT_OK


def _serve_self_stop(ctx: Ctx, timeout: float = 25.0) -> bool:
    """请求控制台**自己退出**（走 HTTP，不需要提权）。成功返回 True。

    为什么需要：控制台常常是提权起的，而非提权进程结束不了提权进程
    （taskkill 报 Access denied），于是"脚本停不掉"，只能人工用管理员 shell 再关。
    这里用端口文件里的 token 证明"是本机的调用方"，让控制台自己收尾退出。
    """
    info = read_port_file() or {}
    token = str(info.get("token") or "")
    if not token:
        # 老版本控制台（端口文件里没有 token）：这条通道用不了，退回 taskkill。
        return False
    try:
        ctx.post("/api/serve/stop", {"token": token})
    except (ApiFailure, TransportError):
        # 接口不存在（老控制台）/ 已经被停掉，都按"没成功"处理，由调用方兜底。
        pass
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        try:
            if not ctx._probe(ctx.base):
                return True
        except TransportError:
            return True
        time.sleep(0.4)
    return False


def _serve_stop(args, ctx: Ctx) -> int:
    info = read_port_file() or {}
    pid = info.get("pid")
    if pid is None:
        try:
            pid = int(PID_FILE.read_text(encoding="utf8").strip())
        except Exception:
            pid = None
    try:
        alive = ctx._probe(ctx.base)
    except TransportError:
        alive = False
    if not alive:
        # 端口文件残留（上次被硬杀，没走完 finally）。这里**绝不**去 kill 它记下的
        # pid：pid 可能已经被系统复用给别的进程了，而"没在监听"本身就是权威结论。
        removed = [p.name for p in (PORT_FILE, PID_FILE) if p.exists() and _unlink(p)]
        if removed:
            print(
                "控制台没在运行（已清理残留的端口文件：" + "、".join(removed) + "）。"
            )
        else:
            print("控制台没在运行（无需停止）。")
        return EXIT_OK
    # 先优雅停自动化，再结束进程：避免在出牌动作中途被硬杀。
    try:
        ctx.post("/api/stop", {"mode": "now"})
        print("已请求停止自动化，等待它收尾……")
        deadline = time.time() + 20
        while time.time() < deadline:
            s = ctx.get("/api/status")
            if not s.get("running"):
                break
            time.sleep(0.5)
    except (ApiFailure, TransportError) as exc:
        print(f"  （停自动化这步没成功，继续结束进程：{exc}）")

    # 首选：让控制台自己停 —— 不需要提权，提权起的控制台也能停掉。
    if not getattr(args, "hard", False):
        print("正在请求控制台自停（HTTP，不需要提权）……")
        if _serve_self_stop(ctx):
            for path in (PORT_FILE, PID_FILE):
                _unlink(path)
            print(f"✓ 控制台已停止（pid {pid}，走的是自停通道）。")
            return EXIT_OK
        print("  自停没成功（可能控制台不是本脚本/端口文件没有 token），退回结束进程。")

    if pid is None:
        print("端口文件里没有 pid，无法结束进程；请手动关掉控制台窗口。")
        return EXIT_API
    image = _pid_image_name(pid)
    if image is not None and "python" not in image.lower() and not args.force:
        raise ApiFailure(
            f"pid {pid} 看起来不是 Python 进程（{image.split(',')[0]}），"
            "拒绝结束它。确认无误可加 --force。"
        )
    ok, detail = _kill_pid(int(pid))
    if not ok and not _pid_alive(int(pid)):
        # pid 已经不在了（刚好在这中间自己退出/被系统回收）。这时报"权限不足"
        # 会把人引到 UAC 上去查一个根本不存在的问题 —— 当成已停止，继续收尾。
        ok, detail = True, "already gone"
    if not ok:
        # 最常见的原因：控制台是**提权**起的（serve start --via-task / --elevate），
        # 而当前 shell 没提权 —— Windows 不允许低权限进程结束高权限进程。
        raise ApiFailure(
            f"结束 pid {pid} 失败：{detail}\n"
            "  已试过 HTTP 自停（没成功），taskkill 也不行。\n"
            "  办法：① 用**管理员** PowerShell 跑 `hsctl serve stop --hard`；"
            "或 ② 先 `hsctl stop` 停掉自动化（这个走 HTTP，不需要提权），"
            "再手动关掉那个控制台窗口。"
        )
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if not ctx._probe(ctx.base):
                break
        except TransportError:
            break
        time.sleep(0.5)
    for path in (PORT_FILE, PID_FILE):
        _unlink(path)
    print(f"✓ 控制台已停止（pid {pid}）。")
    return EXIT_OK


def _kill_pid(pid: int) -> tuple:
    """结束进程：先 os.kill，不行再退到 taskkill。返回 (是否成功, 说明)。"""
    try:
        os.kill(pid, signal.SIGTERM)
        return True, "ok"
    except Exception as exc:
        first = f"{type(exc).__name__}: {exc}"
    if os.name == "nt":
        for extra in ([], ["/F"]):
            code, out = _run_quiet(["taskkill", "/PID", str(pid)] + extra)
            if code == 0:
                return True, "taskkill"
            last = (out or "").strip()[:200]
        return False, f"{first}；taskkill 也不行：{last}"
    return False, first


def _unlink(path) -> bool:
    """删文件，成功返回 True（失败静默：清理动作不该把命令搞挂）。"""
    try:
        path.unlink()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------- 参数表
def _add_bool(parser, name, help_text, dest=None):
    parser.add_argument(
        f"--{name}",
        dest=dest,
        action=argparse.BooleanOptionalAction,
        default=None,
        help=help_text,
    )


def _add_launch_flags(parser):
    """启动方式：普通（默认）／UAC 提权／计划任务；后两者互斥。

    自动化点击必须提权才生效，而两条提权路的代价不同：
    `--elevate` 会弹 UAC（需要人在电脑前点），`--via-task` 不弹（可无人值守）。
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--elevate",
        action="store_true",
        help="请求提权启动（弹 UAC 同意框，需有人在电脑前点）",
    )
    group.add_argument(
        "--via-task",
        dest="via_task",
        action="store_true",
        help="用已注册的「最高权限」计划任务启动（不弹 UAC，"
        "可手机远程/无人值守；需先 serve install-task）",
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # default=SUPPRESS：避免子解析器的默认值覆盖掉根解析器已解析的同名参数
    # （argparse 的已知行为），这样 --json 放在命令前后都有效。
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出机器可读 JSON（agent 用）",
    )
    common.add_argument(
        "--host", default=argparse.SUPPRESS, help=f"控制台地址（默认 {DEFAULT_HOST}）"
    )
    common.add_argument(
        "--port", type=int, default=argparse.SUPPRESS, help="控制台端口（默认自动发现）"
    )
    common.add_argument(
        "--timeout",
        type=float,
        default=argparse.SUPPRESS,
        help=f"HTTP 超时秒数（默认 {DEFAULT_TIMEOUT:g}）",
    )

    parser = argparse.ArgumentParser(
        prog="hsctl",
        parents=[common],
        description="炉石脚本命令行控制台（web_ui.py 的薄客户端）",
        epilog="示例：hsctl status | hsctl log -f | hsctl start | "
        "hsctl delays set pre_action_delay_seconds=5 | "
        "hsctl serve start | hsctl repl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"hsctl {version_label()}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    p = sub.add_parser("status", parents=[common], help="看状态（--watch 持续刷新）")
    p.add_argument(
        "--watch", type=float, default=0, help="每 N 秒刷新一行（Ctrl+C 退出）"
    )
    p.set_defaults(handler=cmd_status)

    p = sub.add_parser("log", parents=[common], help="看日志（-f 实时跟随）")
    p.add_argument("-f", "--follow", action="store_true", help="持续跟随新日志")
    p.add_argument(
        "-n", "--lines", type=int, default=0, help="先打印最近 N 行（0 = 缓冲区全部）"
    )
    p.add_argument(
        "--interval", type=float, default=1.0, help="跟随时的轮询间隔秒数（默认 1）"
    )
    p.set_defaults(handler=cmd_log)

    p = sub.add_parser("selfcheck", parents=[common], help="环境自检")
    p.add_argument("--refresh", action="store_true", help="强制重新检测")
    p.set_defaults(handler=cmd_selfcheck)

    p = sub.add_parser("start", parents=[common], help="开始对战")
    p.add_argument(
        "--keep-score", action="store_true", help="保留已有战绩（等同 resume）"
    )
    p.set_defaults(handler=cmd_start)

    p = sub.add_parser(
        "resume", parents=[common], help="恢复自动化（战绩继续累计，不清零）"
    )
    p.set_defaults(handler=cmd_resume)

    p = sub.add_parser(
        "prepare",
        parents=[common],
        help="开始运行（准备）：开浮窗 + 切炉石前台，不开打",
    )
    p.set_defaults(handler=cmd_prepare)

    p = sub.add_parser("stop", parents=[common], help="停止自动化")
    p.add_argument(
        "--after-game", action="store_true", help="打完当前这一局再停（默认立即停）"
    )
    p.set_defaults(handler=cmd_stop)

    p = sub.add_parser(
        "cancel",
        parents=[common],
        help="急停：叫停正在点的「进对战/等界面」并停止自动化",
    )
    p.set_defaults(handler=cmd_cancel)

    p = sub.add_parser("calibrate", parents=[common], help="打开屏幕上的校准窗口")
    p.add_argument(
        "--target",
        default=None,
        metavar="目标",
        help="一打开就停在这个目标上：main_menu_battle / deck_grid / "
        "recommendation / mulligan_confirm / win_rate / "
        "post_game_start / rank",
    )
    p.set_defaults(handler=cmd_calibrate)

    p = sub.add_parser("overlay", parents=[common], help="开/关日志浮窗")
    p.add_argument("action", choices=("on", "off", "toggle"))
    p.set_defaults(handler=cmd_overlay)

    p = sub.add_parser(
        "regions", parents=[common], help="截屏并画出所有截图区域框（不点击）"
    )
    p.set_defaults(handler=cmd_regions)

    p = sub.add_parser(
        "screenshot", parents=[common], help="按需抓一帧画面（只截图，不点击）"
    )
    p.add_argument(
        "--kind",
        choices=SCREENSHOT_KINDS,
        default="client",
        help="client=炉石客户区(默认) / full=整屏 / "
        "roi=推荐面板(OCR 看到的) / boxes=整屏+区域框",
    )
    p.add_argument(
        "--max-width",
        type=int,
        default=0,
        metavar="像素",
        help="超过这个宽度就等比缩小（0=不缩；给 agent 看建议 1280）",
    )
    p.add_argument(
        "--out",
        default=None,
        metavar="文件名",
        help="存成这个文件（相对 tmp_screens\\；不给就按时间戳命名）",
    )
    p.add_argument("--open", action="store_true", help="抓完用系统看图打开")
    p.add_argument(
        "--watch", action="store_true", help="持续抓图，每次覆盖同一张（实时看画面）"
    )
    p.add_argument(
        "--interval", type=float, default=2.0, help="--watch 的间隔秒数（默认 2）"
    )
    p.add_argument(
        "--points",
        nargs="?",
        const="mode",
        default=None,
        choices=("mode",),
        help="在图上标出「模式切换」四个点击点（红十字=实际点击位置）；"
        "只画图，不点击。核对 hsctl mode 坐标就用它",
    )
    p.set_defaults(handler=cmd_screenshot)

    p = sub.add_parser(
        "feedback",
        parents=[common],
        help="导出出错反馈包（重试/报错文本 + 截图）/ 查看状态",
    )
    p.add_argument(
        "action",
        nargs="?",
        choices=("export", "status"),
        default="status",
        help="export=打包本次运行的重试/报错+截图成 zip（默认 status）",
    )
    p.set_defaults(handler=cmd_feedback)

    p = sub.add_parser(
        "ui", parents=[common], help="认一次当前界面（主界面/选择套牌），只读"
    )
    p.add_argument(
        "--frame",
        nargs="?",
        const="ui_frame.png",
        default=None,
        metavar="文件名",
        help="顺带把这一帧存成 PNG（默认 ui_frame.png）并打印路径",
    )
    p.add_argument(
        "--learn",
        nargs="?",
        const="all",
        default=None,
        metavar="类型",
        help="按当前框重裁识别模板：all(默认)/traditional/start"
        "（皮肤或补丁把字改了之后用）",
    )
    p.set_defaults(handler=cmd_ui)

    p = sub.add_parser("deck", parents=[common], help="旧的自动选卡组设置（已停用；用 deck-click 选）")
    p.add_argument(
        "index",
        nargs="?",
        default=None,
        metavar="序号",
        help="1-9；不给就显示当前设置，给 0 等于关掉",
    )
    p.add_argument(
        "--clear", action="store_true", help="关掉自动选卡组（保持你手选的卡组）"
    )
    p.set_defaults(handler=cmd_deck)

    p = sub.add_parser(
        "enter-battle",
        parents=[common],
        help="一键进入对战（拉起炉石→点传统对战→选卡组）",
    )
    p.add_argument(
        "--deck",
        type=int,
        default=None,
        metavar="序号",
        help="进入后自动选第 N 个卡组（1-9）",
    )
    p.add_argument(
        "--start",
        action="store_true",
        help="选完卡组再点「开始」（等于开始排队；默认只到选择套牌界面）",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        metavar="秒",
        help="等界面时每次识别/点中心的间隔（默认用控制台的 0.5 秒；"
        "调大只会让「取消」反应更慢，一般不用改）",
    )
    p.add_argument(
        "--wait-timeout",
        type=float,
        default=None,
        metavar="秒",
        help="等主界面的超时（默认 180 秒）",
    )
    p.add_argument("--no-wait", action="store_true", help="只下发，不在这里等它跑完")
    p.set_defaults(handler=cmd_enter_battle)
    p = sub.add_parser(
        "deck-click",
        parents=[common],
        help="快速选卡组：直接点第 N 格（秒级，不走状态机）",
    )
    p.add_argument("index", type=int, metavar="序号", help="第几个卡组（1-9，当前页）")
    p.set_defaults(handler=cmd_deck_click)
    p = sub.add_parser(
        "mode", parents=[common], help="手动切换对战模式（wild/standard/casual）"
    )
    p.add_argument(
        "mode", metavar="模式", help="wild | standard | casual（或 狂野/标准/休闲）"
    )
    p.set_defaults(handler=cmd_mode)
    p = sub.add_parser(
        "watchdog",
        parents=[common],
        help="中断重启：日志长时间不变就重启炉石并重开一局",
    )
    p.add_argument(
        "watchdog_cmd",
        nargs="?",
        default="status",
        choices=["on", "off", "status", "set"],
        help="on/off=开关；status=看状态（默认）；set=改参数",
    )
    p.add_argument(
        "--stale",
        type=float,
        default=None,
        metavar="秒",
        help="日志停滞多少秒算卡住（默认 300 = 5 分钟）",
    )
    p.add_argument(
        "--max-restarts",
        dest="max_restarts",
        type=int,
        default=None,
        metavar="次数",
        help="滑动窗口内最多重启几次（默认 3）",
    )
    p.add_argument(
        "--window",
        type=float,
        default=None,
        metavar="秒",
        help="滑动窗口长度（默认 600 = 10 分钟）",
    )
    p.set_defaults(handler=cmd_watchdog)
    p = sub.add_parser(
        "queue", parents=[common], help="待做命令队列：一次提交多条，控制台按顺序执行"
    )
    p.add_argument(
        "queue_cmd",
        nargs="?",
        default="status",
        choices=["run", "status", "cancel"],
        help="run=提交并等待；status=看进度；cancel=取消",
    )
    p.add_argument(
        "steps",
        nargs="*",
        help='run 时：一条条命令字符串，如 "stop mode=after_game" "wait-stopped" "deck-click 1" "start"',
    )
    p.add_argument("--no-wait", action="store_true", help="run 时只提交，不等队列跑完")
    p.set_defaults(handler=cmd_queue)

    p = sub.add_parser(
        "restart-hearthstone",
        parents=[common],
        help="重启炉石并等到主界面（会结束进程并清空其日志目录，高风险）",
    )
    p.add_argument("--yes", action="store_true", help="跳过交互确认")
    p.add_argument(
        "--no-wait",
        action="store_true",
        help="只派发、不等结果（默认等到「传统对战」可点才算完成）",
    )
    p.set_defaults(handler=cmd_restart_hearthstone)

    # ---- config
    p = sub.add_parser("config", parents=[common], help="账号与日志目录")
    p.set_defaults(handler=cmd_config_show)
    csub = p.add_subparsers(dest="action", metavar="<动作>")
    q = csub.add_parser("show", parents=[common], help="显示当前配置")
    q.set_defaults(handler=cmd_config_show)
    q = csub.add_parser("set", parents=[common], help="修改配置")
    q.add_argument("--name", default=None, help="战网昵称，含 #编号")
    q.add_argument("--log-root", default=None, help="炉石 Logs 目录")
    q.add_argument(
        "--no-hot", action="store_true", help="自动化运行中拒绝修改（与网页行为一致）"
    )
    q.set_defaults(handler=cmd_config_set)

    # ---- delays
    p = sub.add_parser("delays", parents=[common], help="延时参数（支持运行中热改）")
    p.set_defaults(handler=cmd_delays_show)
    dsub = p.add_subparsers(dest="action", metavar="<动作>")
    q = dsub.add_parser("show", parents=[common], help="显示当前延时")
    q.set_defaults(handler=cmd_delays_show)
    q = dsub.add_parser("set", parents=[common], help="改延时：键=值 [键=值 ...]")
    q.add_argument("pairs", nargs="+", metavar="键=值")
    q.add_argument(
        "--no-hot", action="store_true", help="自动化运行中拒绝修改（与网页行为一致）"
    )
    q.set_defaults(handler=cmd_delays_set)

    # ---- human-like
    p = sub.add_parser("human-like", parents=[common], help="活人感（运行中可热改）")
    p.set_defaults(handler=cmd_human_like_show)
    hsub = p.add_subparsers(dest="action", metavar="<动作>")
    q = hsub.add_parser("show", parents=[common], help="显示当前配置")
    q.set_defaults(handler=cmd_human_like_show)
    q = hsub.add_parser("set", parents=[common], help="修改配置")
    _add_bool(q, "enabled", "总开关")
    _add_bool(q, "hand-hover", "看卡牌（随机延时）", dest="hand_hover")
    _add_bool(q, "minion-hover", "看随从", dest="minion_hover")
    q.add_argument("--post-delay-min", type=float, default=None)
    q.add_argument("--post-delay-max", type=float, default=None)
    q.add_argument("--hover-min", type=float, default=None)
    q.add_argument("--hover-max", type=float, default=None)
    q.add_argument("--minion-hover-min", type=float, default=None)
    q.add_argument("--minion-hover-max", type=float, default=None)
    q.add_argument("--no-hot", action="store_true", help="运行中拒绝修改")
    q.set_defaults(handler=cmd_human_like_set)

    # ---- liveness
    p = sub.add_parser("liveness", parents=[common], help="炉石存活检测")
    p.set_defaults(handler=cmd_liveness_show)
    lsub = p.add_subparsers(dest="action", metavar="<动作>")
    q = lsub.add_parser("show", parents=[common], help="显示当前配置")
    q.set_defaults(handler=cmd_liveness_show)
    q = lsub.add_parser("set", parents=[common], help="修改配置")
    _add_bool(q, "enabled", "总开关")
    q.add_argument("--warn", type=float, default=None, help="Power.log 停滞多少秒告警")
    q.add_argument(
        "--stop", type=float, default=None, help="Power.log 停滞多少秒自动停止"
    )
    q.add_argument("--no-hot", action="store_true", help="运行中拒绝修改")
    q.set_defaults(handler=cmd_liveness_set)

    # ---- concede
    p = sub.add_parser(
        "concede", parents=[common], help="自动投降配置 / 手动投降（concede now）"
    )
    p.set_defaults(handler=cmd_concede_show)
    csub2 = p.add_subparsers(dest="action", metavar="<动作>")
    q = csub2.add_parser("show", parents=[common], help="显示当前配置")
    q.set_defaults(handler=cmd_concede_show)
    q = csub2.add_parser("set", parents=[common], help="修改配置")
    _add_bool(q, "enabled", "总开关")
    q.add_argument(
        "--threshold", type=float, default=None, help="AI 胜率阈值（百分比 0-100）"
    )
    q.add_argument("--rounds", type=int, default=None, help="连续低于阈值的回合数")
    q.add_argument("--no-hot", action="store_true", help="运行中拒绝修改")
    q.set_defaults(handler=cmd_concede_set)
    q = csub2.add_parser(
        "now",
        parents=[common],
        help="手动投降：立刻点「齿轮 → 认输」（无需先停自动化）",
    )
    q.set_defaults(handler=cmd_concede_now)

    # ---- surrender（卡组识别投降：只判对方）
    p = sub.add_parser(
        "surrender",
        parents=[common],
        help="卡组识别投降：认出对方是哪套卡组就认输（只判对方）",
    )
    p.set_defaults(handler=cmd_surrender)
    ssub = p.add_subparsers(dest="action", metavar="<动作>")
    q = ssub.add_parser("show", parents=[common], help="看配置（默认）")
    q.set_defaults(handler=cmd_surrender)
    q = ssub.add_parser("on", parents=[common], help="打开总开关")
    q.set_defaults(handler=cmd_surrender)
    q = ssub.add_parser("off", parents=[common], help="关掉总开关（保留卡组）")
    q.set_defaults(handler=cmd_surrender)
    q = ssub.add_parser(
        "check",
        parents=[common],
        help="看这一局的实时判定（对方职业 / 每个条件是否满足）",
    )
    q.set_defaults(handler=cmd_surrender)
    q = ssub.add_parser(
        "set",
        parents=[common],
        help="整段替换规则（--file rules.json，- 表示读 stdin）",
    )
    q.add_argument("--file", default=None, help="规则 JSON 文件路径（- = stdin）")
    q.add_argument(
        "--rules",
        default=None,
        help="直接给 JSON 文本（最外层是 {enabled, decks} 或单套卡组）",
    )
    q.add_argument(
        "--yes",
        action="store_true",
        help="已经在当前对局里能命中时，仍然保存（否则需二次确认）",
    )
    q.set_defaults(handler=cmd_surrender)
    q = ssub.add_parser("clear", parents=[common], help="清空所有卡组并关掉开关")
    q.set_defaults(handler=cmd_surrender)

    # ---- card（本地查卡，不联网也不需要控制台）
    p = sub.add_parser(
        "card", parents=[common], help="查卡：中文名补全（本地 cards.json）"
    )
    p.add_argument("words", nargs="+", metavar="<卡名或ID>", help="要查的名字/ID")
    p.add_argument("--limit", type=int, default=8, help="最多返回几条候选（默认 8）")
    p.add_argument(
        "--show", action="store_true", help="单卡详情（含自动纠错与同名多版本 id）"
    )
    p.set_defaults(handler=cmd_card)

    # ---- rank-stop
    p = sub.add_parser("rank-stop", parents=[common], help="段位目标")
    p.set_defaults(handler=cmd_rank_stop_show)
    rsub = p.add_subparsers(dest="action", metavar="<动作>")
    q = rsub.add_parser("show", parents=[common], help="显示当前配置")
    q.set_defaults(handler=cmd_rank_stop_show)
    q = rsub.add_parser("set", parents=[common], help="修改配置")
    _add_bool(q, "enabled", "总开关")
    q.add_argument(
        "--mode",
        choices=("legend", "legend_number"),
        default=None,
        help="legend=上传说即停；legend_number=上到指定名次",
    )
    q.add_argument("--legend-number", type=int, default=None, help="目标名次")
    q.add_argument(
        "--action",
        choices=("stop", "keep"),
        default=None,
        help="stop=达到后停止；keep=控分（名次比目标更靠前就开局投降）",
    )
    q.add_argument("--no-hot", action="store_true", help="运行中拒绝修改")
    q.set_defaults(handler=cmd_rank_stop_set)

    # ---- schedule
    p = sub.add_parser("schedule", parents=[common], help="定时任务")
    p.set_defaults(handler=cmd_schedule_show)
    ssub = p.add_subparsers(dest="action", metavar="<动作>")
    q = ssub.add_parser("show", parents=[common], help="显示当前定时任务")
    q.set_defaults(handler=cmd_schedule_show)
    q = ssub.add_parser(
        "at", parents=[common], help="设置开始时间：22:30 / 2026-05-30T22:30 / --in 30"
    )
    q.add_argument("start", nargs="?", default=None, help="开始时间")
    q.add_argument(
        "--in",
        dest="in_minutes",
        type=float,
        default=None,
        metavar="分钟",
        help="N 分钟后开始",
    )
    q.add_argument("--end", default=None, help="结束时间")
    q.add_argument(
        "--duration", type=float, default=None, metavar="分钟", help="持续 N 分钟后结束"
    )
    q.set_defaults(handler=cmd_schedule_at)
    q = ssub.add_parser("cancel", parents=[common], help="取消定时任务")
    q.set_defaults(handler=cmd_schedule_cancel)

    # ---- serve
    p = sub.add_parser(
        "serve",
        parents=[common],
        help="托管控制台进程（start/stop/status/restart/install-task）",
    )
    p.set_defaults(handler=cmd_serve, action="status")
    vsub = p.add_subparsers(dest="sub_action", metavar="<动作>")
    q = vsub.add_parser("status", parents=[common], help="控制台是否在运行")
    q.set_defaults(handler=cmd_serve, action="status")
    q = vsub.add_parser("start", parents=[common], help="后台拉起控制台")
    q.add_argument("--browser", action="store_true", help="同时打开浏览器")
    q.add_argument("--wait", type=float, default=60.0, help="最长等待秒数")
    _add_launch_flags(q)
    q.set_defaults(handler=cmd_serve, action="start")
    q = vsub.add_parser("stop", parents=[common], help="停止控制台")
    q.add_argument(
        "--force", action="store_true", help="即使 pid 看起来不是 Python 进程也结束它"
    )
    q.add_argument(
        "--hard",
        action="store_true",
        help="跳过 HTTP 自停通道，直接结束进程（默认先试自停）",
    )
    q.set_defaults(handler=cmd_serve, action="stop")
    q = vsub.add_parser("restart", parents=[common], help="重启控制台")
    q.add_argument("--browser", action="store_true")
    q.add_argument("--wait", type=float, default=60.0)
    q.add_argument("--force", action="store_true")
    _add_launch_flags(q)
    q.set_defaults(handler=cmd_serve, action="restart")
    q = vsub.add_parser(
        "install-task",
        parents=[common],
        help="注册「最高权限」计划任务（需管理员跑一次）",
    )
    q.add_argument("--force", action="store_true", help="已存在时覆盖重建")
    q.set_defaults(handler=cmd_serve, action="install-task")
    q = vsub.add_parser("uninstall-task", parents=[common], help="删除该计划任务")
    q.set_defaults(handler=cmd_serve, action="uninstall-task")
    q = vsub.add_parser(
        "task-status", parents=[common], help="看计划任务是否存在、是否勾了最高权限"
    )
    q.set_defaults(handler=cmd_serve, action="task-status")

    p = sub.add_parser("repl", parents=[common], help="进入交互模式")
    p.set_defaults(handler=None)

    p = sub.add_parser(
        "doctor",
        parents=[common],
        help="一条命令自诊：Python 环境 / 控制台 / 提权 / 下一步做什么",
    )
    p.set_defaults(handler=cmd_doctor)

    p = sub.add_parser(
        "env", parents=[common], help="看/固定本机 Python 解释器（保存到 log/env.json）"
    )
    esub = p.add_subparsers(dest="env_action", metavar="<动作>")
    q = esub.add_parser("show", parents=[common], help="看当前与已固定的解释器")
    q.set_defaults(handler=cmd_env, env_action="show")
    q = esub.add_parser(
        "discover", parents=[common], help="列出本机候选解释器并逐个校验（不写盘）"
    )
    q.set_defaults(handler=cmd_env, env_action="discover")
    q = esub.add_parser(
        "pin", parents=[common], help="校验并保存本机解释器到 log/env.json"
    )
    q.add_argument(
        "path",
        nargs="?",
        default="auto",
        help="python.exe 路径；auto=自动选第一个合格候选",
    )
    q.set_defaults(handler=cmd_env, env_action="pin")
    p.set_defaults(handler=cmd_env, env_action="show")

    return parser


# --------------------------------------------------------------------- REPL
BANNER = f"""\
hsctl {version_label()} —— 炉石脚本命令行控制台
输入命令（不带 hsctl 前缀），help 看帮助，exit / Ctrl+D 退出。
例：status | log -f | start | stop --after-game | delays set pre_action_delay_seconds=5
"""


def run_repl(parser) -> int:
    print(BANNER)
    while True:
        try:
            line = input("hsctl> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return EXIT_OK
        if not line:
            continue
        if line in ("exit", "quit", ":q", "q"):
            return EXIT_OK
        if line in ("help", "?", "h"):
            parser.print_help()
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            print(f"✗ 命令解析失败：{exc}")
            continue
        if tokens and tokens[0] == "hsctl":
            tokens = tokens[1:]
        try:
            ns = parser.parse_args(tokens)
        except SystemExit:
            # argparse 出错/打印帮助会 raise SystemExit；REPL 不能被它带走。
            continue
        if getattr(ns, "handler", None) is None:
            parser.print_help()
            continue
        dispatch(ns)


def dispatch(args) -> int:
    ctx = Ctx(args)
    handler = getattr(args, "handler", None)
    if handler is None:
        return EXIT_USAGE
    try:
        return handler(args, ctx)
    except ApiFailure as exc:
        return _fail(ctx, "api", str(exc))
    except TransportError as exc:
        return _fail(ctx, "transport", str(exc))
    except KeyboardInterrupt:
        print()
        return EXIT_OK


def _fail(ctx: Ctx, kind: str, message: str) -> int:
    if ctx.json:
        # JSON 模式下把错误也打成 JSON（stdout），agent 只读 stdout 也能判断。
        print(
            json.dumps(
                {"ok": False, "kind": kind, "error": message}, ensure_ascii=False
            )
        )
    else:
        print(f"✗ {message}", file=sys.stderr)
    return EXIT_API if kind == "api" else EXIT_UNREACHABLE


def _make_output_encoding_safe() -> None:
    """让输出永远不会因为"编码里没有这个字符"而崩掉。

    中文 Windows 的控制台默认是 GBK（cp936），`✓ ✗ ✅ ❌ ℹ️` 这些字符都不在里面：
    直接 print 会抛 UnicodeEncodeError —— 命令**已经执行成功**，却在最后一行报错退出，
    对 agent 来说就是"明明做了却说失败"。这里把不可编码的字符降级成 `?`，
    UTF-8 控制台（以及测试里重定向到 StringIO）完全不受影响。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def _ensure_interpreter_or_exit(argv=None) -> int:
    """入口处的解释器保证。返回 EXIT_OK 继续跑，或直接给出退出码/重入。

    * 当前解释器合格（3.12 + 依赖齐全）→ 什么都不做（**不额外起子进程**，只写一次缓存）；
    * 不合格但本机有合格解释器（log/env.json / HS_PYTHON / 候选清单）→ 顺手保存到
      `log/env.json` 并 `os.execv` 重入，之后的命令都在对的解释器里跑；
    * 找不到合格解释器 → 打印"怎么修"，退出码 4（agent 见到就 `hsctl doctor`）。
    """
    # 测试/嵌入调用传入 argv 时不自动重入（避免把测试进程换掉）。
    if argv is not None:
        return EXIT_OK
    outcome = ensure_interpreter()
    if outcome.get("changed"):
        return EXIT_OK  # os.execv 成功就不会走到这里
    if outcome.get("path"):
        return EXIT_OK
    record = outcome.get("record") or {}
    print(
        "✗ 找不到可用的 Python 环境：本项目的控制台/自动化必须跑在 "
        f"Python {EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]} + requirements.txt 的依赖上。",
        file=sys.stderr,
    )
    print(
        f"  当前解释器：{outcome.get('reason') or record.get('reason') or '未知'}",
        file=sys.stderr,
    )
    print("  下一步（任选一条，装完再回来）：", file=sys.stderr)
    print("    ① 已有合格环境 → hsctl env --pin <python.exe 路径>", file=sys.stderr)
    print(
        "    ② 用 conda 建一个 → conda create -p <你的路径>\\envs\\HTL python=3.12",
        file=sys.stderr,
    )
    print("    ③ 用 venv 建一个   → python -m venv .venv", file=sys.stderr)
    print(
        '  然后："<新解释器>" -m pip install -r requirements.txt '
        "-i https://pypi.tuna.tsinghua.edu.cn/simple",
        file=sys.stderr,
    )
    print(
        "  诊断：python hsctl.py --json doctor（会列出本机所有候选解释器与缺什么）",
        file=sys.stderr,
    )
    return EXIT_ENV


def main(argv=None) -> int:
    _make_output_encoding_safe()
    # DPI 感知必须最先声明：未声明时 Windows 会把客户区几何按系统缩放虚拟化
    # （150% 下 1920×1080 报成 1280×720），而抓图是物理像素 —— 两者对不上会
    # 让所有 ROI 错位、界面识别失败。见 display_mapping 模块 docstring。
    try:
        from display_mapping import dpi_aware

        dpi_aware()
    except Exception:
        pass
    env_code = _ensure_interpreter_or_exit(argv)
    if env_code != EXIT_OK:
        return env_code
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "handler", None) is None:
        # 裸命令：交互终端进 REPL，否则打帮助（便于 agent 发现命令）。
        if args.command == "repl" or (args.command is None and sys.stdin.isatty()):
            return run_repl(parser)
        parser.print_help()
        return EXIT_USAGE
    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
