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

HSCTL_VERSION = "1.0.0"

ROOT = Path(__file__).resolve().parent
PORT_FILE = ROOT / "log" / "ui_port.txt"
PID_FILE = ROOT / "log" / "ui_server.pid"
SERVE_LOG = ROOT / "log" / "cli_serve.log"
# 「最高权限」计划任务的启动器（由 serve install-task 生成；任务指向它而不是内联命令）
TASK_LAUNCHER = ROOT / "log" / "hs_serve_elevated.cmd"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_BASE_PORT = 8765
PORT_SCAN_COUNT = 20            # 与 web_ui.py 的 BASE_PORT..BASE_PORT+19 对齐
DEFAULT_TIMEOUT = 15.0
PROBE_TIMEOUT = 0.3

EXIT_OK = 0
EXIT_API = 1
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3

# 「进入对战」的最长等待：等主界面 + 等选择套牌界面 + 拉起炉石，再留一点余量。
ENTER_BATTLE_WAIT_SECONDS = 600.0
ENTER_BATTLE_POLL_SECONDS = 2.0

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
        self.timeout = float(getattr(args, "timeout", DEFAULT_TIMEOUT)
                             or DEFAULT_TIMEOUT)
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
            "  或用本 CLI 托管：hsctl serve start")

    def _probe(self, base: str) -> bool:
        try:
            with urllib.request.urlopen(base + "/api/status",
                                        timeout=PROBE_TIMEOUT) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _require_alive(self, base: str):
        if not self._probe(base):
            raise TransportError(
                f"连不上 {base}：脚本没在运行，或端口不对。\n"
                "  先启动脚本：python web_ui.py --no-browser\n"
                "  或用本 CLI 托管：hsctl serve start")

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
            raise TransportError(
                f"控制台对 {path} 返回了非 JSON 响应：{raw[:200]!r}")
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
    lines.append(f"控制台   {url or '-'}   （管理员权限："
                 f"{'是' if s.get('is_admin') else '否'}）")
    lines.append(f"状态     {head} · {phase} · {s.get('state_label') or '待机'}")
    if s.get("stop_after_game"):
        lines.append("停止     ⏳ 已请求「本局结束后停止」")
    lines.append(f"就绪     {'是' if s.get('prepared') else '否'}"
                 f"     浮窗 {_flag(s.get('overlay_running'))}"
                 f"     热键 {'已注册' if s.get('hotkey_ok') else '未注册'}")

    nm = s.get("name_match") or {}
    matched = nm.get("matched")
    mark = {True: "✅ 匹配", False: "❌ 不匹配（检查用户 ID）"}.get(
        matched, "… 未读到玩家名")
    name = (s.get("config") or {}).get("name") or nm.get("config") or "（未填写）"
    lines.append(f"账号     {name}   {mark}")

    games = int(s.get("games") or 0)
    wins = int(s.get("wins") or 0)
    lines.append(
        f"战绩     {games} 场 {wins} 胜 ({_num(s.get('win_rate'))}%)"
        f" · 认输 {int(s.get('concede_count') or 0)}"
        f" · 本局 {_dur(s.get('game_elapsed_sec'))}")

    lv = s.get("liveness") or {}
    lines.append(
        f"存活检测 {_flag(lv.get('enabled'))} · 炉石 {_num(lv.get('status'))}"
        f" · 停滞 {_num(lv.get('log_age'))}s"
        f" ({_num(lv.get('log_stale_warn_seconds'))}s 告警 /"
        f" {_num(lv.get('log_stale_stop_seconds'))}s 停止)")
    if lv.get("alert"):
        lines.append(f"         ⚠️ {lv['alert']}")

    cd = s.get("concede") or {}
    det = s.get("concede_detect") or {}
    extra = ""
    if det.get("rate") is not None:
        extra = f" (最近胜率 {_num(det.get('rate'))}% ×{_num(det.get('streak'))})"
    lines.append(f"自动投降 {_flag(cd.get('enabled'))} · 胜率<"
                 f"{_num(cd.get('threshold'))}% 连续 "
                 f"{_num(cd.get('rounds'))} 回合{extra}")

    rk = s.get("rank_stop") or {}
    if not rk.get("enabled"):
        rank_text = "关"
    elif rk.get("mode") == "legend":
        rank_text = "开 · 上传说即停"
    else:
        rank_text = f"开 · 上到传说第 {_num(rk.get('legend_number'))} 名即停"
    lines.append(f"上分停止 {rank_text}")

    hl = s.get("human_like") or {}
    lines.append(
        f"活人感   {_flag(hl.get('enabled'))}"
        f" · 看卡牌 {_flag(hl.get('hand_hover_enabled'))}"
        f" {_num(hl.get('post_delay_min'))}~{_num(hl.get('post_delay_max'))}s"
        f" · 悬停 {_num(hl.get('hover_min'))}~{_num(hl.get('hover_max'))}s"
        f" · 看随从 {_flag(hl.get('minion_hover_enabled'))}"
        f" {_num(hl.get('minion_hover_min'))}~{_num(hl.get('minion_hover_max'))}s")

    sched = (s.get("schedule_start"), s.get("schedule_end"))
    lines.append("定时     " + (f"{sched[0]} → {sched[1] or '（不设结束）'}"
                                if sched[0] else "（无）"))

    delays = s.get("delays") or {}
    if delays:
        items = [f"{k.replace('_seconds', '').replace('_scale', '')}="
                 f"{_num(v)}" for k, v in delays.items()]
        lines.append("延时     " + "  ".join(items[:4]))
        if len(items) > 4:
            lines.append("         " + "  ".join(items[4:]))

    sc = s.get("selfcheck") or {}
    if not sc.get("checked"):
        lines.append("自检     尚未完成")
    else:
        lines.append(f"自检     {'✅ 通过' if sc.get('ok') else '❌ 未通过'}"
                     f" ({int(sc.get('failed') or 0)} 失败 /"
                     f" {int(sc.get('warned') or 0)} 警告)"
                     f" · Python {_num(sc.get('python'))}")

    lines.append(f"最后错误 {s.get('last_error') or '（无）'}")
    if not s.get("is_admin"):
        lines.append("⚠️  非管理员权限：点击可能被系统拦掉（现象是「脚本在跑但点了没反应」）。")
    return "\n".join(lines)


def _one_line(s: dict) -> str:
    running = bool(s.get("running"))
    head = "运行中" if running else ("启动中" if s.get("starting") else "未运行")
    games = int(s.get("games") or 0)
    wins = int(s.get("wins") or 0)
    return (f"{datetime.now():%H:%M:%S}  {head}·{s.get('state_label') or '待机'}"
            f"  {wins}胜/{games}场({_num(s.get('win_rate'))}%)"
            f"  本局 {_dur(s.get('game_elapsed_sec'))}"
            f"  浮窗{_flag(s.get('overlay_running'))}"
            + ("  [本局后停]" if s.get("stop_after_game") else ""))


def _log_line(entry, json_mode: bool) -> str:
    if json_mode:
        return json.dumps(entry, ensure_ascii=False)
    if isinstance(entry, dict):
        return (f"{entry.get('t', '--:--:--')} [{entry.get('level', '?'):>5}] "
                f"{entry.get('msg', '')}")
    return str(entry)


def _ok_message(ctx: Ctx, payload: dict) -> int:
    """POST 类命令的统一回话。"""
    msg = payload.get("message") or "完成"
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"✓ {msg}")
        for warn in (payload.get("warnings") or []):
            print(f"  ⚠️ {warn}")
        note = payload.get("note")
        if note:
            print(f"  ℹ️ {note}")
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
        lines = lines[-int(args.lines):]
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
    payload = (ctx.post("/api/selfcheck") if args.refresh
               else ctx.get("/api/selfcheck"))
    result = payload.get("result") or {}
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    print(f"环境自检：{'✅ 通过' if result.get('ok') else '❌ 未通过'}"
          f"  ({result.get('summary') or ''})")
    for item in result.get("checks") or []:
        status = str(item.get("status") or "")
        mark = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(status, "•")
        print(f"  {mark} {item.get('label') or item.get('name') or ''}"
              f"  {item.get('detail') or item.get('message') or ''}".rstrip())
    return EXIT_OK


def cmd_start(args, ctx: Ctx) -> int:
    return _ok_message(ctx, ctx.post("/api/start",
                                     {"keep_score": bool(args.keep_score)}))


def cmd_resume(args, ctx: Ctx) -> int:
    """浮窗「恢复」的语义：续跑，战绩继续累计（不清零）。"""
    return _ok_message(ctx, ctx.post("/api/start", {"keep_score": True}))


def cmd_prepare(args, ctx: Ctx) -> int:
    return _ok_message(ctx, ctx.post("/api/prepare", {}))


def cmd_stop(args, ctx: Ctx) -> int:
    return _ok_message(ctx, ctx.post(
        "/api/stop", {"mode": "after_game" if args.after_game else "now"}))


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
    print(f"  卡组格    {deck.get('slots_found')}/9 格有名牌文字"
          f"（≥6 才算卡组界面）")
    if ui.get("note"):
        print(f"  注：{ui['note']}")
    index = payload.get("deck_index")
    print(f"  自动选卡组：{'第 %s 个' % index if index else '关（保持你手选的卡组）'}")
    if payload.get("frame_path"):
        print(f"  这一帧已存：{payload['frame_path']}（可以直接读这张图核对）")
    return EXIT_OK


def cmd_deck(args, ctx: Ctx) -> int:
    """看/设「自动选择的卡组序号」（1-9；`--clear` 或 0 = 关掉）。

    每次开局前都会重新读盘，所以改完下一局就生效。
    """
    index = getattr(args, "index", None)
    if getattr(args, "clear", False) or (index is not None and int(index) <= 0):
        return _ok_message(ctx, ctx.post("/api/deck", {"clear": True}))
    if index is None:
        payload = ctx.get("/api/ui_state")
        current = payload.get("deck_index")
        message = (f"自动选卡组：第 {current} 个" if current
                   else "自动选卡组：关（保持你手选的卡组）")
        if ctx.json:
            print(json.dumps({"ok": True, "deck_index": current,
                              "message": message}, ensure_ascii=False))
        else:
            print(message)
            print("  设一个：python hsctl.py deck 8     关掉：python hsctl.py deck --clear")
        return EXIT_OK
    if not 1 <= int(index) <= 9:
        print(f"卡组序号必须是 1-9（当前页只有 9 格），收到 {index}", file=sys.stderr)
        return EXIT_USAGE
    return _ok_message(ctx, ctx.post("/api/deck", {"index": int(index)}))


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


def cmd_queue(args, ctx: Ctx) -> int:
    """待做命令队列：一次性提交多条命令，控制台按顺序执行（断线不影响）。

    用法：
      hsctl queue run "stop mode=after_game" "wait-stopped" "deck 1" "start"
      hsctl queue status
      hsctl queue cancel
    支持：stop [mode=after_game|now] / wait-stopped [timeout=秒] /
          deck 序号 | deck-click 序号 / prepare / start [keep_score=1] / delay 秒
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
            mark = {"pending": "…", "running": "▶", "ok": "✓",
                    "failed": "✗", "skipped": "－", "cancelled": "⊘"}.get(item.get("status"), "?")
            print(f"  {mark} [{i + 1}] {item.get('action')} {item.get('args') or ''}")
        return EXIT_OK
    if sub == "cancel":
        return _ok_message(ctx, ctx.post("/api/queue/cancel", {}))
    # run：把剩余位置参数当 step 字符串提交
    steps = list(getattr(args, "steps", []) or [])
    if not steps:
        print("用法：hsctl queue run \"stop mode=after_game\" \"wait-stopped\" \"deck 1\" \"start\"",
              file=sys.stderr)
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
                print(f"{'✅' if all(i.get('status') == 'ok' for i in q.get('items') or []) else '❌'} "
                      f"{q.get('message')}")
            return EXIT_OK if not q.get("items") or all(
                i.get("status") == "ok" for i in q["items"]) else EXIT_API
        time.sleep(2)


def cmd_enter_battle(args, ctx: Ctx) -> int:
    """一键进入对战：拉起炉石 → 等主界面 → 点「传统对战」→ 选卡组 →（可选）点「开始」。

    认不出界面时只会点画面中心（安全点），到超时就退出并说明原因 —— 不会盲点。
    默认**不点「开始」**（那等于开始排队）；要一路打到排队加 `--start`。
    """
    body = {"click_start": bool(getattr(args, "start", False)),
            "interval": float(getattr(args, "interval", 3.0))}
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
    """等这次「进入对战」跑完（轮询便宜的进度接口，不反复截屏）。"""
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
                    print(f"    · {step.get('stage')}："
                          f"{step.get('detail') or ''}{at}")
            return EXIT_OK if state.get("ok") else EXIT_API
        time.sleep(ENTER_BATTLE_POLL_SECONDS)
    print("等待超时：后台可能还在跑，用 python hsctl.py log -f 看进度。",
          file=sys.stderr)
    return EXIT_API


def cmd_overlay(args, ctx: Ctx) -> int:
    action = args.action
    if action == "toggle":
        return _ok_message(ctx, ctx.post("/api/overlay", {}))
    running = bool((ctx.get("/api/status") or {}).get("overlay_running"))
    want = (action == "on")
    if running == want:
        if ctx.json:
            print(json.dumps({"ok": True, "enabled": running,
                              "message": "已是目标状态"}, ensure_ascii=False))
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
        print(f"客户区 {client.get('width')}×{client.get('height')}"
              f" @屏幕 {tuple(mapping.get('screen_origin') or [])}")
    for item in result.get("checks") or []:
        status = str(item.get("status") or "")
        mark = {"ok": "✅", "warn": "⚠️", "fail": "❌"}.get(status, "•")
        print(f"  {mark} {item.get('label') or ''}  {item.get('detail') or ''}"
              .rstrip())
    print("（截图已写出：见日志）")
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
        print(f"  {datetime.now():%H:%M:%S}  "
              f"{payload.get('width')}×{payload.get('height')}  "
              f"{payload.get('path')}", flush=True)
        return
    print(f"✓ 已抓图：{payload.get('path')}")
    print(f"  {payload.get('width')}×{payload.get('height')}"
          f"   类型 {payload.get('kind')}")
    if payload.get("latest"):
        print(f"  固定路径（每次都覆盖这一个）：{payload.get('latest')}")
    mapping = payload.get("mapping") or {}
    if mapping.get("available"):
        client = mapping.get("client") or {}
        print(f"  客户区 {client.get('width')}×{client.get('height')}"
              f" @屏幕 {tuple(mapping.get('screen_origin') or [])}")
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
    print(f"持续抓图：每 {interval:g}s 覆盖一张，Ctrl+C 退出"
          f"（看图工具打开 tmp_screens\\{body['out']} 即可实时刷新）")
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
            raise ApiFailure("这是高风险命令（会结束炉石进程并清空其日志目录），"
                             "非交互环境下请显式加 --yes")
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
    return _ok_message(ctx, ctx.post("/api/restart_hearthstone",
                                     {"confirm": True}))


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
            print(f"  {key:<38} {_num(delays[key]):>8}"
                  f"   {EFFECT_LABEL.get(DELAY_EFFECT[key], '')}")
    return EXIT_OK


def cmd_delays_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    for pair in args.pairs:
        if "=" not in pair:
            raise ApiFailure(f"要写成 键=值 的形式，收到：{pair}")
        key, _, raw = pair.partition("=")
        key = key.strip()
        if key not in DELAY_KEYS:
            raise ApiFailure(f"未知延时字段：{key}\n可用字段：\n  "
                             + "\n  ".join(DELAY_KEYS))
        try:
            body[key] = float(raw.strip())
        except ValueError:
            raise ApiFailure(f"{key} 必须是数字，收到：{raw.strip()!r}")
    return _ok_message(ctx, ctx.post("/api/delays", body))


def cmd_human_like_show(args, ctx: Ctx) -> int:
    return _print_section(ctx, "human_like", "活人感", (
        ("enabled", "总开关"),
        ("hand_hover_enabled", "看卡牌（随机延时）"),
        ("minion_hover_enabled", "看随从"),
        ("post_delay_min", "操作后延时下限"),
        ("post_delay_max", "操作后延时上限"),
        ("hover_min", "悬停下限"),
        ("hover_max", "悬停上限"),
        ("minion_hover_min", "随从悬停下限"),
        ("minion_hover_max", "随从悬停上限"),
    ))


def cmd_human_like_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    mapping = (
        ("enabled", args.enabled), ("hand_hover_enabled", args.hand_hover),
        ("minion_hover_enabled", args.minion_hover),
        ("post_delay_min", args.post_delay_min),
        ("post_delay_max", args.post_delay_max),
        ("hover_min", args.hover_min), ("hover_max", args.hover_max),
        ("minion_hover_min", args.minion_hover_min),
        ("minion_hover_max", args.minion_hover_max),
    )
    body.update({k: v for k, v in mapping if v is not None})
    if len(body) == 1:
        raise ApiFailure("没有要改的字段（用 --enabled / --post-delay-min 等）")
    return _ok_message(ctx, ctx.post("/api/human_like", body))


def cmd_liveness_show(args, ctx: Ctx) -> int:
    return _print_section(ctx, "liveness", "炉石存活检测", (
        ("enabled", "总开关"),
        ("status", "当前状态"),
        ("process", "进程"),
        ("log_age", "Power.log 停滞(秒)"),
        ("log_stale_warn_seconds", "告警阈值(秒)"),
        ("log_stale_stop_seconds", "自动停止阈值(秒)"),
    ))


def cmd_liveness_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    mapping = (("enabled", args.enabled),
               ("log_stale_warn_seconds", args.warn),
               ("log_stale_stop_seconds", args.stop))
    body.update({k: v for k, v in mapping if v is not None})
    if len(body) == 1:
        raise ApiFailure("没有要改的字段（用 --enabled / --warn / --stop）")
    return _ok_message(ctx, ctx.post("/api/liveness", body))


def cmd_concede_show(args, ctx: Ctx) -> int:
    return _print_section(ctx, "concede", "自动投降", (
        ("enabled", "总开关"),
        ("threshold", "胜率阈值(%)"),
        ("rounds", "连续回合数"),
    ))


def cmd_concede_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    mapping = (("enabled", args.enabled), ("threshold", args.threshold),
               ("rounds", args.rounds))
    body.update({k: v for k, v in mapping if v is not None})
    if len(body) == 1:
        raise ApiFailure("没有要改的字段（用 --enabled / --threshold / --rounds）")
    return _ok_message(ctx, ctx.post("/api/concede", body))


def cmd_rank_stop_show(args, ctx: Ctx) -> int:
    return _print_section(ctx, "rank_stop", "上分停止条件", (
        ("enabled", "总开关"),
        ("mode", "方式（legend/legend_number）"),
        ("legend_number", "目标名次"),
        ("last_number", "最近读到的名次"),
        ("triggered", "已触发"),
    ))


def cmd_rank_stop_set(args, ctx: Ctx) -> int:
    body = {"hot": not args.no_hot}
    mapping = (("enabled", args.enabled), ("mode", args.mode),
               ("legend_number", args.legend_number))
    body.update({k: v for k, v in mapping if v is not None})
    if len(body) == 1:
        raise ApiFailure("没有要改的字段（用 --enabled / --mode / --legend-number）")
    return _ok_message(ctx, ctx.post("/api/rank_stop", body))


def cmd_schedule_show(args, ctx: Ctx) -> int:
    s = ctx.get("/api/status")
    payload = {"schedule_start": s.get("schedule_start"),
               "schedule_end": s.get("schedule_end"), "phase": s.get("phase")}
    if ctx.json:
        print(json.dumps(payload, ensure_ascii=False))
        return EXIT_OK
    if not payload["schedule_start"]:
        print("定时任务：（无）")
    else:
        print(f"定时任务：{payload['schedule_start']} 开始，"
              f"{payload['schedule_end'] or '不设结束（运行至手动停止）'}")
    return EXIT_OK


def _parse_when(text: str) -> datetime:
    """解析时间：HH:MM(:SS) 按「今天，已过则明天」；其余交给 fromisoformat。"""
    text = (text or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if m:
        now = datetime.now()
        cand = now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                           second=int(m.group(3) or 0), microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        return cand
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ApiFailure(f"看不懂的时间：{text!r}"
                         "（用 HH:MM、HH:MM:SS 或 2026-05-30T22:30）")


def cmd_schedule_at(args, ctx: Ctx) -> int:
    if args.in_minutes is not None:
        start = datetime.now() + timedelta(minutes=float(args.in_minutes))
    elif args.start:
        start = _parse_when(args.start)
    else:
        raise ApiFailure("要给一个开始时间：hsctl schedule at 22:30 "
                         "或 hsctl schedule at --in 30")
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
    print(f"✓ 定时任务已设置：{start:%Y-%m-%d %H:%M} 开始"
          f"{f'，{end:%Y-%m-%d %H:%M} 结束' if end else '，不设结束'}")
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
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout
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
    payload = {"running": alive, "port_file": str(PORT_FILE),
               "port_info": info}
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
    """计划任务用哪个解释器：优先 python.exe。

    pythonw.exe 下 sys.stdout 可能是 None，而 web_ui 会用 _TeeStream 接管 stdout，
    拿 None 去 write 会炸；所以这里刻意避开 pythonw。
    """
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
    return ["schtasks", "/create", "/tn", TASK_NAME,
            "/tr", _task_action(),
            "/sc", "ONCE", "/st", "00:00",   # 只手动触发，不靠时间计划
            "/rl", "HIGHEST",                # ← 关键：以最高权限运行
            "/f"]


def _task_create_hint() -> str:
    """给用户复制粘贴用的管理员命令（单层引号，可直接粘贴执行）。"""
    _ensure_task_launcher()
    return (f'schtasks /create /tn "{TASK_NAME}" /tr "{_task_action()}" '
            f"/sc ONCE /st 00:00 /rl HIGHEST /f")


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
        proc = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME, "/xml"],
                              capture_output=True, timeout=30)
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
    fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                   ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    res = fn(None, "runas", exe, params, str(ROOT), 1)      # SW_SHOWNORMAL
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
            candidates.append((info.get("host") or DEFAULT_HOST,
                               int(info["port"])))
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
            with urllib.request.urlopen(base + "/api/status",
                                        timeout=PROBE_TIMEOUT) as resp:
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
        lines = path.read_text(encoding="utf8",
                               errors="replace").splitlines()[-count:]
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
    log_handle.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} "
                     f"hsctl serve start =====\n")
    log_handle.flush()
    try:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                stdout=log_handle, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                creationflags=_detached_flags())
    except Exception as exc:
        raise ApiFailure(f"启动失败：{exc}")
    print(f"已在后台拉起控制台（pid {proc.pid}），等待它监听端口……")
    base = _wait_for_console(ctx, args.wait, proc=proc)
    if not base:
        if proc.poll() is not None:
            raise ApiFailure(f"控制台进程已退出（退出码 {proc.poll()}）。"
                             f"\n日志尾部（{SERVE_LOG}）：\n{_tail(SERVE_LOG)}")
        raise ApiFailure("控制台在限定时间内没有起来。"
                         f"\n日志尾部（{SERVE_LOG}）：\n{_tail(SERVE_LOG)}")
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
        [str(ROOT / "web_ui.py")] + ([] if args.browser else ["--no-browser"]))
    print("正在请求提权：屏幕上会弹出 UAC 同意框，请点「是」。")
    print("  （这一步必须有人的电脑前操作；无人值守请改用："
          "hsctl serve install-task，然后 hsctl serve start --via-task）")
    ok, code = _launch_runas(_python_for_task(), params)
    if not ok:
        if code == 5:
            raise ApiFailure("提权被拒绝（UAC 框上点了「否」或超时取消），"
                             "控制台没有启动。")
        raise ApiFailure(f"提权启动失败（ShellExecute 返回 {code}）。")
    base = _wait_for_console(ctx, args.wait)
    if not base:
        raise ApiFailure("已发起提权，但控制台在限定时间内没有就绪。"
                         "请确认 UAC 框已点「是」。")
    _report_ready(base)
    return EXIT_OK


def _serve_start_via_task(args, ctx: Ctx) -> int:
    """通过「最高权限」计划任务启动：不弹 UAC，可无人值守。"""
    state = _task_state()
    if not state["exists"]:
        raise ApiFailure("计划任务还没注册。请用**管理员** PowerShell 跑一次：\n  "
                         + _task_create_hint()
                         + "\n（或者：hsctl serve install-task）")
    if state["highest"] is False:
        print("⚠️ 这个任务没有勾选「最高权限」，起来后仍然不是管理员。")
        print("   重建：hsctl serve uninstall-task && hsctl serve install-task")
    code, out = _run_quiet(["schtasks", "/run", "/tn", TASK_NAME])
    if code != 0:
        raise ApiFailure(f"触发计划任务失败：{(out or '').strip()[:300]}")
    print(f"已触发计划任务 {TASK_NAME}"
          f"（由任务计划程序以最高权限启动，不弹 UAC）。")
    base = _wait_for_console(ctx, args.wait)
    if not base:
        raise ApiFailure("已触发计划任务，但控制台在限定时间内没有就绪。"
                         "\n  查任务状态：hsctl serve task-status"
                         f"\n  日志尾部（{SERVE_LOG}）：\n{_tail(SERVE_LOG)}")
    _report_ready(base)
    return EXIT_OK


def cmd_serve_install_task(args, ctx: Ctx) -> int:
    state = _task_state()
    if state["exists"] and not args.force:
        print(f"计划任务已存在：{TASK_NAME}"
              f"（最高权限：{'是' if state['highest'] else '否/未知'}）")
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
    print(f"✓ 计划任务已注册：{TASK_NAME}"
          f"（最高权限：{'是' if state['highest'] else '未知'}）")
    print("  之后任何非提权进程（包括 WorkBuddy 这类 agent）都能触发它，"
          "且不会再弹 UAC：")
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
        removed = [p.name for p in (PORT_FILE, PID_FILE)
                   if p.exists() and _unlink(p)]
        if removed:
            print("控制台没在运行（已清理残留的端口文件："
                  + "、".join(removed) + "）。")
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
    if pid is None:
        print("端口文件里没有 pid，无法结束进程；请手动关掉控制台窗口。")
        return EXIT_API
    image = _pid_image_name(pid)
    if image is not None and "python" not in image.lower() and not args.force:
        raise ApiFailure(f"pid {pid} 看起来不是 Python 进程（{image.split(',')[0]}），"
                         "拒绝结束它。确认无误可加 --force。")
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
            "  常见原因：控制台是**提权**运行的，而非提权进程结束不了提权进程。\n"
            "  办法：① 用**管理员** PowerShell 跑 `hsctl serve stop`；"
            "或 ② 先 `hsctl stop` 停掉自动化（这个走 HTTP，不需要提权），"
            "再手动关掉那个控制台窗口。")
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
    parser.add_argument(f"--{name}", dest=dest, action=argparse.BooleanOptionalAction,
                        default=None, help=help_text)


def _add_launch_flags(parser):
    """启动方式：普通（默认）／UAC 提权／计划任务；后两者互斥。

    自动化点击必须提权才生效，而两条提权路的代价不同：
    `--elevate` 会弹 UAC（需要人在电脑前点），`--via-task` 不弹（可无人值守）。
    """
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--elevate", action="store_true",
                       help="请求提权启动（弹 UAC 同意框，需有人在电脑前点）")
    group.add_argument("--via-task", dest="via_task", action="store_true",
                       help="用已注册的「最高权限」计划任务启动（不弹 UAC，"
                            "可手机远程/无人值守；需先 serve install-task）")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # default=SUPPRESS：避免子解析器的默认值覆盖掉根解析器已解析的同名参数
    # （argparse 的已知行为），这样 --json 放在命令前后都有效。
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="输出机器可读 JSON（agent 用）")
    common.add_argument("--host", default=argparse.SUPPRESS,
                        help=f"控制台地址（默认 {DEFAULT_HOST}）")
    common.add_argument("--port", type=int, default=argparse.SUPPRESS,
                        help="控制台端口（默认自动发现）")
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help=f"HTTP 超时秒数（默认 {DEFAULT_TIMEOUT:g}）")

    parser = argparse.ArgumentParser(
        prog="hsctl", parents=[common],
        description="炉石脚本命令行控制台（web_ui.py 的薄客户端）",
        epilog="示例：hsctl status | hsctl log -f | hsctl start | "
               "hsctl delays set pre_action_delay_seconds=5 | "
               "hsctl serve start | hsctl repl",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version",
                        version=f"hsctl {HSCTL_VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    p = sub.add_parser("status", parents=[common], help="看状态（--watch 持续刷新）")
    p.add_argument("--watch", type=float, default=0,
                   help="每 N 秒刷新一行（Ctrl+C 退出）")
    p.set_defaults(handler=cmd_status)

    p = sub.add_parser("log", parents=[common], help="看日志（-f 实时跟随）")
    p.add_argument("-f", "--follow", action="store_true", help="持续跟随新日志")
    p.add_argument("-n", "--lines", type=int, default=0,
                   help="先打印最近 N 行（0 = 缓冲区全部）")
    p.add_argument("--interval", type=float, default=1.0,
                   help="跟随时的轮询间隔秒数（默认 1）")
    p.set_defaults(handler=cmd_log)

    p = sub.add_parser("selfcheck", parents=[common], help="环境自检")
    p.add_argument("--refresh", action="store_true", help="强制重新检测")
    p.set_defaults(handler=cmd_selfcheck)

    p = sub.add_parser("start", parents=[common], help="开始对战")
    p.add_argument("--keep-score", action="store_true",
                   help="保留已有战绩（等同 resume）")
    p.set_defaults(handler=cmd_start)

    p = sub.add_parser("resume", parents=[common],
                       help="恢复自动化（战绩继续累计，不清零）")
    p.set_defaults(handler=cmd_resume)

    p = sub.add_parser("prepare", parents=[common],
                       help="开始运行（准备）：开浮窗 + 切炉石前台，不开打")
    p.set_defaults(handler=cmd_prepare)

    p = sub.add_parser("stop", parents=[common], help="停止自动化")
    p.add_argument("--after-game", action="store_true",
                   help="打完当前这一局再停（默认立即停）")
    p.set_defaults(handler=cmd_stop)

    p = sub.add_parser("calibrate", parents=[common],
                       help="打开屏幕上的校准窗口")
    p.add_argument("--target", default=None, metavar="目标",
                   help="一打开就停在这个目标上：main_menu_battle / deck_grid / "
                        "recommendation / mulligan_confirm / win_rate / "
                        "post_game_start / rank")
    p.set_defaults(handler=cmd_calibrate)

    p = sub.add_parser("overlay", parents=[common], help="开/关日志浮窗")
    p.add_argument("action", choices=("on", "off", "toggle"))
    p.set_defaults(handler=cmd_overlay)

    p = sub.add_parser("regions", parents=[common],
                       help="截屏并画出所有截图区域框（不点击）")
    p.set_defaults(handler=cmd_regions)

    p = sub.add_parser("screenshot", parents=[common],
                       help="按需抓一帧画面（只截图，不点击）")
    p.add_argument("--kind", choices=SCREENSHOT_KINDS, default="client",
                   help="client=炉石客户区(默认) / full=整屏 / "
                        "roi=推荐面板(OCR 看到的) / boxes=整屏+区域框")
    p.add_argument("--max-width", type=int, default=0, metavar="像素",
                   help="超过这个宽度就等比缩小（0=不缩；给 agent 看建议 1280）")
    p.add_argument("--out", default=None, metavar="文件名",
                   help="存成这个文件（相对 tmp_screens\\；不给就按时间戳命名）")
    p.add_argument("--open", action="store_true", help="抓完用系统看图打开")
    p.add_argument("--watch", action="store_true",
                   help="持续抓图，每次覆盖同一张（实时看画面）")
    p.add_argument("--interval", type=float, default=2.0,
                   help="--watch 的间隔秒数（默认 2）")
    p.set_defaults(handler=cmd_screenshot)

    p = sub.add_parser("ui", parents=[common],
                       help="认一次当前界面（主界面/选择套牌），只读")
    p.add_argument("--frame", nargs="?", const="ui_frame.png", default=None,
                   metavar="文件名",
                   help="顺带把这一帧存成 PNG（默认 ui_frame.png）并打印路径")
    p.add_argument("--learn", nargs="?", const="all", default=None,
                   metavar="类型",
                   help="按当前框重裁识别模板：all(默认)/traditional/start"
                        "（皮肤或补丁把字改了之后用）")
    p.set_defaults(handler=cmd_ui)

    p = sub.add_parser("deck", parents=[common],
                       help="自动选择的卡组序号（1-9）")
    p.add_argument("index", nargs="?", default=None, metavar="序号",
                   help="1-9；不给就显示当前设置，给 0 等于关掉")
    p.add_argument("--clear", action="store_true",
                   help="关掉自动选卡组（保持你手选的卡组）")
    p.set_defaults(handler=cmd_deck)

    p = sub.add_parser("enter-battle", parents=[common],
                       help="一键进入对战（拉起炉石→点传统对战→选卡组）")
    p.add_argument("--deck", type=int, default=None, metavar="序号",
                   help="进入后自动选第 N 个卡组（1-9）")
    p.add_argument("--start", action="store_true",
                   help="选完卡组再点「开始」（等于开始排队；默认只到选择套牌界面）")
    p.add_argument("--interval", type=float, default=3.0, metavar="秒",
                   help="等界面时每次识别/点中心的间隔（默认 3 秒）")
    p.add_argument("--wait-timeout", type=float, default=None, metavar="秒",
                   help="等主界面的超时（默认 180 秒）")
    p.add_argument("--no-wait", action="store_true",
                   help="只下发，不在这里等它跑完")
    p.set_defaults(handler=cmd_enter_battle)
    p = sub.add_parser("deck-click", parents=[common],
                       help="快速选卡组：直接点第 N 格（秒级，不走状态机）")
    p.add_argument("index", type=int, metavar="序号",
                   help="第几个卡组（1-9，当前页）")
    p.set_defaults(handler=cmd_deck_click)
    p = sub.add_parser("queue", parents=[common],
                       help="待做命令队列：一次提交多条，控制台按顺序执行")
    p.add_argument("queue_cmd", nargs="?", default="status",
                   choices=["run", "status", "cancel"],
                   help="run=提交并等待；status=看进度；cancel=取消")
    p.add_argument("steps", nargs="*",
                   help="run 时：一条条命令字符串，如 \"stop mode=after_game\" \"wait-stopped\" \"deck 1\" \"start\"")
    p.add_argument("--no-wait", action="store_true",
                   help="run 时只提交，不等队列跑完")
    p.set_defaults(handler=cmd_queue)

    p = sub.add_parser("restart-hearthstone", parents=[common],
                       help="重启炉石（会结束进程并清空其日志目录，高风险）")
    p.add_argument("--yes", action="store_true", help="跳过交互确认")
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
    q.add_argument("--no-hot", action="store_true",
                   help="自动化运行中拒绝修改（与网页行为一致）")
    q.set_defaults(handler=cmd_config_set)

    # ---- delays
    p = sub.add_parser("delays", parents=[common], help="延时参数（支持运行中热改）")
    p.set_defaults(handler=cmd_delays_show)
    dsub = p.add_subparsers(dest="action", metavar="<动作>")
    q = dsub.add_parser("show", parents=[common], help="显示当前延时")
    q.set_defaults(handler=cmd_delays_show)
    q = dsub.add_parser("set", parents=[common], help="改延时：键=值 [键=值 ...]")
    q.add_argument("pairs", nargs="+", metavar="键=值")
    q.add_argument("--no-hot", action="store_true",
                   help="自动化运行中拒绝修改（与网页行为一致）")
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
    q.add_argument("--warn", type=float, default=None,
                   help="Power.log 停滞多少秒告警")
    q.add_argument("--stop", type=float, default=None,
                   help="Power.log 停滞多少秒自动停止")
    q.add_argument("--no-hot", action="store_true", help="运行中拒绝修改")
    q.set_defaults(handler=cmd_liveness_set)

    # ---- concede
    p = sub.add_parser("concede", parents=[common], help="自动投降")
    p.set_defaults(handler=cmd_concede_show)
    csub2 = p.add_subparsers(dest="action", metavar="<动作>")
    q = csub2.add_parser("show", parents=[common], help="显示当前配置")
    q.set_defaults(handler=cmd_concede_show)
    q = csub2.add_parser("set", parents=[common], help="修改配置")
    _add_bool(q, "enabled", "总开关")
    q.add_argument("--threshold", type=float, default=None,
                   help="AI 胜率阈值（百分比 0-100）")
    q.add_argument("--rounds", type=int, default=None, help="连续低于阈值的回合数")
    q.add_argument("--no-hot", action="store_true", help="运行中拒绝修改")
    q.set_defaults(handler=cmd_concede_set)

    # ---- rank-stop
    p = sub.add_parser("rank-stop", parents=[common], help="上分停止条件")
    p.set_defaults(handler=cmd_rank_stop_show)
    rsub = p.add_subparsers(dest="action", metavar="<动作>")
    q = rsub.add_parser("show", parents=[common], help="显示当前配置")
    q.set_defaults(handler=cmd_rank_stop_show)
    q = rsub.add_parser("set", parents=[common], help="修改配置")
    _add_bool(q, "enabled", "总开关")
    q.add_argument("--mode", choices=("legend", "legend_number"), default=None,
                   help="legend=上传说即停；legend_number=上到指定名次")
    q.add_argument("--legend-number", type=int, default=None, help="目标名次")
    q.add_argument("--no-hot", action="store_true", help="运行中拒绝修改")
    q.set_defaults(handler=cmd_rank_stop_set)

    # ---- schedule
    p = sub.add_parser("schedule", parents=[common], help="定时任务")
    p.set_defaults(handler=cmd_schedule_show)
    ssub = p.add_subparsers(dest="action", metavar="<动作>")
    q = ssub.add_parser("show", parents=[common], help="显示当前定时任务")
    q.set_defaults(handler=cmd_schedule_show)
    q = ssub.add_parser("at", parents=[common],
                        help="设置开始时间：22:30 / 2026-05-30T22:30 / --in 30")
    q.add_argument("start", nargs="?", default=None, help="开始时间")
    q.add_argument("--in", dest="in_minutes", type=float, default=None,
                   metavar="分钟", help="N 分钟后开始")
    q.add_argument("--end", default=None, help="结束时间")
    q.add_argument("--duration", type=float, default=None, metavar="分钟",
                   help="持续 N 分钟后结束")
    q.set_defaults(handler=cmd_schedule_at)
    q = ssub.add_parser("cancel", parents=[common], help="取消定时任务")
    q.set_defaults(handler=cmd_schedule_cancel)

    # ---- serve
    p = sub.add_parser("serve", parents=[common],
                       help="托管控制台进程（start/stop/status/restart/"
                            "install-task）")
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
    q.add_argument("--force", action="store_true",
                   help="即使 pid 看起来不是 Python 进程也结束它")
    q.set_defaults(handler=cmd_serve, action="stop")
    q = vsub.add_parser("restart", parents=[common], help="重启控制台")
    q.add_argument("--browser", action="store_true")
    q.add_argument("--wait", type=float, default=60.0)
    q.add_argument("--force", action="store_true")
    _add_launch_flags(q)
    q.set_defaults(handler=cmd_serve, action="restart")
    q = vsub.add_parser("install-task", parents=[common],
                        help="注册「最高权限」计划任务（需管理员跑一次）")
    q.add_argument("--force", action="store_true", help="已存在时覆盖重建")
    q.set_defaults(handler=cmd_serve, action="install-task")
    q = vsub.add_parser("uninstall-task", parents=[common],
                        help="删除该计划任务")
    q.set_defaults(handler=cmd_serve, action="uninstall-task")
    q = vsub.add_parser("task-status", parents=[common],
                        help="看计划任务是否存在、是否勾了最高权限")
    q.set_defaults(handler=cmd_serve, action="task-status")

    p = sub.add_parser("repl", parents=[common], help="进入交互模式")
    p.set_defaults(handler=None)

    return parser


# --------------------------------------------------------------------- REPL
BANNER = f"""\
hsctl {HSCTL_VERSION} —— 炉石脚本命令行控制台
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
        print(json.dumps({"ok": False, "kind": kind, "error": message},
                         ensure_ascii=False))
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


def main(argv=None) -> int:
    _make_output_encoding_safe()
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
