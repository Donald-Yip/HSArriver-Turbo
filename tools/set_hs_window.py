# -*- coding: utf-8 -*-
"""把炉石窗口的**客户区**改成指定尺寸（默认 1920×1080 的一半 = 960×540）。

为什么需要它：项目的坐标基准是炉石客户区 1920×1080，但有时要在别的尺寸下
跑（小屏、测试缩放、验证 `display_mapping` 的等比换算）。游戏内改分辨率要点开
设置、还可能被覆盖；这个脚本直接改窗口尺寸，重启游戏即恢复。

真正的 Win32 逻辑在 `hs_window`（与浮窗滑条共用一份），本脚本只是命令行壳。

用法（用哪个 python 都行：会自动切到本机固定的项目解释器）：
    python tools/set_hs_window.py                      # 只看当前客户区尺寸
    python tools/set_hs_window.py --half               # 1920×1080 的 1/2 = 960×540
    python tools/set_hs_window.py --client 1280x720
    python tools/set_hs_window.py --client 1920x1080   # 项目推荐口径

退出码：0 成功 / 1 没找到炉石或改不动 / 2 参数错 / 4 没有可用的项目解释器。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 项目口径的基准客户区；`--half` 就是对它取一半。
DEFAULT_REFERENCE = (1920, 1080)


def ensure_project_python() -> None:
    """确保跑在带 pywin32 的项目解释器上；需要时按本机 `log/env.json` 用子进程重跑。

    为什么要这一步：本脚本要用 win32gui，而 `python` 默认可能不是项目那个环境
    （见 AGENTS.md「环境要自己配置并保存」）。`log/env.json` 里存着本机固定的
    解释器，照它把**本脚本自己**再跑一遍，任何 python 都能把它跑起来。

    **用 subprocess 而不是 os.execv**：Windows 上 `os.exec*` 更换解释器后 stdout
    容易丢/错乱（实测换到另一个 conda 环境时子进程没输出），子进程转发最稳。
    """
    try:
        import win32gui  # noqa: F401

        return
    except Exception:
        pass
    if os.environ.get("HS_WINDOW_REEXEC"):
        _missing_interpreter_hint()
        raise SystemExit(4)
    try:
        pinned = json.loads((ROOT / "log" / "env.json").read_text("utf8")).get(
            "interpreter"
        )
    except Exception:
        pinned = None
    same = bool(pinned) and os.path.normcase(
        os.path.abspath(pinned)
    ) == os.path.normcase(os.path.abspath(sys.executable))
    if not pinned or not Path(pinned).exists() or same:
        _missing_interpreter_hint()
        raise SystemExit(4)
    env = dict(os.environ)
    env["HS_WINDOW_REEXEC"] = "1"
    print(f"当前解释器没有 pywin32，改用项目解释器重跑：{pinned}", flush=True)
    proc = subprocess.run(
        [pinned, str(Path(__file__).resolve())] + sys.argv[1:], env=env
    )
    raise SystemExit(proc.returncode)


def _missing_interpreter_hint() -> None:
    print(
        "找不到带 pywin32 的项目解释器。先跑 `python hsctl.py --json doctor`，"
        "按它给的 `hsctl env --pin <路径>` 保存本机解释器后再试。"
    )


def parse_client(text: str) -> tuple:
    """把 ``"960x540"``（也接受 ``X`` / ``×`` / ``*`` / 逗号）解析成 (w, h)。"""
    raw = str(text or "").strip().lower()
    for sep in ("x", "×", "*", ",", " ", ":"):
        raw = raw.replace(sep, "x")
    parts = [p for p in raw.split("x") if p]
    if len(parts) != 2:
        raise ValueError(f"尺寸格式不对：{text!r}（示例 960x540）")
    width, height = int(parts[0]), int(parts[1])
    if width <= 0 or height <= 0:
        raise ValueError(f"尺寸必须是正数：{text!r}")
    return width, height


def main() -> int:
    ensure_project_python()
    # hs_window 在确保解释器之后再 import（它依赖 win32gui）。
    import hs_window

    parser = argparse.ArgumentParser(
        description="改炉石窗口客户区尺寸（DPI 感知；默认只读当前尺寸）"
    )
    parser.add_argument(
        "--client",
        metavar="WxH",
        default=None,
        help="目标客户区，例如 960x540；不写则只显示当前尺寸",
    )
    parser.add_argument(
        "--half",
        action="store_true",
        help=f"改成基准 {DEFAULT_REFERENCE[0]}×{DEFAULT_REFERENCE[1]} 的一半",
    )
    parser.add_argument(
        "--reference",
        metavar="WxH",
        default=f"{DEFAULT_REFERENCE[0]}x{DEFAULT_REFERENCE[1]}",
        help="--half 的基准尺寸（默认 1920x1080）",
    )
    parser.add_argument(
        "--move",
        metavar="X,Y",
        default=None,
        help="顺带把窗口外框左上角移到该屏幕坐标",
    )
    args = parser.parse_args()

    current = hs_window.current_client_size()
    if current is None:
        print("没找到炉石窗口（游戏没开、标题不同或窗口不可用）。")
        return 1

    target = None
    try:
        if args.half:
            ref = parse_client(args.reference)
            target = (ref[0] // 2, ref[1] // 2)
        elif args.client:
            target = parse_client(args.client)
    except ValueError as exc:
        print(f"参数错误：{exc}")
        return 2

    mode = _window_mode()
    print(f"炉石窗口 模式={mode}  当前客户区 {current[0]}×{current[1]}")

    if target is None:
        print("（只读：加 --client 960x540 或 --half 才会修改）")
        return 0

    move = None
    if args.move:
        try:
            mx, my = (int(v) for v in args.move.replace("，", ",").split(","))
            move = (mx, my)
        except Exception:
            print(f"参数错误：--move 需要 X,Y，收到 {args.move!r}")
            return 2

    if not hs_window.has_thickframe(hs_window.hearthstone_hwnd()):
        print(
            "[警告] 窗口没有 WS_THICKFRAME（可能是全屏/无边）——仍尝试改，但游戏可能弹回。"
        )

    hwnd = hs_window.hearthstone_hwnd()
    outer = hs_window.outer_size_for(hwnd, target)
    print(f"目标客户区 {target[0]}×{target[1]} → 外框 {outer[0]}×{outer[1]}")
    after = hs_window.set_client_size(target[0], target[1], move=move)
    if after is None:
        print("[失败] 改不动窗口（读不到炉石窗口句柄）。")
        return 1
    print(f"改后客户区 {after[0]}×{after[1]}")
    if tuple(after) != tuple(target):
        print(
            "[失败] 客户区与目标不一致（游戏把尺寸钳住了，如最小 400×400）："
            "改用游戏内「选项 → 图形 → 分辨率」，或对齐到 1920×1080 基准。"
        )
        return 1
    print("完成（重启炉石或在游戏内改画质设置即恢复）。")
    return 0


def _window_mode() -> str:
    try:
        from display_mapping import window_mode

        return window_mode()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
