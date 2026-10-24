#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""hsctl MCP server —— 把炉石脚本命令行控制台（hsctl）暴露成 MCP tools（stdio）。

设计
----
本进程是 `hsctl.py` 的**薄封装**：真正干活的是 `python hsctl.py --json <命令>`，
不含任何自动化逻辑。所以它跟 hsctl 一样，是「实时控制正在运行的脚本」的入口，
不是脚本本体。环境不对时 hsctl 自己会切到本机固定解释器（`log/env.json`）。

MCP 客户端配置（stdio）::

    {
      "mcpServers": {
        "hsctl": {
          "command": "D:\\\\miniconda3\\\\envs\\\\HTL\\\\python.exe",
          "args": ["<仓库路径>\\\\mcp_server.py"]
        }
      }
    }

`command` 用哪个解释器都行（本文件会自己处理），但那个解释器必须装了 `mcp`
（见 requirements.txt）；跑命令时 hsctl 会再切到本机固定的 HTL 环境。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# 版本号的唯一来源（只依赖标准库；必须在下面把仓库根从 sys.path 摘掉之前导入）。
import version  # noqa: E402

# 仓库根目录有 click.py（炉石的鼠标点击模块），`import click` 会解析到它而不是
# PyPI 的 click —— mcp 依赖的 uvicorn/starlette 会因此在导入时直接炸。导入 mcp
# 之前先把仓库根从 sys.path 摘掉；之后靠子进程跑 hsctl，不需要它在 sys.path 里。
_ROOT = Path(__file__).resolve().parent
for _path in ("", str(_ROOT), os.getcwd()):
    while _path in sys.path:
        sys.path.remove(_path)

from mcp.server.mcpserver import Image, MCPServer  # noqa: E402

_HSCTL = _ROOT / "hsctl.py"
_DEFAULT_TIMEOUT = 180.0

server = MCPServer(
    name="hsctl",
    version=version.version_label(),
    instructions=(
        "炉石脚本（HSLegendArriver）的命令行控制台。会话第一条先调 doctor，"
        "看环境 / 控制台 / 提权状态与它给的下一步。"
        "所有工具返回 JSON（含 exit_code 与 hsctl 的机器可读输出，ok=false 时看 "
        "data.error / data.next_steps）。screenshot 会直接把 PNG 返回给你看。"
        "退出码：0 成功 / 1 业务失败 / 2 参数错 / 3 连不上控制台 / 4 环境不可用。"
    ),
)


# ------------------------------------------------------------------ 底层
def _run(args, timeout: float = _DEFAULT_TIMEOUT) -> dict:
    """跑 `hsctl --json <args>`，返回结构化结果（绝不抛异常）。"""
    cmd = [sys.executable, str(_HSCTL), "--json", *[str(a) for a in args]]
    # hsctl 是子进程、stdout 是管道时，Python 默认按系统 locale（中文 Windows = GBK）
    # 写字节；这里强制它用 UTF-8，否则中文/JSON 会变成乱码（replacement char）。
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": None,
            "error": f"命令超时（{timeout:g}s）：hsctl {' '.join(str(a) for a in args)}",
        }
    except Exception as exc:  # 解释器都起不来等
        return {"ok": False, "exit_code": None, "error": f"{type(exc).__name__}: {exc}"}
    out = (proc.stdout or "").strip()
    result: dict = {"ok": proc.returncode == 0, "exit_code": proc.returncode}
    if out:
        try:
            result["data"] = json.loads(out)
        except ValueError:
            result["stdout"] = out
    err = (proc.stderr or "").strip()
    if err:
        result["stderr"] = err[-4000:]
    return result


def _json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


def _value(result: dict, *keys):
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    for key in keys:
        if data.get(key):
            return data[key]
    return None


# ------------------------------------------------------------------ 自诊 / 只读
@server.tool(
    description="一条命令自诊：Python 环境 / 控制台 / 提权 / 下一步做什么。会话第一条先跑它。"
)
def doctor() -> str:
    return _json(_run(["doctor"]))


@server.tool(description="环境自检（依赖 / 控制台 / 各功能是否可用）。")
def selfcheck(refresh: bool = False) -> str:
    args = ["selfcheck"] + (["--refresh"] if refresh else [])
    return _json(_run(args))


@server.tool(description="看当前状态：阶段 / 账号是否匹配 / 战绩 / 参数。")
def status() -> str:
    return _json(_run(["status"]))


@server.tool(description="读最近的运行日志（不跟随）。lines=要读的行数（默认 50）。")
def log(lines: int = 50) -> str:
    return _json(_run(["log", "-n", str(int(lines))]))


@server.tool(description="认一次当前界面（主界面 / 选择套牌 / 没认出），只读不点击。")
def ui() -> str:
    return _json(_run(["ui"]))


@server.tool(
    description="抓一帧画面并把 PNG 直接返回给你看。kind: client=炉石客户区(默认) / full=整屏 / roi=推荐面板(OCR 的输入) / boxes=整屏+区域框。"
)
def screenshot(kind: str = "client", max_width: int = 1280):
    result = _run(
        [
            "screenshot",
            "--kind",
            kind,
            "--max-width",
            str(int(max_width)),
            "--out",
            "agent_mcp.png",
        ]
    )
    path = _value(result, "path", "latest")
    if not path or not os.path.exists(str(path)):
        return _json(result)  # 失败时退回文本，agent 能看到 error / next_steps
    return Image(path=str(path))


# ------------------------------------------------------------------ 对战流程
@server.tool(
    description="开始对战（会二次确认吗？不会，直接开始）。keep_score=true 时保留已有战绩。"
)
def start(keep_score: bool = False) -> str:
    args = ["start"] + (["--keep-score"] if keep_score else [])
    return _json(_run(args))


@server.tool(
    description="停止自动化。after_game=true 表示打完当前这一局再停（默认立即停）。"
)
def stop(after_game: bool = False) -> str:
    args = ["stop"] + (["--after-game"] if after_game else [])
    return _json(_run(args))


@server.tool(description="恢复自动化（战绩继续累计，不清零）。")
def resume() -> str:
    return _json(_run(["resume"]))


@server.tool(description="开始运行（准备）：开浮窗 + 把炉石切到前台，但不开打。")
def prepare() -> str:
    return _json(_run(["prepare"]))


@server.tool(
    description="急停：叫停正在跑的「进对战 / 等界面」并停止自动化（比 stop 更彻底）。"
)
def cancel() -> str:
    return _json(_run(["cancel"]))


@server.tool(
    description="一键进入对战：拉起炉石 → 点传统对战 → 选卡组。deck=进入后选第几个卡组(1-9)；start=true 再点「开始」开始排队。"
)
def enter_battle(
    deck: int | None = None, start_battle: bool = False, wait: bool = True
) -> str:
    args = ["enter-battle"]
    if deck is not None:
        args += ["--deck", str(int(deck))]
    if start_battle:
        args += ["--start"]
    if not wait:
        args += ["--no-wait"]
    return _json(_run(args, timeout=300.0))


@server.tool(
    description="切换对战模式：wild | standard | casual（也认中文 狂野/标准/休闲）。"
)
def mode(mode: str) -> str:
    return _json(_run(["mode", mode]))


@server.tool(
    description="已停用：自动化不再每局自动选卡组。index 会被拒绝；请用 enter_battle(deck=N) 显式选，或 CLI deck-click N。"
)
def deck(index: int | None = None, clear: bool = False) -> str:
    args = ["deck"]
    if index is not None:
        args.append(str(int(index)))
    if clear:
        args += ["--clear"]
    return _json(_run(args))


@server.tool(
    description="打开屏幕上的校准窗口（人工对齐各区域框）。target 可指定一打开就停在哪一项。"
)
def calibrate(target: str | None = None) -> str:
    args = ["calibrate"] + (["--target", target] if target else [])
    return _json(_run(args))


@server.tool(
    description="重启炉石并等到主界面（高风险：会结束 Hearthstone.exe 并清空其日志目录）。必须 confirm=true 才执行。"
)
def restart_hearthstone(confirm: bool = False, wait: bool = True) -> str:
    if not confirm:
        return _json(
            {
                "ok": False,
                "error": "restart_hearthstone 是高风险操作（结束炉石进程 + 清空其日志目录）。"
                "确认要执行请带 confirm=true 再调一次。",
            }
        )
    args = ["restart-hearthstone", "--yes"] + ([] if wait else ["--no-wait"])
    return _json(_run(args, timeout=600.0))


# ------------------------------------------------------------------ 参数热改
@server.tool(description="显示当前配置（战网昵称 / 炉石日志目录）。")
def config_show() -> str:
    return _json(_run(["config", "show"]))


@server.tool(
    description="修改配置：name=战网昵称（含 #编号）；log_root=炉石 Logs 目录。"
)
def config_set(name: str | None = None, log_root: str | None = None) -> str:
    args = ["config", "set"]
    if name is not None:
        args += ["--name", name]
    if log_root is not None:
        args += ["--log-root", log_root]
    if len(args) == 2:
        return _json({"ok": False, "error": "至少要给 name 或 log_root 之一。"})
    return _json(_run(args))


@server.tool(description="显示当前延时参数。")
def delays_show() -> str:
    return _json(_run(["delays", "show"]))


@server.tool(
    description="改延时参数（支持运行中热改）。pairs 是 ['键=值', ...]，例如 ['pre_action_delay_seconds=5']。"
)
def delays_set(pairs: list[str]) -> str:
    if not pairs:
        return _json({"ok": False, "error": "pairs 不能为空。"})
    return _json(_run(["delays", "set", *pairs]))


# ------------------------------------------------------------------ 万能兜底
@server.tool(
    description="万能兜底：直接跑任意 hsctl 子命令（自动加 --json）。argv 是参数数组，"
    "例如 ['deck','8']、['mode','wild']、['concede','now']、['serve','status']、"
    "['rank-stop','set','--enabled']。完整命令见 SKILL.md。"
)
def hsctl(argv: list[str], timeout: float = _DEFAULT_TIMEOUT) -> str:
    if not argv:
        return _json({"ok": False, "error": "argv 不能为空。"})
    return _json(_run(argv, timeout=float(timeout)))


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
